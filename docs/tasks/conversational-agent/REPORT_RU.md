# Report — Conversational-First Agent

## Goal

Conversation is the default; a structured plan is the exception. A single obvious
action runs as a one-step *direct* action with no card; multi-step, explicit, or
ambiguous work becomes a *plan*. Dangerous direct actions are confirmed in plain
text. `agent_plan` remains the only callable tool, so plan-first is still enforced
structurally.

## Delivered

- `src/core/agent.py` — optional `mode` ("direct"/"plan"); `_effective_mode`
  clamps shape (declared plan wins, more than one step forces plan, else direct);
  `plan_is_direct`; `next_revision` forces plan; `validate_plan` rejects bad mode.
- `src/llm/prompt.py` — conversational-first `PLANNER_PROMPT`; the planner system
  message is prefixed with `SOUL.md`.
- `src/core/confirmations.py` + `src/core/confirmations.json` — config-driven
  plain-text confirmation parser (`parse_confirmation`, `normalize`,
  `load_phrases`), 300 s timeout, fail-safe on missing/corrupt config; new
  `Settings.confirmations_file` with `CONFIRMATIONS_FILE` override.
- `src/interfaces/websocket.py` — direct-safe auto-run without a plan card;
  direct-dangerous asks `"... Reply yes to proceed."` and stores the pending plan;
  a text path parses confirmations (approve / reject / one re-ask / timeout reset)
  before normal routing; `_run_plan` skips plan events for direct runs.
- `frontend/lib/chat-types.ts` — `Plan.mode` added; no functional change (direct
  runs render through the existing tool cards and response).

## Verification

- `ruff check .` — clean.
- `pytest -m "not integration and not e2e"` — 412 passed, 2 deselected.
- Frontend `npx tsc --noEmit` — clean; `npm run build` — 3/3 static pages.

## Commits

1. `45b9009 feat(core): conversational-first planner with direct and plan modes`
2. `e5645e8 feat(core): parse plain-text confirmations from a config file`
3. `9f5e544 feat(interfaces): run direct actions and confirm in plain text`
4. `docs: document conversational-first behavior and confirmations`

## Notes / follow-ups

- Each tool turn still costs one planner call plus one summary call.
- A failed step stops the run; there is no automatic re-plan.
- The legacy single-tool approval path stays removed; confirmation reuses the
  existing approve action.
- Direct confirmations are ordinary text messages, so they appear in history
  alongside the conversation.
