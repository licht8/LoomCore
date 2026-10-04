# TZ_RU.md — Agent dispatch approval, REST pending, pending authority + memory gating

> Spec for the executor (RULES 16). Content is English (RULES 0). Do not commit
> (RULES 0a). One spec, **two commits** — see §8.

## 1. Goal

Fix five verified defects found in the coordinator audit. Group them into two
logical commits:

- **Commit 1 — dispatch safety / REST plan handling** (Parts A–B):
  - (A) The plugin dispatch boundary does not re-check a tool's risk: for plugin
    tools, `approved` is never threaded, so the whole safety invariant rests on
    `execute_plan` alone. Any future caller of `_execute_tool` /
    `execute_tool_by_name` that forgets the flag can run a dangerous plugin tool
    with no gate.
  - (B) REST `POST /api/chat` returns a planner-returned plan but never executes
    it and never persists it as a pending plan, so an API client gets a "plan"
    that nothing runs and that no approval can bind to.
- **Commit 2 — pending-plan authority + long-conversation memory** (Parts C–E):
  - (C) `GET /api/approvals` reads `role="plan"` rows with a stored
    `status="proposed"` instead of the authoritative `sessions.pending` slot, so
    it lists superseded plans that can no longer be approved (a phantom entry).
  - (D) Long-conversation compaction is gated on the already-filtered 50-row
    prompt history, so in a tool-heavy session (the agent's sessions) the summed
    size stays under the threshold and compaction never fires even when the DB
    holds foldable user/assistant text.
  - (E) The per-session "at most one compaction at a time" guard lives on the
    `AIRouter` instance, but REST and WebSocket each construct their own
    `AIRouter`, so the same session can be compacted concurrently (duplicate
    model calls, stale-baseline overwrite).

## 2. Non-goals

- Do NOT touch any other audit finding (`clients.json` traversal, manifest
  `entry`/`internal_tools`/`tools_prefix`, `_exchange` write/restart timeout,
  audit `/verify` snapshot, SSRF, TOTP replay, CORS, WS handshake limit, REST raw
  LLM error text, frontend responsive/UX, docs hygiene, etc.). Out of scope.
- Do NOT execute plans over REST: REST converses/plans only (see §3.2). No REST
  execution path is added.
- Do NOT change the plan executor (`src/core/agent.py::execute_plan`) or the
  safety invariant (a non-safe step runs only when the plan status is
  `approved`). This TZ adds a *second* check at the dispatch boundary.
- No new plugins, no DB schema/model/migration changes.
- No frontend changes.
- `MEMORY_HISTORY_LIMIT` (200 rows) stays as-is; text older than 200 messages is
  not folded (accepted limitation, not fixed here).

## 3. Approved decisions (locked)

1. **`PluginManager.execute_tool_by_name` re-checks risk (defense in depth).**
   New keyword parameter `approved: bool = False`. After argument validation,
   resolve `risk = classify_tool_risk(tool_name, self.tool_risk(tool_name))`; if
   `risk != "safe"` and `not approved`, raise `PermissionError` **before**
   dispatch. Argument validation must still run first, so a malformed argument
   keeps raising `ToolContractError` (existing tests). Internal tools call
   `execute_tool` directly (not `_by_name`) and are unaffected.
2. **REST `/api/chat` never executes.** When `route()` returns a plan, the
   endpoint persists it as the session's pending plan with `set_pending(...)`
   (best-effort) and returns the route result unchanged. Safe, risky and direct
   plans all go through the pending flow and are approved over the WebSocket
   (whose `approve` path already handles direct plans). This makes the plan a
   first-class, approvable pending — no phantom — without a REST executor.
3. **`list_pending_plans` reads `sessions.pending`.** The single pending slot per
   session is the authority (same record the WS reads, claims and replaces). A
   superseded plan is no longer listed; no `role="plan"` row is deleted or
   migrated.
4. **The compaction threshold lives in `refresh_memory`, not the scheduler.**
   `_schedule_memory_refresh` only dedups (via the shared guard below) and
   schedules `refresh_memory`; `refresh_memory` computes the foldable
   user/assistant char total from the same 200-row read it already performs and
   returns the current memory unchanged when it is `<= MEMORY_COMPACT_AFTER_CHARS`
   (12000). This removes the divergence between the scheduler's filtered view and
   the folder's view.
5. **The memory in-flight guard is process-wide.** Move `_memory_inflight` and
   `_memory_tasks` to module-level sets in `src/llm/router.py` so any number of
   `AIRouter` instances dedupe per session. `aclose()` cancels the shared tasks
   and clears the shared in-flight set.

## 4. Data model / interfaces

- `execute_tool_by_name(tool_name, arguments=None, *, approved=False) -> dict`
  (new keyword-only parameter; existing two-arg callers keep working).
- No REST response shape change: `/api/chat` still returns the `route()` dict.
- No DB change; `sessions.pending` JSONB shape is unchanged.

## 5. Parts

### Part A — Re-check tool risk at the dispatch boundary (Commit 1)

Files: `src/plugins/manager.py`, `src/interfaces/websocket.py`,
`tests/test_plugin_manager.py`, `tests/test_LoomOps_tools.py`,
`tests/test_websocket.py`.

- `src/plugins/manager.py`:
  - Add `from src.core.security import classify_tool_risk` (verify no import
    cycle: `src.core.security` imports `src.auth`, `src.config`,
    `src.core.client_ip`, `src.core.security_log` only).
  - `execute_tool_by_name` (`:1255`): add `*, approved: bool = False`; keep
    `validate_tool_args(...)` first; then:
    ```python
    risk = classify_tool_risk(tool_name, self.tool_risk(tool_name))
    if risk != "safe" and not approved:
        raise PermissionError(f"Tool '{tool_name}' requires approval")
    ```
    Update the docstring to say it enforces both the argument contract and the
    approval gate for non-safe tools.
- `src/interfaces/websocket.py::_execute_tool` (`:156-163`): pass the flag to the
  plugin branch — `return await plugin_manager.execute_tool_by_name(tool_name, arguments, approved=approved)`.
  Core tools keep going through `dispatch_core_tool(..., approved=approved)`.
- Tests:
  - `tests/test_plugin_manager.py`: add a case that a tool with
    `_tool_risk[name] = "dangerous"` raises `PermissionError` for
    `execute_tool_by_name(name, {})` and dispatches when `approved=True`; add a
    case that `ops_run_command` (core `_EXPLICIT_DANGEROUS`) still raises even
    when the plugin declares `"safe"`. Keep `test_execute_tool_by_name_routes`
    and `test_execute_tool_by_name_rejects_invalid_args` green (`wg_x` is safe;
    argument validation still runs first).
  - `tests/test_LoomOps_tools.py`: `test_run_command_contract_accepts_argv_list`
    (`:2103`) and `test_run_command_contract_accepts_string` (`:2121`) call
    `execute_tool_by_name("ops_run_command", ...)`; add `approved=True`.
    `test_run_command_contract_rejects_non_string_command` (`:2136`) keeps
    expecting `ToolContractError` and needs no change (validation runs first).
  - `tests/test_websocket.py`: `test_execute_tool_routes_plugin_names_to_manager`
    (`:257`) — update `fake_execute` to accept `*, approved=False` and assert the
    forwarded `approved` value (add an `approved=True` assertion).

### Part B — REST `/api/chat` registers a returned plan as pending (Commit 1)

Files: `src/api/chat.py`, `tests/test_chat_api.py`, `docs/architecture/AGENT.md`.

- `src/api/chat.py`:
  - Imports: `from src.db.database import async_session_factory` and
    `from src.db.session import set_pending`.
  - In `chat_endpoint`, after the `try/except` that calls `airouter.route`:
    ```python
    if isinstance(result, dict) and result.get("type") == "plan" and result.get("plan"):
        # REST never executes: register the plan as the session's pending so it
        # is approvable over the WebSocket and visible in GET /api/approvals.
        try:
            async with async_session_factory() as db:
                await set_pending(db, payload.session_id, result["plan"], payload.message)
        except Exception:
            logger.exception("Failed to persist pending plan (session=%s)", payload.session_id)
    return result
    ```
  - Do not execute, summarize, or mutate the plan.
- `docs/architecture/AGENT.md`: in "Modes" or a short note, state that REST
  `/api/chat` is conversation/planning only — a returned plan is stored as the
  session's pending and must be approved over the WebSocket.
- Tests (`tests/test_chat_api.py`):
  - A test where the fake `route` returns
    `{"type": "plan", "plan": {"plan_id": "pl_1", "steps": []}}`; monkeypatch
    `chat_api.set_pending` to capture calls and `chat_api.async_session_factory`
    with a dummy async context manager (follow the `_DummyContext` pattern in
    `tests/test_approvals_api.py`); assert the response equals the plan dict and
    `set_pending` was called with `("s1", plan, "hi")`.
  - A test where `set_pending` raises: the endpoint still returns the plan with
    status `200` (best-effort) and logs.
  - Existing text tests stay green (no `set_pending` call on `type == "text"`).

### Part C — `list_pending_plans` reads `sessions.pending` (Commit 2)

Files: `src/db/session.py`, `tests/test_db_session.py`, `src/api/approvals.py`.

- `src/db/session.py::list_pending_plans` (`:415-454`): replace the
  `role="plan"` scan with a query over `Session`:
  ```python
  stmt = (
      select(Session.id, Session.pending)
      .where(Session.pending.is_not(None))
      .order_by(Session.pending["created_at"].as_string().desc())
      .limit(limit)
  )
  rows = (await session_db.execute(stmt)).all()
  ```
  For each row: `record = row.pending or {}`; `plan = record.get("plan") or {}`;
  skip when `plan.get("plan_id")` is falsy; build the same dict keys as today
  (`plan_id`, `session_id`, `goal` = `plan.get("goal")` (fall back to `""`),
  `revision`, `step_count`, `risks`, `created_at` = `record.get("created_at")`).
  Update the docstring (authority is `sessions.pending`; superseded plans are not
  listed because a newer `set_pending` overwrote the slot).
- `src/api/approvals.py` (`:20`): update the route docstring to "plans awaiting a
  decision (one pending plan per session)".
- Tests (`tests/test_db_session.py`):
  - Rewrite `test_list_pending_plans_dedupes_to_newest_revision` and
    `test_list_pending_plans_skips_rows_without_plan_id` around `Session` rows
    with a `pending` record (the `_FakeSession`/`_FakeResult` doubles must return
    rows with `.id`/`.pending` via `.all()`). Assert the returned keys/values
    (`plan_id`, `session_id`, `goal`, `revision`, `step_count`, `risks`,
    `created_at`).
  - Add a case where a session's `pending` has no `plan_id` → skipped.
  - Note: dedup by `plan_id` is no longer needed (the slot is single) — remove
    that expectation and the `_plan_row` helper if unused.

### Part D — Move the compaction threshold into `refresh_memory` (Commit 2)

Files: `src/core/memory.py`, `src/llm/router.py`, `tests/test_memory.py`,
`tests/test_router.py`, `docs/architecture/AGENT.md`.

- `src/core/memory.py::refresh_memory` (`:150-208`): after
  `history = _history_dicts(messages)`, compute
  `total = sum(len(str(message.get("content") or "")) for message in history)`
  and return the current memory unchanged when
  `total <= MEMORY_COMPACT_AFTER_CHARS`. Then run `select_foldable(history)` as
  today. Update the docstring to state this function is the single compaction
  gate.
- `src/llm/router.py`:
  - `route` finally block (`:488`): call `self._schedule_memory_refresh(session_id, config)`.
  - `_schedule_memory_refresh` (`:490-510`): drop the `history` parameter and the
    `total`/`select_foldable` short-circuit; keep only the in-flight dedup and
    scheduling. Update the docstring ("`refresh_memory` decides whether there is
    anything to fold; scheduling is cheap and bounded").
  - Keep `MEMORY_COMPACT_AFTER_CHARS` imported/used only where it belongs
    (memory.py). Remove any now-unused imports in `router.py`
    (`select_foldable`, the constant) if ruff flags them.
- `docs/architecture/AGENT.md` (Latency → Long-conversation memory): reword "Once
  the history of a session grows past `MEMORY_COMPACT_AFTER_CHARS` …" to state
  the threshold is evaluated by `refresh_memory` over the foldable
  user/assistant history, and that the scheduler runs every turn.
- Tests:
  - `tests/test_memory.py`: add `test_refresh_memory_skips_below_the_compact_threshold`
    (e.g. two 4000-char messages = 8000 > keep 6000 but <= 12000 → model not
    called, existing memory returned) and
    `test_refresh_memory_folds_when_tool_rows_hide_older_text` (input contains
    `role="tool"` rows plus older user/assistant totaling > 12000 → folds; tool
    rows are ignored by `_history_dicts`). Keep the existing 7000+7000 tests
    green.
  - `tests/test_router.py`: `test_route_schedules_no_refresh_for_a_short_history`
    (`:1690`) is now obsolete — replace it with a test asserting a refresh is
    scheduled for every turn and that the short-history decision is delegated to
    `refresh_memory` (i.e. the fake is called). Keep
    `test_route_schedules_a_refresh_for_a_long_history` and the
    failure-does-not-fail-the-turn test green.

### Part E — Process-wide memory in-flight guard (Commit 2)

Files: `src/llm/router.py`, `tests/test_router.py`.

- Add module-level state in `src/llm/router.py`:
  ```python
  # Process-wide, so every AIRouter instance (REST + WebSocket) dedupes the same
  # session's compaction; the guard is per session id, not per router.
  _MEMORY_INFLIGHT: set[str] = set()
  _MEMORY_TASKS: set[asyncio.Task] = set()
  ```
- Remove the per-instance `self._memory_inflight` / `self._memory_tasks` from
  `__init__`. `_schedule_memory_refresh`, `_run_memory_refresh` and `aclose` use
  the module-level sets. `aclose` cancels `_MEMORY_TASKS`, awaits them, then
  clears both sets.
- Tests (`tests/test_router.py`): update references from `router._memory_inflight`
  / `router._memory_tasks` to `router_module._MEMORY_INFLIGHT` /
  `router_module._MEMORY_TASKS`. Add a test that a second `AIRouter` instance
  does not schedule a refresh for a session already in `_MEMORY_INFLIGHT`. Ensure
  module state is reset between tests (clear both sets in the `patched_router`
  fixture or an autouse fixture).

## 6. Tests

Added/updated as listed per part. Exact gates (run from the repo root):

```
ruff check .
pytest -m "not integration and not e2e"
pytest tests/test_plugin_manager.py tests/test_LoomOps_tools.py tests/test_websocket.py tests/test_chat_api.py tests/test_db_session.py tests/test_memory.py tests/test_router.py -q
```

No frontend change, so the frontend gates are not required.

## 7. Acceptance criteria

1. Calling `execute_tool_by_name` with a non-safe plugin tool and
   `approved=False` raises `PermissionError`; with `approved=True` it dispatches.
   `ops_run_command` is denied without approval even if a plugin declares it
   `safe`. Argument validation still runs first (`ToolContractError` unchanged).
2. `POST /api/chat` that yields a plan persists it with `set_pending` and returns
   the plan dict unchanged; it never executes a tool. `type == "text"` does not
   touch `set_pending`.
3. `list_pending_plans` derives from `sessions.pending`; a session whose slot was
   overwritten (superseded) contributes only the newest plan; a pending without
   a `plan_id` is skipped.
4. `refresh_memory` returns the stored memory unchanged when the foldable
   user/assistant history is `<= MEMORY_COMPACT_AFTER_CHARS`, and folds a
   tool-heavy history whose user/assistant text exceeds it. The scheduler
   schedules a refresh every turn and does not gate.
5. Two `AIRouter` instances cannot compact the same session at once (shared
   `_MEMORY_INFLIGHT`); `aclose()` cancels shared tasks and clears the guard.
6. `ruff check .` is clean and `pytest -m "not integration and not e2e"` is green
   (baseline before this TZ: 1398 passed, 13 skipped, 5 deselected).
7. Docs updated in the same commit as the behaviour they describe
   (AGENT.md for Parts B and D; CHANGELOG.md per commit).

## 8. Commits

Coordinator commits only after acceptance (RULES 0a). Two commits, in order:

**Commit 1 — dispatch safety / REST plan handling:**
`fix(core): re-check tool risk at dispatch and register REST plans as pending`

**Commit 2 — pending authority + memory:**
`fix(core): pending-plan authority, memory gate in one place, shared refresh guard`

Each commit updates its own `CHANGELOG.md` entry (RULES 14, 1–2 lines) and the
docs changed by that commit. The executor reports changes and
`Commits created: none`.

## 9. Risks

- **Double-gating is intentional.** `execute_plan` already blocks non-safe steps
  without approval; the new dispatch check is defense in depth. Do not relax
  either. Any caller that legitimately runs a non-safe tool must pass
  `approved=True` (only the executor's dispatch does).
- **`PermissionError` surface**: a legitimately blocked call should never occur in
  production; if it does, the WS `_safe_error_message` maps it to a generic
  message and the step is marked failed. Acceptable.
- **REST pending has no attach-prompt message**: unlike the WS path, REST does not
  persist an "Awaiting approval" assistant message. The pending record alone is
  enough for hydration and approval; do not add a message.
- **Shared memory guard and `aclose()`**: with two routers, either `aclose()` may
  cancel the shared tasks; this is only called on shutdown, so it is safe. Tests
  must clear the module sets to avoid cross-test leakage.
- **JSONB ordering**: `Session.pending["created_at"].as_string().desc()` orders
  ISO-8601 strings lexicographically; all writers use offset-aware UTC ISO, so
  ordering is correct. Do not switch to a locale-dependent format.
- **Scheduler now runs every turn**: `refresh_memory` performs one bounded
  200-row SELECT and, below the threshold, no model call. This is the intended
  tradeoff for correctness; do not reintroduce a filtered-history gate.
