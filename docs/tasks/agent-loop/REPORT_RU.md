# REPORT — Plan-and-Execute Agent Loop

Task spec: `docs/tasks/agent-loop/TZ_RU.md`.

## Goal

Turn the assistant from an LLM that calls tools reactively into a real agent:
the LLM proposes an explicit, inspectable plan; the user reviews, approves,
edits or rejects it; a deterministic executor in code replays the approved plan
verbatim and reports results.

## Delivered

- **Core agent** (`src/core/agent.py`): `agent_plan` schema, `build_plan`,
  `next_revision`, `validate_plan`, `plan_requires_approval`, and the
  deterministic `execute_plan` (verbatim args, stops on failure, refuses a
  non-safe step unless the plan is `approved`).
- **Prompts and voice** (`src/llm/prompt.py`, `src/llm/SOUL.md`): planner
  messages with the tool catalog as context; summary messages voiced by SOUL.
  SOUL is used only for the summary call, never for planning.
- **Router** (`src/llm/router.py`): `route()` now offers exactly one callable
  tool (`agent_plan`), returns `type: "plan"` or `type: "text"`, re-asks once on
  a malformed plan, and exposes `summarize_plan()`.
- **Persistence** (`src/db/session.py`): `add_plan_message()` stores plans in
  `messages.tool_calls` JSONB under `role="plan"` — no migration.
- **WebSocket** (`src/interfaces/websocket.py`, `src/interfaces/tool_events.py`):
  plan events `plan_proposed` / `plan_step` / `plan_done` plus the existing
  tool cards; client actions `plan_approve` / `plan_edit` / `plan_reject`;
  all-safe plans auto-run, otherwise a single approval per revision.
- **Frontend** (`frontend/lib/*`, `frontend/components/*`): plan types, plan
  handling in the WS reducer and hydration (revisions collapse by `plan_id`), a
  plan card in the chat feed, and an Approve / Edit / Reject bar with a JSON
  steps editor.

## Verification

- `python -m ruff check .` — clean.
- `python -m pytest -m "not integration and not e2e" -q` — 388 passed, 2 deselected.
- `frontend`: `npx tsc --noEmit` clean; `npm run build` compiled, 3/3 static pages.

## Commits

1. `feat(core): add plan-and-execute agent core`
2. `feat(interfaces): plan events and approval over the websocket`
3. `feat(frontend): render and approve agent plans`
4. `docs: document the plan-and-execute agent loop`

## Notes and follow-ups

- One extra planner call and one summary call per tool turn; the tool catalog is
  passed on every planner call. Accepted for transparency.
- v1 stops at a failed step and does not re-plan; a changed goal is a new plan.
- The legacy single-tool approval path (`approval_request` / `tool_denied`) was
  removed as unreachable once the model could no longer call tools directly.
- Plans are append-only; the frontend collapses revisions by `plan_id` on reload.
