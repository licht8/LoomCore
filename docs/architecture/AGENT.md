# Agent — Conversational-First Loop

LoomCore's assistant is a real agent, not a reactive tool-caller: the language
model never calls a tool directly. Instead it talks to the user, and when work is
needed it proposes it through a single callable tool, `agent_plan`. A
deterministic executor in code decides what actually runs.

The default is conversation. Plans are the exception.

## Modes

- **Conversation** (default) — greetings, questions, explanations, and
  clarifying questions. The model replies with plain text and no tool.
- **Direct** — a single, obvious action ("show disk usage", "restart nginx").
  The model calls `agent_plan` with `mode: "direct"` and exactly one step. Code
  runs it without showing a plan card.
- **Plan** — multi-step work, an explicit request for a plan, or genuinely
  ambiguous scope. The model calls `agent_plan` with `mode: "plan"`; the user
  sees a plan card and approves, edits, or rejects it.

The shape is clamped in code, not left to the model: a declared `plan` always
wins, and any plan with more than one step is forced to `plan`. A single step
with no declared mode becomes `direct`. See `resolve_mode` in `src/core/agent.py`.

## Flow

1. **Understand** — the planner LLM is offered exactly one callable tool,
   `agent_plan`. The real tool catalog is passed as context, never as callable
   functions. If no tool is needed, the model answers in plain text; if it needs
   a fact from the user, it asks one short question in plain text.
2. **Classify** — every step's `risk` is set in code by `classify_tool_risk`
   (`src/core/security.py`): `safe`, `moderate`, or `dangerous`.
3. **Confirm**:
   - direct + safe — runs immediately, no card;
   - direct + non-safe — the assistant asks in plain text ("... Reply yes to
     proceed.") and waits for a short confirmation;
   - plan + all safe — runs immediately and shows the plan card;
   - plan + any non-safe — waits for approve / edit / reject.
4. **Execute** — the executor replays the stored steps verbatim: the approved
   `tool` and `args`, in order. It never re-asks between steps and never
   paraphrases arguments. A failed step stops the plan.
5. **Summarize** — a second LLM call, voiced by `src/llm/SOUL.md`, reports the
   outcome.

## Plain-text confirmation

Direct actions that are not safe are confirmed conversationally, not with
buttons. `src/core/confirmations.py` parses the user's reply:

- Only a short message (three words or fewer) that is *entirely* an approved
  phrase counts. "yes" approves; "да, но сначала логи" is `unclear`.
- Approve and reject phrase lists live in `src/core/confirmations.json`, not in
  code. Override the path with `CONFIRMATIONS_FILE`.
- A pending confirmation is bound to the plan id, the session, and a 300-second
  timeout. An expired or unclear reply is treated as a new message (with one
  re-ask for the first unclear reply), so the agent never hangs waiting.

The result is code, not prompt: a reply only approves when `parse_confirmation`
returns `approve`, and that routes into the same approval path used by
`plan_approve`.

## Plan object

A plan proposed in plan mode is persisted in `messages.tool_calls` JSONB under
`role="plan"` (no migration needed):

```json
{
  "type": "plan",
  "plan_id": "pl_...",
  "revision": 1,
  "goal": "One line: what the plan achieves",
  "steps": [
    {"id": "s1", "tool": "ops_disk_usage", "args": {}, "why": "...",
     "risk": "safe", "status": "pending"}
  ],
  "mode": "plan",
  "status": "proposed",
  "created_at": "2025-01-01T00:00:00+00:00"
}
```

- Step status: `pending`, `running`, `done`, `failed`, `blocked`.
- Plan status: `proposed`, `approved`, `running`, `done`, `failed`, `rejected`,
  `cancelled`.
- Editing keeps the `plan_id`, bumps `revision`, and re-classifies every step
  (`next_revision`). A different goal is a new plan. A failed step stops the
  plan; there is no automatic re-planning.
- Direct actions run without a plan card, so they are not persisted as a plan.

## Safety invariant

A non-safe step runs only when the plan status is `approved`. This is enforced in
`execute_plan`, not by the prompt: the planner shapes quality, but code decides
what runs. Direct-dangerous actions set `approved` only after a clean text
confirmation.

## Events and actions

- Server to client: `plan_proposed`, `plan_step`, `plan_done` for plan mode;
  `tool_call`, `tool_response`, and `response` for both modes.
- Client to server: `plan_approve`, `plan_edit`, `plan_reject` for plan cards.
- Direct confirmation is an ordinary text message; the server parses it.
- On reload, plan revisions are collapsed by `plan_id` (last one wins).

## Files

| File | Responsibility |
| --- | --- |
| `src/core/agent.py` | Plan model, mode clamp, validation, executor |
| `src/core/confirmations.py` | Plain-text confirmation parser |
| `src/core/confirmations.json` | Approve/reject phrase lists |
| `src/llm/prompt.py` | Planner and summary message builders, SOUL loader |
| `src/llm/SOUL.md` | Voice for the summary call (never planning) |
| `src/llm/router.py` | Planner call, `agent_plan` parsing, summary |
| `src/interfaces/websocket.py` | Direct run, plan events, approval, execution |
| `src/interfaces/tool_events.py` | Payloads and persistence |
| `src/core/security.py` | `classify_tool_risk` |

## Non-goals

- No autonomous multi-turn loop; v1 stops at each step and on failure.
- No token streaming, no parallel steps, no memory beyond chat history.
- SOUL never influences planning or safety.
