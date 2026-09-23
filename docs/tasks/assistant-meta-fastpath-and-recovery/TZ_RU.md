# TZ_RU.md — assistant-meta-fastpath-and-recovery

## 1. Goal

Fix two classes of defects visible in a real session, and cut the latency they
cause:

1. **Meta queries are slow and over-answer.** "show me tools for LoomOps" ran a
   full planner LLM turn, executed `core_list_tools` (which returns **all**
   tools because it has no filter), echoed everything, and only produced the
   right answer after the user repeated "no. tools for LoomOps".
2. **The planner fails on simple requests and wastes a whole re-ask turn.**
   - "покажи инструменты для LoomCore" -> *"Sorry, I didn't quite catch that —
     could you rephrase your request?"* (a bare/empty planner completion).
   - A planner that puts `reply` inside an `agent_plan` step -> *"I could not
     form a valid plan: unknown tool: reply"*.

The fix: answer high-confidence meta queries **deterministically without an LLM
call**, give `core_list_tools` a plugin filter, recover the two planner failure
shapes instead of erroring/re-asking, and add a configurable `planner_model` so
the planner can run on a faster model.

## 2. Non-goals

- No change to the plan/approval safety model, `tool_choice="required"`, the
  `agent_plan`/`reply` schemas, or `PLANNER_REASK_LIMIT`.
- No new DB tables or migrations; no plugin changes.
- No change to the REST `POST /api/chat` contract (it benefits from the same
  `route()` changes but keeps its shape).
- The meta fast-path is **not** a general intent classifier: it must only fire
  on short, explicit tool/plugin-listing requests (see D2). Everything else
  falls through to the LLM.
- No change to the streaming/`delta` machinery from the previous task.

## 3. Approved decisions (locked)

**D1 — `core_list_tools` gains an optional `plugin` filter (and uses the cache).**
- `src/core/core_tools.py` `CORE_TOOLS_SCHEMA`: give `core_list_tools` an
  optional string property `plugin` (not required), documented as
  "Optional plugin id (e.g. LoomOps) or `core`; omit to list everything."
- `_core_list_tools(arguments)`: read `plugin = _plugin_name(arguments) or ""`.
  - **Core alias**: if `plugin.lower()` in `{"core", "loomcore", "loom_core"}`,
    return only the core-tools line.
  - **Named plugin**: `_require_plugin(plugin)` (existing helper; raises a clear
    error for an unknown id), then list **only that plugin's** tools.
  - **No filter**: keep today's all-tools behavior.
- When listing a plugin (filtered or not), prefer the **indexed cache** over a
  live round trip: for each tool name in `plugin_manager.get_cached_tools()`
  whose owner `plugin_manager.plugin_for_tool(name) == plugin`. Fall back to the
  current `await plugin_manager.get_tools(name)` loop only when
  `not plugin_manager.tools_indexed()`. Keep the `"{name} ({n}): ..."` line
  format and the `core_tools` / `plugin_tools` result fields.

**D2 — Deterministic meta fast-path in `AIRouter.route` (no LLM call).**
- Add module-level constants in `src/llm/router.py`:
  ```python
  _META_MAX_CHARS = 120
  _META_TOOLS_RE = re.compile(
      r"(?:list|show|see|which|what)\b[^?]*\btools?\b"
      r"|\btools?\s+(?:for|of)\b"
      r"|(?:какие|покажи|список)\s+инструмент",
      re.IGNORECASE,
  )
  _META_PLUGINS_RE = re.compile(
      r"(?:list|show|which|what)\b[^?]*\bplugins?\b"
      r"|(?:какие|покажи|список)\s+плагин",
      re.IGNORECASE,
  )
  _META_PLUGIN_TOKEN_RE = re.compile(r"\b(LoomOps|LoomWG|LoomCore)\b", re.IGNORECASE)
  _META_PLUGIN_AFTER_RE = re.compile(r"(?:for|of|для)\s+([A-Za-z_][\w-]*)", re.IGNORECASE)
  ```
- Add `async def _meta_fast_path(self, user_message: str) -> str | None`:
  - Return `None` unless `len(user_message) <= _META_MAX_CHARS`.
  - Determine intent: tools if `_META_TOOLS_RE.search`, plugins if
    `_META_PLUGINS_RE.search`; otherwise `None`. (Tools wins if both.)
  - Extract a plugin token: `_META_PLUGIN_TOKEN_RE` first, else
    `_META_PLUGIN_AFTER_RE` (group 1). Normalize `LoomCore` -> `core`.
  - Call the core tool via `await dispatch_core_tool("core_list_tools",
    {"plugin": token} if token else {})` (tools) or `"core_list_plugins"` with
    `{}` (plugins), then return the tool's text via a small local helper
    `_tool_text(result)` that reads `result["content"][0]["text"]` (guarding
    missing/short shapes -> `None`).
  - On any exception, return `None` (fall through to the LLM).
- Wire it in `route`, **after** the not-configured check and `_begin_turn`
  (so the user message is persisted), **before** `_collect_tools`/the planner:
  ```python
  fast = await self._meta_fast_path(user_message)
  if fast:
      await self._persist_assistant(session_id, fast)
      return {"type": "text", "content": fast}
  ```
  (Use whatever the existing `_persist_assistant`/persist helper is named; keep
  behavior identical to the not-configured path.)
- Keep it conservative: no fast-path for "what can you do"/"что ты умеешь"
  (leave those to the LLM), only explicit tool/plugin listing.

**D3 — A `reply` step inside `agent_plan` becomes a text reply.**
- In `route`, in the `tool_name == AGENT_PLAN_TOOL` branch, **before**
  `validate_plan`: if the plan has exactly one step and
  `step.get("tool") == REPLY_TOOL`, take
  `text = step.get("args", {}).get("text")` (fallback: `plan.get("goal")` or the
  step's `why`). If `text` strips to a non-empty, non-bare string, persist and
  return `{"type": "text", "content": text}`. Otherwise fall through to the
  normal validation/re-ask.
- This removes the "unknown tool: reply" failure entirely.

**D4 — A bare known tool name becomes a direct one-step plan.**
- In `route`, in the branches that currently set `bare_answer = True` (the
  `reply` branch when the text is bare, and the `not raw_tool_calls` branch when
  `_is_bare_tool_name(content, known_tools)` is true): before falling back to
  `_CONFUSED_REPLY`, check whether the stripped text is a **known catalog tool
  name**.
  - If it is, synthesize a direct plan and validate it:
    ```python
    raw = {
        "goal": user_message,
        "mode": "direct",
        "steps": [{"tool": name, "args": {}, "why": "The user asked for this tool."}],
    }
    plan = build_plan(raw, risk_of=resolve_tool_risk)
    errors = validate_plan(plan, known_tools, tool_schemas)
    ```
  - If `not errors`, treat it as a normal plan result (persist only when not
    direct; return `{"type": "plan", "plan": plan}`). A safe single-step read
    then runs; a mutating one asks for approval.
  - If there are errors (e.g. a required argument is missing, such as
    `core_plugin_status` needing `plugin`), do **not** emit `_CONFUSED_REPLY`:
    set a specific re-ask nudge — `"Call agent_plan with the required arguments
    for '{name}'."` — and continue the re-ask loop.
- `_CONFUSED_REPLY` remains only for genuinely unparseable completions.

**D5 — Configurable `planner_model` (mirror `summary_model`).**
- `src/config.py` `Settings`: add `llm_planner_model: str = ""`.
- `src/llm/config.py`: add `planner_model: str = ""` to `LlmConfig`;
  `default_config()` sets `planner_model=settings.llm_planner_model`; in
  `load_config()`, use the file value when `"planner_model" in data`, else
  `base.planner_model` (same pattern as `summary_model`).
- `src/api/llm.py`: add `planner_model` to `LlmSettingsUpdate`, `_serialize`,
  and `_merge` (null/omitted keeps current, `""` clears).
- `frontend/lib/llm-settings-api.ts` + `frontend/components/settings-view.tsx`:
  add the `planner_model` field next to `summary_model`.
- `.env.example`: document `LLM_PLANNER_MODEL=` ("Optional faster model for the
  planner; empty = main model."). `docker-compose.yml`: add
  `LLM_PLANNER_MODEL: ${LLM_PLANNER_MODEL:-}`.
- `src/llm/router.py` `route`: pass `model=config.planner_model or None` to the
  planner `stream_chat_completion`/`chat_completion` call.

**D6 — Stream the plan goal for action-turn feedback.**
- Reuse the D4 machinery from the previous task: add a `_GoalTextStream` in
  `src/llm/router.py` (same shape as `_ReplyTextStream`, extracting the `goal`
  field) and extend `_make_reply_streamer` so that when the streamed tool is
  `agent_plan` it forwards the growing `goal` as deltas (when the tool is
  `reply` it forwards `text` as today). Only one of the two is active per
  stream.
- Effect: while the planner generates a plan, the assistant bubble shows the
  goal text; the final `response` (summary) reconciles the bubble as today, and
  the plan card still renders on `plan_proposed`.
- Keep the per-attempt reset behavior (D4 of the previous task) unchanged.

## 4. Data model / interfaces

- No DB/migration change.
- New `Settings.llm_planner_model` / `LlmConfig.planner_model` / env
  `LLM_PLANNER_MODEL`; exposed via the Settings API and form.
- `core_list_tools` schema gains an optional `plugin` argument; its result shape
  is unchanged.
- New private helpers in `router.py`: `_meta_fast_path`, `_tool_text`,
  `_GoalTextStream`; new module regexes/constants.
- No change to WS event shapes (D6 reuses `delta`).

## 5. Parts

1. **`src/core/core_tools.py`** — D1 (schema `plugin` arg + filtered/cached
   `_core_list_tools`).
2. **`src/llm/router.py`** — D2 (meta fast-path + helpers + wiring), D3 (`reply`
   step recovery), D4 (bare known tool -> direct plan), D5 (pass
   `config.planner_model`), D6 (`_GoalTextStream` + streamer extension).
3. **`src/config.py`, `src/llm/config.py`, `src/api/llm.py`** — D5 config
   plumbing.
4. **`frontend/lib/llm-settings-api.ts`, `frontend/components/settings-view.tsx`**
   — D5 field.
5. **`.env.example`, `docker-compose.yml`** — D5 documentation/defaults.
6. **Tests** — §6.
7. **`CHANGELOG.md`** — short dated entry (RULES 14).

## 6. Tests

Fast gate: `uv run ruff check .` and
`uv run pytest -m "not integration and not e2e"`. Frontend: `npx tsc --noEmit`
and `npm run build` in `frontend/`.

- **`tests/test_core_tools.py`**
  - `test_list_tools_filtered_by_plugin`: `_core_list_tools({"plugin": "LoomOps"})`
    returns only LoomOps tool names (no core line, no other plugin).
  - `test_list_tools_core_alias`: `{"plugin": "core"}` (and `"LoomCore"`)
    returns only the core tools.
  - `test_list_tools_unknown_plugin_errors`: an unregistered id raises/returns an
    error, not a full listing.
  - `test_list_tools_unfiltered_still_lists_all`: unchanged behavior without
    `plugin`.
- **`tests/test_router.py`**
  - `test_meta_fast_path_tools_en`: "show me tools for LoomOps" returns a text
    result listing only LoomOps tools and makes **no** LLM call (assert the fake
    `chat_completion`/`stream_chat_completion` was not awaited).
  - `test_meta_fast_path_tools_ru`: "покажи инструменты для LoomCore" returns the
    core tools, no LLM call.
  - `test_meta_fast_path_skips_what_can_you_do`: "что ты умеешь" is **not**
    fast-pathed (LLM is called).
  - `test_reply_step_becomes_text`: an `agent_plan` with one `reply` step yields
    a text result, not "unknown tool: reply".
  - `test_bare_known_tool_becomes_direct_plan`: a bare `core_list_tools`
    completion yields a direct plan for that tool (and does not return
    `_CONFUSED_REPLY`).
  - `test_bare_known_tool_missing_args_reasks`: a bare tool that needs args
    triggers a re-ask with the specific nudge (not `_CONFUSED_REPLY`).
  - `test_route_passes_configured_planner_model`: with
    `planner_model="fast-model"` in the patched config, the planner call
    receives `model="fast-model"`.
  - `test_goal_stream_forwards_agent_plan_goal`: a streamed `agent_plan` forwards
    its `goal` via `on_delta`.
- **`tests/test_llm_config.py`**
  - `test_planner_model_from_env`, `test_load_config_planner_model_falls_back_when_absent`,
    `test_load_config_planner_model_explicit_empty_clears`.
- **`tests/test_api_llm.py`**
  - `planner_model` round-trips through `_serialize` and is preserved when
    omitted; an explicit `""` clears it.
- **`tests/test_websocket.py`** (only if D6 touches the WS wiring; the delta path
  is unchanged, so no new case is strictly required)

## 7. Acceptance criteria

- `uv run ruff check .` clean; `uv run pytest -m "not integration and not e2e"`
  green; frontend `npx tsc --noEmit` clean and `npm run build` succeeds.
- "show me tools for LoomOps" / "покажи инструменты для LoomCore" are answered
  **without any LLM call** (with `LOOMCORE_TIMING=1`, no `stage=llm_call` for
  those turns) and list only the requested set.
- The planner can no longer produce "I could not form a valid plan: unknown tool:
  reply"; a `reply` step is answered as text.
- A bare known tool name produces a valid direct plan (or a specific re-ask),
  never the "Sorry, I didn't quite catch that" fallback.
- `planner_model` set via env, settings file, or the Settings tab routes the
  planner call to that model; empty keeps the main model.
- Action turns show the plan goal incrementally (D6) with no duplicate bubble.
- No change to plan/approval behavior, tool cards, or the REST contract.

## 8. Commits

Coordinator commits after acceptance (RULES 0a). Suggested messages:
- `feat(core): filter core_list_tools by plugin and serve it from the cache`
- `feat(agent): answer tool/plugin listing requests without an LLM call`
- `fix(agent): recover reply steps and bare tool names in planner output`
- `feat(llm): add a configurable planner model`
- `feat(agent): stream the plan goal for action-turn feedback`

## 9. Risks

- **Meta fast-path false positives**: bounded by `_META_MAX_CHARS` and explicit
  listing regexes; on any doubt (or exception) it returns `None` and the LLM
  handles the turn. A false positive degrades to a deterministic list, not an
  error.
- **`core_list_tools` cache staleness**: the cache is refreshed on
  plugin enable/disable/reload/restart; the unfiltered fallback still works when
  not indexed.
- **D4 synthesizes args `{}`**: a tool needing required args will fail
  validation and re-ask with a specific nudge — correct, but adds one planner
  call for those cases. Acceptable; they are rare.
- **`planner_model` on a weak model**: a small planner may violate the schema
  more often; D3/D4 absorb the common cases, but the owner should pick a model
  that follows tool calls.
- **D6 goal streaming**: the goal text is replaced by the final summary on
  `response` (same reconciliation as `reply`); if the plan is direct and runs
  without a summary, the goal may briefly show before the raw result. Verify the
  bubble does not duplicate on re-ask (the per-attempt reset still applies).
- **Layering**: `_meta_fast_path` calls `dispatch_core_tool` from the LLM
  router; this is the same core-tool entry the WS uses and keeps the router free
  of interface imports. Do not import `src/interfaces/*` into `src/llm/*`.
