# Agent — Plan-and-Execute Loop

LoomCore runs a real agent, not an LLM that calls tools reactively. The model
proposes a plan; the user approves it; deterministic code executes it. This is
the Plan-and-Execute pattern with a human-in-the-loop gate.

## Flow

1. **Plan.** The planner LLM is offered exactly one callable tool, `agent_plan`.
   The live tool catalog is passed as context, never as callable functions. The
   planner returns a goal and an ordered list of steps (`tool`, `args`, `why`).
2. **Classify.** Each step gets a risk (`safe` / `moderate` / `dangerous`) via
   `classify_tool_risk` in `src/core/security.py`.
3. **Confirm.** The plan is shown in the UI.
   - All steps safe: it auto-runs.
   - Any non-safe step: the user must approve, edit, or reject.
4. **Execute.** The executor replays the stored steps verbatim through the same
   tool dispatcher the rest of the app uses. It never re-asks the model between
   steps and never paraphrases arguments.
5. **Summarize.** A second LLM call narrates the result, voiced by
   `src/llm/SOUL.md`.

## Plan object

Persisted in `messages.tool_calls` (JSONB) under `role = "plan"` — no migration.

```json
{
  "type": "plan",
  "plan_id": "pl_<uuid>",
  "revision": 1,
  "goal": "why this plan exists",
  "steps": [
    {"id": "s1", "tool": "ops_disk_usage", "args": {}, "why": "...",
     "risk": "safe", "status": "pending"}
  ],
  "status": "proposed",
  "created_at": "<utc iso>"
}
```

Step status: `pending` / `running` / `done` / `failed` / `blocked`.
Plan status: `proposed` / `approved` / `running` / `done` / `failed` /
`rejected` / `cancelled`.

Editing a plan keeps its `plan_id`, bumps `revision`, keeps the goal, and
re-classifies every step (`next_revision`). A different goal is a new plan.
A failed step stops execution; there is no automatic re-planning.

## Safety invariant

A step whose risk is not `safe` runs only when the plan status is `approved`.
This is enforced in `execute_plan`, independent of the prompt. The prompt only
shapes plan quality; code owns what is allowed to run.

## Events and actions

Server → client (`src/interfaces/tool_events.py`):

- `plan_proposed` — plan, revision, goal, `requires_approval`
- `plan_step` — plan_id, revision, step, phase (`started` / `done` / `failed`)
- `plan_done` — plan, revision, status
- plus the existing `tool_call` / `tool_response` for each step

Client → server (`src/interfaces/websocket.py`):

- `plan_approve` — `{plan_id, revision}`
- `plan_edit` — `{plan_id, revision, steps}`
- `plan_reject` — `{plan_id}`

On reload, the frontend collapses plan revisions by `plan_id` (last wins) and
rebuilds step cards from the persisted tool messages.

## Files

| File | Role |
| --- | --- |
| `src/core/agent.py` | plan model, validation, deterministic executor |
| `src/llm/prompt.py` | planner/summary message builders, SOUL loader |
| `src/llm/SOUL.md` | voice only (identity, tone, format); summary call only |
| `src/llm/router.py` | planner call, `agent_plan` parsing, summary call |
| `src/interfaces/websocket.py` | plan events, approval actions, execution |
| `src/interfaces/tool_events.py` | plan/tool payloads and persistence |
| `src/core/security.py` | `classify_tool_risk` |

## Non-goals

No autonomous multi-turn loop, no streaming, no parallel steps, no persistent
agent memory beyond the message history. SOUL.md never influences planning.
