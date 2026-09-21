# TZ_RU.md — plugin tools 5xx + lifecycle serialization

> Spec for the executor (RULES 16). Content is English (RULES 0).

## 1. Goal

Fix two defects around the plugin surface:

1. `GET /api/tools` (`src/api/plugins.py::list_plugins_tools`) wraps
   `plugin_manager.get_all_tools()` in a broad `except Exception` and returns
   **HTTP 200** with `{"error": str(exc)}`. That leaks internals (paths,
   hostnames, DB/process errors), hides the failure from monitoring (nothing is
   logged), and silently changes the response shape (no `tools` key), breaking
   consumers. It must log and return a 5xx.
2. Per-plugin lifecycle mutations are not mutually exclusive. `enable_plugin`,
   `disable_plugin` and `reload_plugin` all mutate shared state
   (`plugin.enabled`, `plugin.proc`, the tool caches) and `await` inside the
   operation (initialize / tools/list), but none of them holds the plugin's
   lock across the sequence. Two concurrent admin requests can interleave:
   e.g. a `reload` can `_kill_and_stop` a process that an in-flight tool call
   (or another lifecycle op) is currently reading from.

## 2. Non-goals

- No API contract change beyond the `/api/tools` failure path (success shape
  stays `{"tools": [...]}`).
- Do **not** add a new lock type. A per-plugin reentrant async lock already
  exists (`_PluginLock`, `src/plugins/manager.py:79`, field
  `ManagedPlugin.lock` at `:120`) and is used by `_exchange` (`:715`); reuse it.
- Do not wrap `_restart_plugin` / `restart_plugin`. Its exponential backoff
  sleeps up to 30s and must not hold the lock. The background-healthcheck vs.
  lifecycle race is explicitly left as a follow-up (see Risks).
- No changes to plugin subprocesses, manifests, auth, rate limits, or DB.

## 3. Approved decisions (locked)

- Finding 1: log with `logger.exception(...)` and raise
  `HTTPException(status_code=500, detail="Failed to collect tool schemas")`.
  The raw exception text must never reach the response body.
- Finding 2: reuse the existing `_PluginLock` and hold it for the whole
  mutation body of `enable_plugin`, `disable_plugin`, `reload_plugin`, with the
  enabled-state re-checks moved **inside** the lock (avoid TOCTOU on the state
  the caller waited on). `_PluginLock` is reentrant per task, so the nested
  `_initialize_plugin`/`get_tools` → `_exchange` → `plugin.lock` acquisitions
  in the same task keep working (regression-covered by
  `test_exchange_lock_is_reentrant_for_restart`).
- Correction to the report: the claim "no `Lock`/`asyncio.Lock` in
  `src/plugins/manager.py`" is false; the lock exists. The defect is only that
  lifecycle mutations don't hold it.

## 4. Data model / interfaces

- `/api/tools`: 200 `{"tools": [<schema>, ...]}` (unchanged); failure now
  500 `{"detail": "Failed to collect tool schemas"}` (was 200
  `{"error": "<internal text>"}`).
- `PluginManager` public method signatures unchanged; only their locking and
  internal ordering change.
- No schema/migration change.

## 5. Parts

1. `src/api/plugins.py`
   - Add `import logging` and
     `logger = logging.getLogger("loomcore.api.plugins")`.
   - Rewrite `list_plugins_tools` to:
     ```python
     try:
         tools = list(CORE_TOOLS_SCHEMA) + await plugin_manager.get_all_tools()
     except Exception as exc:
         logger.exception("Failed to collect plugin tool schemas")
         raise HTTPException(
             status_code=500, detail="Failed to collect tool schemas"
         ) from exc
     return {"tools": tools}
     ```
     (drop the mutable `result: dict` shape-switch). Keep the existing auth
     check and `@limiter.limit("60 per 60 seconds")`.
2. `src/plugins/manager.py`
   - `disable_plugin`: keep the `plugin is None` guard before locking; wrap the
     rest in `async with plugin.lock:` and move the `if not plugin.enabled:
     return True` re-check inside.
   - `enable_plugin`: same — guard before, then
     `async with plugin.lock:` around the allowlist check, state flips, spawn,
     initialize, tools fetch, cache rebuild and persist; the `except` revert
     stays inside the lock.
   - `reload_plugin`: same — guard before, then `async with plugin.lock:` around
     the enabled re-check, kill, spawn, initialize, tools fetch, cache rebuild.
3. `tests/test_api_plugins.py`
   - Replace `test_tools_endpoint_reports_error` with
     `test_tools_endpoint_returns_500_on_failure` (see Tests).
4. `tests/test_plugin_manager.py`
   - Add `test_lifecycle_mutations_are_serialized` (see Tests).
5. `CHANGELOG.md` — one dated bullet (coordinator, RULES 14).

## 6. Tests

- `tests/test_api_plugins.py::test_tools_endpoint_returns_500_on_failure(monkeypatch, caplog)`:
  patch `plugins_api.plugin_manager.get_all_tools` to an async fn that raises
  `RuntimeError("plugin down")`; with `caplog.at_level(logging.ERROR,
  logger="loomcore.api.plugins")`, GET `/api/tools` (admin auth) asserts:
  status `500`; `response.json() == {"detail": "Failed to collect tool schemas"}`;
  `"plugin down" not in response.text`; `"Failed to collect plugin tool
  schemas" in caplog.text`.
- `tests/test_plugin_manager.py::test_lifecycle_mutations_are_serialized`:
  build a disabled/STOPPED `demo` via `_add_plugin(manager,
  state=PluginState.STOPPED, enabled=False)`; monkeypatch `_do_register_inplace`
  (noop), `_initialize_plugin` (signals `init_started`, then awaits
  `release_init`), `get_tools` (returns `[]`), `_rebuild_tool_cache` (noop) and
  `_kill_and_stop` (records into `killed`). Then:
  `enable = asyncio.create_task(manager.enable_plugin("demo")); await
  init_started.wait(); disable = asyncio.create_task(manager.disable_plugin("demo"));
  await asyncio.sleep(0); assert killed == []` (disable is blocked behind the
  lock); `release_init.set(); assert await asyncio.gather(enable, disable) ==
  [True, True]; assert killed == ["demo"]; assert plugin.enabled is False`.
  This fails without the lock (disable kills during initialize).
- Existing tests (`test_enable_plugin_spawns_and_registers_tools`,
  `test_disable_plugin_stops_drops_tools_and_persists`,
  `test_reload_plugin_refreshes_enabled`,
  `test_exchange_lock_is_reentrant_for_restart`) must remain green.

Gates:

```
uv run ruff check .
uv run pytest tests/test_api_plugins.py tests/test_plugin_manager.py -q
uv run pytest -m "not integration and not e2e" -q
```

## 7. Acceptance criteria

- `/api/tools` returns 500 + `{"detail": "Failed to collect tool schemas"}` and
  logs the traceback; the exception text is absent from the body; the success
  path still returns `{"tools": [...]}`.
- Concurrent `enable` then `disable` on the same plugin are serialized: disable
  does not run `_kill_and_stop` until enable's initialize/tools sequence
  finishes, and no task observes a half-mutated plugin.
- `ruff check .` clean and the full fast suite green.

## 8. Commits

1. `fix(api): log and 5xx on plugin tool collection failure`
2. `fix(plugins): serialize per-plugin lifecycle mutations with the plugin lock`

(Coordinator commits only after acceptance — RULES 0a.)

## 9. Risks

- The lock is held for the whole enable/reload sequence, so a concurrent tool
  call or lifecycle request on the **same** plugin now waits. Bounded by the
  existing timeouts (initialize 10s, tools/list 30s); a different plugin is
  unaffected (per-plugin lock).
- `_restart_plugin` (auto-restart / background healthcheck) still does not take
  the lock, so a healthcheck restart can race a concurrent disable. Out of
  scope here; tracked as a follow-up.
- `_PluginLock` reentrancy is keyed on `asyncio.current_task()`; all call
  paths here run within the same task, so nested acquisitions are safe.
