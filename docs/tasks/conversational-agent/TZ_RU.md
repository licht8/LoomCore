# TZ — Conversational-First Agent (direct / plan modes + plain-text confirmation)

## Goal

Make conversation the default and planning the exception. The model must not
produce a structured plan for chit-chat, questions, or a single obvious action.
A single obvious action runs as a one-step *direct* action with no plan card;
only multi-step, explicitly-planned, or genuinely ambiguous work becomes a
structured *plan*. Dangerous direct actions are confirmed in plain text.

The structural invariant is unchanged: `agent_plan` remains the only callable
tool. A direct action is a degenerate one-step plan executed by the same
executor and the same risk gate.

## Non-goals

- No autonomous multi-turn loop; a failed step still stops the run.
- No streaming, no parallel steps, no memory beyond chat history.
- No new approval mechanism — text confirmation feeds the existing approve path.
- `SOUL.md` never influences planning or safety.

## Modes

`agent_plan` carries an optional `mode: "direct" | "plan"`. The effective mode is
clamped in code (`_effective_mode` in `src/core/agent.py`):

- declared `plan` -> `plan` (card even for one step);
- more than one step -> `plan`;
- otherwise -> `direct`.

So one step is direct by default; two or more always require a plan.

## Plain-text confirmation

`src/core/confirmations.py` parses a short message into `approve` / `reject` /
`unclear`. Phrase lists live in `src/core/confirmations.json` (override with
`CONFIRMATIONS_FILE`); the file is authoritative and no lists are hardcoded.
Only a message of at most three words that is entirely an approve/reject phrase
counts: `да, но сначала логи` is `unclear`. A missing or corrupt config yields
empty lists, so nothing is auto-approved (fail-safe).

Confirmation is bound to the pending plan id and session with a 300 s timeout
(`CONFIRMATION_TIMEOUT_SECONDS`). Approve runs the same routine as `plan_approve`;
reject marks the plan rejected and clears it; unclear re-asks once, then the next
message is treated as a new request. The parser is code, not prompt.

## Minimal diff

- `src/core/agent.py` — `VALID_PLAN_MODES`, `_effective_mode`, mode stored by
  `build_plan`, `next_revision` forces `plan`, `validate_plan` rejects an invalid
  mode, new `plan_is_direct`.
- `src/llm/prompt.py` — `PLANNER_PROMPT` rewritten conversation-first;
  `build_planner_messages` prepends `SOUL.md`.
- `src/core/confirmations.py` + `.json` + `Settings.confirmations_file` (new).
- `src/interfaces/websocket.py` — direct-safe runs with no card; direct-dangerous
  asks in plain text and remembers the pending plan; a text path parses
  confirmations before normal routing; `_run_plan` skips plan events for direct.
- `frontend/lib/chat-types.ts` — add `mode?: "direct" | "plan"` to `Plan`.

## Tests

- `tests/test_agent.py` — mode clamp, `plan_is_direct`, `next_revision`, invalid mode.
- `tests/test_prompt.py` — planner prompt carries SOUL + direct/plan guidance.
- `tests/test_confirmations.py` — approve / reject / unclear, extra words, timeout
  boundary helpers, missing and corrupt config, override path.
- `tests/test_websocket.py` — direct-safe auto-run (no `plan_proposed`),
  direct-dangerous prompt then `yes`, `нет` rejects and clears, unclear re-ask then
  reset, timeout as new message, cross-session isolation, `yes` with no pending.

## Acceptance criteria

1. Greetings and questions produce plain text with no `agent_plan` call.
2. A single obvious action runs as direct with no plan card.
3. Multi-step and explicit-plan requests produce a plan card.
4. A non-safe step runs only after approval (card button or clean text confirmation).
5. Confirmation parsing is code-level, config-driven, and fail-safe.
6. ruff clean; `pytest -m "not integration and not e2e"` green; frontend tsc + build green.

## Commits

1. `feat(core): conversational-first planner with direct and plan modes`
2. `feat(core): parse plain-text confirmations from a config file`
3. `feat(interfaces): run direct actions and confirm in plain text`
4. `docs: document conversational-first behavior and confirmations`

## Risks

- Extra planner/summary calls per tool turn (unchanged from before).
- Phrase lists may miss a phrasing; missing entries are `unclear` (safe) and can be
  extended in `confirmations.json` without code changes.
- Direct-dangerous approvals rely on the UI forwarding ordinary text; no buttons.
