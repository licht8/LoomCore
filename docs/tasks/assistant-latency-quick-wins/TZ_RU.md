# TZ_RU.md — assistant-latency-quick-wins

## 1. Goal

Reduce end-to-end assistant latency on state/action turns by removing work that
is repeated on every turn and by avoiding the second LLM generation when it adds
no value. The owner-visible symptom: "the AI in my assistant is very slow".

Scope is the **backend hot path only** (no protocol/WebSocket/UI change, no
streaming). Concretely:

- Stop the live JSON-RPC `tools/list` round trip to every plugin on every turn;
  serve the planner catalog from the already-indexed cache (the cache is primed
  at startup and refreshed on plugin enable/disable/reload/restart).
- Add a small `summary_model` bootstrap from the environment so the summary call
  can run on a cheaper/faster model instead of the heavy planner model.
- Skip the summary LLM call entirely for a *direct* (single-step) plan, where
  the raw tool output already is the answer.
- Lower the default LLM `read_timeout` and `max_retries` so a slow/hung provider
  fails fast instead of blocking the turn for minutes.
- Keep thinking/reasoning off (already the default); document it.

## 2. Non-goals

- No response streaming (planner or summary). Tracked separately.
- No change to the WebSocket message contract, payload shapes, or the frontend.
- No change to the plan/approval safety model, planner prompt content, tool
  risk classification, or `agent_plan` schema.
- No new DB tables or migrations.
- No plugin code changes.
- Do not remove the existing `tool_schema_warning` mechanism; only its trigger
  frequency changes (it can now only fire on the cold, un-indexed fallback).

## 3. Approved decisions (locked)

**D1 — Cache the tool catalog; do not re-fetch `tools/list` per turn.**
`PluginManager` keeps the indexed tool metadata already (`ManagedPlugin.tools`,
`_tool_plugins`, `_internal_tools`, `_tool_schemas`, `_tool_risk`). Add an
indexed flag and sync accessors, and a single async dispatcher used by the
router.

- In `PluginManager.__init__` add: `self._tools_indexed: bool = False`.
- At the end of a successful `get_all_tools()` (after the loop, before
  `return all_tools`) set `self._tools_indexed = True`.
- Add:
  ```python
  def tools_indexed(self) -> bool:
      """True once the external tool catalog has been indexed at least once."""
      return self._tools_indexed

  def get_cached_tools(self) -> list[dict]:
      """Return indexed external tool schemas without a JSON-RPC round trip.

      Internal tools and disabled plugins are excluded. Returns [] when the
      catalog has not been indexed yet.
      """
      if not self._tools_indexed:
          return []
      result: list[dict] = []
      for name in self.plugin_names():
          plugin = self._plugins.get(name)
          if plugin is None or not plugin.enabled:
              continue
          for tool in plugin.tools:
              tool_name = tool.get("name")
              if not tool_name:
                  continue
              if self._tool_plugins.get(tool_name) != name:
                  continue
              result.append(tool)
      return result

  async def get_tools_for_routing(self) -> list[dict]:
      """Tool schemas for the LLM catalog: cached when indexed, else live."""
      if self._tools_indexed:
          return self.get_cached_tools()
      return await self.get_all_tools()
  ```

**D2 — `_collect_tools()` uses the cache.**
In `src/llm/router.py` `_collect_tools()` replace the plugin branch with:
```python
if self.plugin_manager:
    try:
        tools_schema.extend(await self.plugin_manager.get_tools_for_routing())
    except Exception as exc:
        logger.warning(f"Failed to fetch plugin tools: {exc}")
        unavailable_plugins.append("all plugins")
```
Everything else in the method is unchanged. `known_tool_names()` /
`tool_schemas()` (which also call `_collect_tools`) then use the cache too.

**D3 — `summary_model` bootstrap from the environment.**
- `src/config.py` `Settings`: add `llm_summary_model: str = ""` next to the
  other `llm_*` bootstrap fields.
- `src/llm/config.py` `default_config()`: add
  `summary_model=settings.llm_summary_model` (empty string is fine; empty means
  "use the main model").
- `src/llm/config.py` `load_config(path)`: when a JSON dict is present, prefer
  the file value **when the key exists**, otherwise fall back to the base
  (env) value:
  ```python
  summary_model = (
      _as_str(data.get("summary_model"))
      if "summary_model" in data
      else base.summary_model
  )
  ```
  This preserves an explicit clear (`"summary_model": ""`) and lets an explicit
  value win, while an old settings file written before this field existed still
  inherits the env seed.
- `.env.example`: document `LLM_SUMMARY_MODEL=` in the LLM block with a one-line
  comment ("Optional smaller/faster model for plan summaries; empty = main
  model.").

**D4 — Skip the summary LLM call for direct plans.**
In `src/interfaces/websocket.py` `_summarize(...)`, after the existing
`raw_requested` and `not observations` early returns, add:
```python
if plan_is_direct(plan):
    return _raw_summary(plan, observations)
```
`plan_is_direct` is already imported/used in this module. Rationale: a direct
plan is a single obvious action whose raw tool output is the answer; a second
full generation only adds latency. The `_counts_mismatch` guard still applies to
the multi-step `plan` path.

**D5 — Lower default timeouts/retries.**
Change the defaults in **both** `src/config.py` `Settings` and `src/llm/config.py`
`LlmConfig`/`default_config()`, and mirror them in `.env.example`:
- `read_timeout`: `90.0` → `30.0`
- `max_retries`: `2` → `1`
Leave `connect_timeout` (15), `write_timeout` (30), `pool_timeout` (15) as-is.
These remain fully overridable by env and by the Settings API.

**D6 — Thinking stays off.**
No code change: `llm_enable_thinking` / `llm_thinking_template` already default to
`False`. Add a one-line startup log warning in the lifespan (or
`check_security_posture`) when `settings.llm_enable_thinking` is true, so a
latency-doubling misconfiguration is visible. Optional but preferred.

## 4. Data model / interfaces

- No DB, no migration, no protocol change.
- New `PluginManager` surface: `tools_indexed() -> bool`,
  `get_cached_tools() -> list[dict]`, `async get_tools_for_routing() -> list[dict]`.
- New `Settings` field `llm_summary_model: str = ""`; new env `LLM_SUMMARY_MODEL`.
- `LlmConfig.summary_model` already exists and is already wired through
  `src/api/llm.py` and the frontend Settings form — no API/UI change needed.

## 5. Parts

1. **`src/plugins/manager.py`** — D1: `_tools_indexed`, `tools_indexed()`,
   `get_cached_tools()`, `get_tools_for_routing()`, set the flag in
   `get_all_tools()`. Keep the docstrings and existing logging.
2. **`src/llm/router.py`** — D2: `_collect_tools()` calls
   `get_tools_for_routing()`.
3. **`src/config.py`** — D3: `llm_summary_model` field; D5: default
   `llm_read_timeout=30.0`, `llm_max_retries=1`.
4. **`src/llm/config.py`** — D3: `default_config()` maps `summary_model`,
   `load_config()` absent-key fallback; D5: `LlmConfig` defaults
   `read_timeout=30.0`, `max_retries=1`.
5. **`src/interfaces/websocket.py`** — D4: direct-plan early return in
   `_summarize`.
6. **`.env.example`** — document `LLM_SUMMARY_MODEL`, update the
   `LLM_READ_TIMEOUT`/`LLM_MAX_RETRIES` values and the per-turn call comment.
7. **`src/app.py`** (optional, D6) — startup warning when thinking is enabled.
8. **Tests** — see §6.
9. **`CHANGELOG.md`** — short dated entry (RULES 14).

## 6. Tests

Fast gate (must pass): `pytest -m "not integration and not e2e"` and
`ruff check .`.

- **`tests/test_plugin_manager.py`**
  - `test_get_all_tools_marks_indexed`: after `get_all_tools()`,
    `manager.tools_indexed()` is `True` and `get_cached_tools()` returns the
    same external tool names.
  - `test_get_cached_tools_excludes_internal_and_disabled`: a plugin with an
    `internal: true` tool and a disabled plugin are excluded from
    `get_cached_tools()`.
  - `test_get_tools_for_routing_uses_cache_when_indexed`: index once, then
    monkeypatch `get_all_tools` to raise; `get_tools_for_routing()` still
    returns the cached tools (proves no live fetch).
  - `test_get_tools_for_routing_fetches_when_not_indexed`: on a fresh manager,
    `get_tools_for_routing()` performs the live fetch and returns tools.
- **`tests/test_router.py`**
  - Update the `_Manager` / `_BrokenManager` fakes to expose
    `get_tools_for_routing` (keep `get_all_tools` behavior; `_BrokenManager`
    raises from `get_tools_for_routing`).
  - `test_route_uses_cached_tools_without_refetch`: a manager whose
    `tools_indexed()` is `True` and `get_cached_tools()` returns a tool; assert
    the tool appears in the catalog and that `get_all_tools` was not awaited.
  - Existing `test_route_warns_when_plugin_tools_unavailable` and
    `test_route_includes_plugin_tools_in_catalog` still pass.
- **`tests/test_llm_config.py`**
  - `test_default_config_summary_model_from_env`: monkeypatch
    `settings.llm_summary_model = "small-model"`; assert
    `default_config().summary_model == "small-model"`.
  - `test_load_config_summary_model_falls_back_when_absent`: a settings file
    without the `summary_model` key inherits `base.summary_model`.
  - `test_load_config_summary_model_explicit_empty_clears`: a file with
    `"summary_model": ""` yields `""` even when the env seed is set.
  - Existing assertions compare to `default_config()` (not literals), so the
    D5 default change must not break them; fix any that do.
- **`tests/test_websocket.py`**
  - `test_summarize_skips_llm_for_direct_plan`: a `direct` plan with a
    non-empty observation returns the raw output and does **not** await
    `summarize_plan` (monkeypatch it to raise if called).
  - Existing `_summarize` tests (raw requested, empty observations, count
    mismatch) still pass.

## 7. Acceptance criteria

- `ruff check .` clean; `pytest -m "not integration and not e2e"` green.
- A state/action turn performs **no** per-turn `tools/list` exchange: with
  `LOOMCORE_TIMING=1`, `stage=get_all_tools` no longer appears on a normal turn
  (only at startup/reload), and `stage=collect_tools` is near-zero.
- For a direct single-step action, `stage=summary` reports `llm=false` and no
  second LLM call is made.
- `LLM_SUMMARY_MODEL` (env) or the Settings `summary_model` field routes the
  summary call to that model; empty keeps the main model.
- Defaults for `read_timeout`/`max_retries` are 30 / 1 in `Settings`,
  `LlmConfig`, `default_config()` and `.env.example`.
- No frontend, protocol, DB, or plugin changes; plan/approval behavior
  unchanged for multi-step plans.

## 8. Commits

Coordinator commits after acceptance (RULES 0a). Suggested messages:
- `perf(plugins): serve the planner tool catalog from the indexed cache`
- `perf(llm): bootstrap summary_model from the environment`
- `perf(agent): skip the summary call for direct plans`
- `perf(llm): lower default read timeout and retries`
- `docs: document LLM_SUMMARY_MODEL and the new timeout defaults`

## 9. Risks

- **Lower `read_timeout` (30s)** may abort genuinely slow local generations.
  It is env/UI-overridable; document in `.env.example`. If a deployment needs
  more, raise `LLM_READ_TIMEOUT`.
- **Skipping the summary for direct plans** changes the assistant's voice on
  one-step actions (raw output instead of a rephrased sentence). Accepted for
  speed; trivially reversible by removing the D4 early return.
- **Cached catalog staleness**: mitigated because `_rebuild_tool_cache` runs on
  enable/disable/reload/restart and `get_all_tools`. A plugin that changes its
  tools without a reload will not be seen until reload — same as any lifecycle
  change.
- **`load_config` fallback** changes behavior for pre-existing settings files
  that omit `summary_model` (they now inherit the env seed). Explicit `""`
  still clears it. Update any test that assumed absent → `""`.
- **Test fakes** in `test_router.py` must implement `get_tools_for_routing`, or
  the two catalog tests break.
