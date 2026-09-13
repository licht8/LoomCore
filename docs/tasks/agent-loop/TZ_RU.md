# TZ_RU.md — Plan-and-Execute Agent Loop

## 1. Goal

Make LoomCore a real agent, not an LLM-plus-tools wrapper. The LLM proposes an
explicit, structured **plan**; the user reviews/approves/edits/rejects; a
deterministic **executor** in code runs the approved plan verbatim and reports
results. Plan-first becomes a structural guarantee, not prompt behavior.

## 2. Non-goals

- No autonomous re-planning loop (v1 is Plan -> Confirm -> Execute -> Summarize;
  step failure stops).
- No streaming token output; no parallel tool execution.
- No new third-party dependency; no DB schema migration.
- No changes to plugins (`plugins/LoomWG`, `plugins/LoomOps`) or to
  `classify_tool_risk`'s table.
- SOUL.md is voice only — no behavior/safety/tool/approval content.

## 3. Approved decisions (locked)

1. **Only `agent_plan` is callable by the LLM.** The real tool catalog is passed
   as context, never as callable functions. The executor is the sole dispatcher.
2. **All-safe plan -> auto-run and shown.** Any step classified
   moderate/dangerous -> one `plan_proposed` approval for the whole revision.
3. Execution uses stored `step.tool` + `step.args` **verbatim**.
4. **Edit patches the plan** (steps mutated, `revision++`, re-validated and
   re-classified); a changed goal is a new `plan_id`.
5. Step failure **stops** execution and surfaces; no automatic re-plan.
6. Plans persist as `Message.tool_calls` JSONB (role `"plan"`) — no migration.
7. `SOUL.md` loads only for the **summary call**, never for the planner.

## 4. Data model

Plan object (persisted in a `Message` with `role="plan"`, `content=goal`,
`tool_calls=[plan]`):

```json
{
  "type": "plan",
  "plan_id": "pl_<uuid>",
  "revision": 1,
  "goal": "why this plan exists",
  "steps": [
    {"id": "s1", "tool": "ops_disk_usage", "args": {}, "why": "...",
     "risk": "safe|moderate|dangerous", "status": "pending"}
  ],
  "status": "proposed|approved|running|done|failed|rejected|cancelled",
  "created_at": "<utc iso>"
}
```

Rules: `steps[i].tool` must exist in the live registry; `args` must be a JSON
object; step ids are `s1..sN`; `revision` starts at 1.

## 5. Part 1 — Core agent (`src/core/agent.py`, new)

- `AGENT_PLAN_SCHEMA: dict` — OpenAI function schema for the single callable
  tool `agent_plan` (`goal: str`, `steps: [{tool: str, args: object, why: str}]`).
- `MAX_PLAN_STEPS = 12`, `PLANNER_REASK_LIMIT = 1`.
- `build_plan(raw_args) -> dict` — assign `plan_id`, `revision=1`, step ids,
  `risk` via `classify_tool_risk`, `status="pending"`.
- `validate_plan(plan, known_tools) -> list[str]` — structural + reference checks.
- `plan_requires_approval(plan) -> bool` — any step not classified safe.
- `next_revision(plan, steps) -> dict` — patch steps, bump revision, re-classify.
- `execute_plan(plan, *, dispatch, emit, max_steps)` — deterministic replay;
  refuses a non-safe step unless `plan["status"] == "approved"`; a failed step
  stops execution. `dispatch` is injected so the module stays transport-agnostic.

## 6. Part 2 — Prompts and SOUL (`src/llm/prompt.py`, `src/llm/SOUL.md`)

- Keep `SYSTEM_PROMPT` and `build_messages` (compat).
- Add `build_planner_messages(user_text, history, tool_catalog)`,
  `load_soul(path=None)` (fallback `DEFAULT_SOUL`), and
  `build_summary_messages(user_text, plan, observations)`.
- Add `src/llm/SOUL.md` — voice only (identity, language, tone, format, plan
  summaries, failure reporting, prohibitions). No behavior rules.

## 7. Part 3 — Router (`src/llm/router.py`)

- `route()` becomes planner-only: `_collect_tools()` -> `build_planner_messages`
  -> `chat_completion(messages, tools=[AGENT_PLAN_SCHEMA])` -> parse.
  - `agent_plan` -> `build_plan` + `validate_plan`; valid -> `_persist_plan` and
    return `{"type": "plan", "plan": ...}`.
  - plain text -> `{"type": "text", "content": ...}`.
  - malformed / unexpected tool -> one re-ask, then an error text.
- Add `summarize_plan(user_text, plan, observations) -> str` (the SOUL call).
- Replace `_build_llm_tools` with `_catalog_text`.

## 8. Part 4 — WebSocket (`src/interfaces/websocket.py`, `tool_events.py`)

- `_process_user_message`: text -> response; plan -> `save_plan_message` +
  `plan_proposed`; all-safe auto-runs, otherwise wait for a client action.
- `_run_plan`: `execute_plan(dispatch=_execute_tool, emit=...)`; per step emits
  `plan_step` plus the existing `tool_call`/`tool_response` cards; then
  `summarize_plan`, persist, emit `plan_done` + response.
- New actions: `plan_approve`, `plan_edit`, `plan_reject`.
- Remove the now-unreachable single-tool approval branch; keep
  `classify_tool_risk`.

## 9. Part 5 — Frontend (`frontend/`)

Read `frontend/node_modules/next/dist/docs/` first (per `frontend/AGENTS.md`).
Types, WS handling, `use-chat` state, hydration, the plan card in
`chat-message.tsx`, and the approve/edit/reject bar in `chat-input.tsx`.

## 10. Tests

`tests/test_agent.py`, `tests/test_prompt.py`, `tests/test_router.py`,
`tests/test_websocket.py`. Gate: ruff, `pytest -m "not integration and not e2e"`,
frontend `npx tsc --noEmit` + `npm run build`.

## 11. Acceptance criteria

1. The LLM is offered only `agent_plan`; tool-needing requests plan first.
2. All-safe plans auto-run; plans with a non-safe step need one approval.
3. Execution uses approved args verbatim; unapproved non-safe steps cannot run.
4. Edit bumps `revision` and re-classifies; reject and failure stop cleanly.
5. Summary is voiced per SOUL.md; the planner never loads SOUL.
6. Plans and step results persist and restore after reload.
7. No migration, no new dependency; ruff + unit suite + frontend build green.

## 12. Commits

1. `feat(core): add plan-and-execute agent core`
2. `feat(interfaces): plan events and approval over the websocket`
3. `feat(frontend): render and approve agent plans`
4. `docs: document the plan-and-execute agent loop`

## 13. Risks

- +1 planner call and +1 summary call per tool turn; catalog tokens per call.
- Plan quality: validation + one re-ask + strict schema.
- Legacy approval removal touches existing websocket tests.
- History replay: plans are append-only; hydrate collapses by `plan_id`.
- Frontend Next 16 breaking changes: consult bundled docs before coding.
