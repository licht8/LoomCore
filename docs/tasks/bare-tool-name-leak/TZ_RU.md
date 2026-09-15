# TZ — Bare tool-name leak (chat shows `reply` / `agent_plan`)

## Goal

Stop the assistant from surfacing a bare planner tool name (`reply`, `agent_plan`,
or any catalog tool) as chat text, and stop the self-reinforcing loop it causes
in later turns. Fix the cause in the LLM router, harden the planner prompt, heal
already-poisoned history, and hide leaked rows in the UI.

## Non-goals

- No change to the plan-and-execute flow, tool schemas or risk handling.
- No DB migration; no backfill of stored rows (history is filtered at read time).
- No response-model rewrite: the friendly fallback stays a plain text answer.

## Root cause

Some providers ignore `tool_choice="required"` and return a completion with **no**
`tool_calls` whose `content` is just the tool name. `AIRouter.route` treated that
as the answer:

- `src/llm/router.py:186-189` — no `raw_tool_calls` -> `_recover_textual_plan_args`
  returns `None` (a bare name has no `[...]`/JSON) -> the name is persisted and
  returned.
- `src/llm/router.py:41` — `_strip_tool_prefix` only matches `reply:`/`reply -`
  (separator required), so a bare name passes through.
- `src/llm/router.py:174-180` — the reply path falls back to `content`, which can
  be the bare name.
- `src/llm/router.py:296-317` — `_api_history` replays the leaked assistant row, so
  the model imitates itself and emits the bare name again.

## Changes

### `src/llm/router.py`

- Add `_BARE_TOOL_NAMES = frozenset({AGENT_PLAN_TOOL, REPLY_TOOL})`,
  `_MALFORMED_NUDGE`, `_CONFUSED_REPLY` and
  `_is_bare_tool_name(text, known_tools=())` (normalize whitespace/backticks, lower,
  membership in `_BARE_TOOL_NAMES | known_tools`). `_TOOL_PREFIX_RE` is unchanged.
- Reply path: return only when the resolved text is non-empty and not a bare
  tool name; otherwise mark the turn malformed.
- No-tool-call path: return only when the stripped answer is non-empty and not a
  bare tool name; otherwise mark the turn malformed.
- Malformed turn: log a warning, re-ask once (existing `PLANNER_REASK_LIMIT`) with
  a corrective hint; after the last attempt return `_CONFUSED_REPLY` and persist
  that friendly text instead of a tool name.
- `_api_history`: also drop assistant rows that are bare planner tool names.

### `src/llm/prompt.py`

- Add a rule forbidding a bare tool name as the whole answer (call the tool
  instead), alongside the existing "never prefix with the tool name" line.

### `frontend/lib/chat-hydrate.ts`

- Skip assistant rows whose trimmed content is a bare planner tool name so old
  leaked rows stop rendering as bubbles.

## Minimal diff

- `src/llm/router.py`, `src/llm/prompt.py`
- `frontend/lib/chat-hydrate.ts`
- `tests/test_router.py`, `tests/test_prompt.py`
- `docs/tasks/bare-tool-name-leak/TZ_RU.md`, `REPORT_RU.md`

## Tests

- `tests/test_router.py`: bare `reply`/`agent_plan` text responses are never shown
  or persisted; a bare name triggers one re-ask and the valid retry wins; two bare
  answers give the friendly fallback; `_api_history` drops bare-name rows;
  `_is_bare_tool_name` cases (`reply`, `  agent_plan  `, `` `reply` `` bare;
  `Reply is required` not bare).
- `tests/test_prompt.py`: the planner prompt forbids a bare tool name.

## Acceptance criteria

1. No bare planner/catalog tool name reaches the user or `messages` history.
2. A bare answer triggers at most one re-ask, then a friendly text fallback.
3. Already-persisted bare-name assistant rows are not replayed and not rendered.
4. A one-word legitimate reply equal to a tool name is treated as malformed (known,
   accepted tradeoff).
5. `ruff check .` clean; `pytest -m "not integration and not e2e"` green;
   `npx tsc --noEmit` in `frontend/` clean.

## Commits

1. `fix(llm): never surface a bare planner tool name as chat text`
2. `fix(prompt): forbid bare tool names in the planner instructions`
3. `fix(frontend): hide leaked bare tool-name assistant rows`
4. `docs: add the bare-tool-name leak task and report`

## Risks

- Over-aggressive stripping could hide a real one-word reply; only an exact tool
  name (case-insensitive, after trimming quotes/backticks) is affected.
- A provider that keeps ignoring `tool_choice` will now yield the friendly
  fallback instead of a leak — user-visible but safe.
- The frontend guard is display-only; the DB keeps the old rows (no migration).
