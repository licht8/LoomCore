# Report — Bare tool-name leak (assistant text = `reply` / `agent_plan`)

## Goal

Guarantee that a planner tool name can never reach the user or the stored chat
history as an assistant message, and stop already-leaked history from replaying
and rendering.

## Root cause

The planner sometimes returned a completion with **no tool call** whose `content`
was only a bare tool name (`reply`, then `agent_plan`). `route()` accepted it as
the answer:

- `src/llm/router.py` — when there were no `raw_tool_calls`,
  `_recover_textual_plan_args` returned `None` (a bare name carries no `[tool …]`
  tag or JSON to recover) and `_strip_tool_prefix(content)` was persisted and
  returned. `_strip_tool_prefix` only matches `reply:` / `agent_plan -` prefixes
  (it requires a separator), so a bare name passed through untouched.
- `_api_history` dropped leaked `[tool …]` / `Tool result (…)` notes but not bare
  tool names, so the leaked assistant row was replayed into the next prompt; the
  model imitated its own leak and kept emitting `agent_plan` — the observed loop.

Likely trigger: the inference endpoint ignored `tool_choice: "required"` and
printed the tool name as text. The guard is provider-agnostic.

## Delivered

- `src/llm/router.py`
  - `_is_bare_tool_name(text, known_tools=())` — normalizes (whitespace,
    backticks/quotes, case) and matches `{reply, agent_plan}` or a known catalog
    tool name.
  - Reply path: an empty or bare-name `reply` text is treated as malformed
    instead of being persisted/returned.
  - No-tool-call path: a bare-name answer is malformed too; it logs a warning and
    re-asks once (`_MALFORMED_NUDGE`), reusing `PLANNER_REASK_LIMIT`.
  - After the re-ask still fails, returns and persists the neutral
    `_CONFUSED_REPLY` ("Sorry, I didn't quite catch that — could you rephrase your
    request?") — never a tool name.
  - `_api_history` also drops assistant rows that are bare planner tool names, so
    already-poisoned sessions stop looping (no migration).
- `src/llm/prompt.py` — planner rule extended: never output a tool name by
  itself; call the tool instead.
- `frontend/lib/chat-hydrate.ts` — `isBareToolName` guard skips assistant rows
  whose content is a leaked bare tool name, so historical leaks no longer render
  as assistant bubbles.

## Tests

- `tests/test_router.py` — `_is_bare_tool_name` variants; bare `reply` / bare
  `agent_plan` / bare known tool name / empty `reply` text all malformed and
  re-asked; the valid retry wins; two bare answers fall back to `_CONFUSED_REPLY`;
  `_api_history` drops bare-name rows.
- `tests/test_prompt.py` — the prompt forbids bare tool names.
- No frontend test file changed; the hydrate guard is covered by `tsc`.

## Verification

- `ruff check .` — clean.
- `pytest -m "not integration and not e2e"` — 537 passed, 2 deselected.
- `npx tsc --noEmit` (frontend) — clean.

## Commits

1. `fix(llm): never surface a bare planner tool name as chat text`
2. `fix(prompt): forbid bare tool names in the planner instructions`
3. `fix(frontend): hide leaked bare tool-name assistant rows`
4. `docs: add the bare-tool-name leak task and report`

## Notes / follow-ups

- Tradeoff: a legitimate one-word reply that happens to equal a tool name
  (`reply`, `agent_plan`, or a catalog name) is treated as malformed and re-asked
  once. Acceptable — such a reply is far more likely a leak than meaningful.
- The guard is defensive; the real fix is the provider honouring
  `tool_choice: "required"`. The warning log lets us measure how often that
  fails.
- `_strip_tool_prefix` still requires a separator, so normal prose such as
  "Reply is required" is unaffected.
