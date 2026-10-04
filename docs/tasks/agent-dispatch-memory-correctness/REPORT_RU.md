# REPORT_RU.md — agent-dispatch-memory-correctness

> Executor report (RULES 0, 0a). Content is English. No secrets. The executor
> did not commit; the coordinator commits after acceptance.

Task spec: `docs/tasks/agent-dispatch-memory-correctness/TZ_RU.md`.

## Goal

Fix five verified defects in two logical commits:

- **Commit 1** — dispatch safety / REST plan handling: (A) re-check a tool's risk
  at the plugin dispatch boundary so `approved` can never be silently dropped;
  (B) REST `POST /api/chat` registers a returned plan as the session's pending
  plan instead of returning an unapprovable phantom.
- **Commit 2** — pending-plan authority + long-conversation memory: (C)
  `GET /api/approvals` reads the authoritative `sessions.pending` slot; (D) the
  compaction threshold lives in `refresh_memory` over the foldable
  user/assistant history, not in the scheduler's filtered view; (E) the memory
  in-flight guard is process-wide so REST and WebSocket cannot compact the same
  session concurrently.

## Delivered

Commit 1 (Parts A–B):

- `src/plugins/manager.py`: `execute_tool_by_name` gained keyword-only
  `approved: bool = False`; after `validate_tool_args` it resolves
  `classify_tool_risk(tool_name, self.tool_risk(tool_name))` and raises
  `PermissionError` for a non-safe tool unless approved. Imported
  `classify_tool_risk`; docstring updated.
- `src/interfaces/websocket.py`: `_execute_tool` forwards `approved=approved` to
  the plugin branch.
- `src/api/chat.py`: imports `async_session_factory` and `set_pending`; after a
  successful `route()`, a `{"type": "plan", ...}` result is persisted best-effort
  with `set_pending(db, session_id, plan, message)` and the route result is
  returned unchanged. No execution path is added.
- Docs: `docs/architecture/AGENT.md` (REST is conversation/planning only),
  `CHANGELOG.md`.

Commit 2 (Parts C–E):

- `src/db/session.py`: `list_pending_plans` now selects `Session.id,
  Session.pending` where `pending IS NOT NULL`, ordered by
  `pending["created_at"].as_string().desc()`, skips records without a
  `plan_id`, and returns the same key set. No `role="plan"` row is touched.
- `src/api/approvals.py`: route docstring updated.
- `src/core/memory.py`: `refresh_memory` computes the foldable user/assistant
  char total from its own 200-row read and returns the stored memory unchanged
  at or below `MEMORY_COMPACT_AFTER_CHARS`; it is now documented as the single
  compaction gate.
- `src/llm/router.py`: module-level `_MEMORY_INFLIGHT` / `_MEMORY_TASKS`;
  per-instance sets removed; `_schedule_memory_refresh(session_id, config)` only
  dedups and schedules every turn; `_run_memory_refresh` and `aclose` use the
  shared sets; unused imports dropped.
- Docs: `docs/architecture/AGENT.md` (long-conversation memory), `CHANGELOG.md`.

## Verification

- `ruff check .` — clean ("All checks passed!").
- `pytest tests/test_plugin_manager.py tests/test_LoomOps_tools.py tests/test_websocket.py tests/test_chat_api.py tests/test_db_session.py tests/test_memory.py tests/test_router.py -q` — 454 passed, 9 skipped.
- `pytest -m "not integration and not e2e"` — 1406 passed, 13 skipped, 5 deselected (baseline before this TZ: 1398 passed, 13 skipped, 5 deselected; +8 new tests). Output: `tmp/tests_fast.log`.

## Changed files

- `src/plugins/manager.py` — risk re-check + approval gate at dispatch.
- `src/interfaces/websocket.py` — forward `approved`.
- `src/api/chat.py` — register a returned plan as pending (best-effort).
- `src/db/session.py` — `list_pending_plans` reads `sessions.pending`.
- `src/api/approvals.py` — docstring.
- `src/core/memory.py` — compaction gate in `refresh_memory`.
- `src/llm/router.py` — process-wide memory in-flight guard.
- `docs/architecture/AGENT.md` — REST planning-only note; memory gate wording.
- `CHANGELOG.md` — one entry per commit.
- `tests/test_plugin_manager.py` — approval-gate tests.
- `tests/test_LoomOps_tools.py` — `approved=True` on the two accept tests.
- `tests/test_websocket.py` — forwarded-flag assertion.
- `tests/test_chat_api.py` — plan-persistence tests plus failure/no-persist cases.
- `tests/test_db_session.py` — rewritten pending tests around `Session` rows.
- `tests/test_memory.py` — below-threshold and tool-heavy-history tests.
- `tests/test_router.py` — module-level guard updates + second-router test.

## Commits

Planned by the coordinator (executor created none):

- Commit 1 — `fix(core): re-check tool risk at dispatch and register REST plans as pending`
- Commit 2 — `fix(core): pending-plan authority, memory gate in one place, shared refresh guard`

Commits created: none

## Notes and follow-ups

- The dispatch risk check is defense in depth: `execute_plan`'s safety invariant
  is unchanged; only the executor's dispatch passes `approved=True`.
- `AGENT.md` carries changes from both commits; the coordinator should stage the
  Part B paragraph with Commit 1 and the memory paragraph with Commit 2 if a
  clean per-commit split is required.
- `list_pending_plans` ordering relies on offset-aware ISO-8601 UTC strings in
  `pending["created_at"]` (all writers use it).
- Accepted limitation (out of scope): memory text older than
  `MEMORY_HISTORY_LIMIT` (200) rows is not folded.
- `MEMORY_COMPACT_AFTER_CHARS` and `select_foldable` imports were removed from
  `src/llm/router.py` now that the gate moved into `refresh_memory`.
