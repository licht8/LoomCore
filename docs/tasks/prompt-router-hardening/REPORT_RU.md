# REPORT_RU.md — prompt/router hardening

Task spec: `docs/tasks/prompt-router-hardening/TZ_RU.md` (D1–D5).

## Goal

Fix six review findings: an unguarded `load_soul` (non-UTF-8 or blank `SOUL.md`),
mis-keyed plan observations in `_render_plan`, unbounded prompt size (results,
user text, history, summary body), a name-braced textual-call regex that hijacked
prose into an empty-args tool call, and an empty assistant turn on re-ask.

## Delivered

### Part 1 — `src/llm/prompt.py` (D1, D2, D3)

- Added `from functools import lru_cache` and four module constants:
  `_MAX_RESULT_CHARS = 2000`, `_MAX_USER_CHARS = 8000`,
  `_MAX_HISTORY_CHARS = 12000`, `_MAX_PLAN_BODY_CHARS = 16000`.
- `load_soul(path=None)` delegates to a private
  `@lru_cache(maxsize=8) _read_soul(path: Path)`: reads with
  `encoding="utf-8"`, returns `DEFAULT_SOUL` on `(OSError, UnicodeDecodeError)` or
  when `text.strip()` is empty; successful reads are memoised per path.
- Added `_truncate(text, limit)` (head/tail plus
  `\n… (truncated N chars) …\n`) and `_trim_history(history, max_chars)`
  (drops the oldest messages while over budget, always keeping the last one and
  truncating it when it alone is oversized).
- Applied the caps: per-result truncation in `_render_plan`, `user_text` in both
  builders, the summary `body` in `build_summary_messages`, and history trimming
  in `build_planner_messages`.
- Rewrote `_render_plan` to iterate with `enumerate`, skip non-dict observations
  and non-dict/`None` steps, key by `str(step.get("id") or f"s{index}")`, and keep
  only the first result per key. Plan steps are looked up the same way.
  `build_messages` was left untouched (non-goal).

### Part 2 — `src/llm/router.py` (D4, D5)

- `_recover_textual_plan_args`: a `_NAME_BRACED_JSON_RE` candidate is now kept
  only when the name is in `known_tools` or `_BARE_TOOL_NAMES`, the balanced body
  is found, and `json.loads(body)` returns a `dict`; otherwise it is skipped, so
  prose with a known name and non-JSON braces stays plain text.
- The re-ask loop appends the assistant turn only `if content:`, so no
  empty-content assistant message is sent.

## Verification

- `uv run ruff check .` — All checks passed.
- `uv run pytest tests/test_prompt.py tests/test_router.py -q` — 65 passed.
- `uv run pytest -m "not integration and not e2e" -q` — 994 passed, 11 skipped,
  5 deselected (`tmp/tests_prompt_router_full.log`; was 984, +10 tests).
- Integration not run (no Postgres needed for this task).

## Tests

- `tests/test_prompt.py`: `test_load_soul_falls_back_on_invalid_utf8`,
  `test_load_soul_uses_default_for_blank_file`,
  `test_render_plan_ignores_non_dict_and_none_step_entries`,
  `test_render_plan_prefers_first_result_for_duplicate_ids`,
  `test_render_plan_truncates_long_result`,
  `test_build_summary_messages_truncates_long_user_text`, and
  `test_build_planner_messages_trims_long_history`.
- `tests/test_router.py`: `test_route_unwrapped_invalid_json_body_not_executed`,
  `test_route_prose_with_known_name_and_brace_not_executed`, and
  `test_route_reask_skips_empty_assistant_content`. Existing unwrapped/recovery
  tests (`agent_plan {json}`, `reply { ... }`, `core_list_tools {}`) stay green.

## Changed files

- `src/llm/prompt.py` — soul fallback, size caps, plan rendering.
- `src/llm/router.py` — parse-gated name-braced candidates, non-empty re-ask.
- `tests/test_prompt.py`, `tests/test_router.py`.
- `docs/tasks/prompt-router-hardening/REPORT_RU.md`.

## Suggested commits

1. `fix(llm): harden soul loading, plan rendering, and prompt size caps`
2. `fix(llm): require parseable textual tool calls and skip empty re-ask turns`

Commits created: none

## Notes / follow-ups

- Truncation keeps head and tail plus an explicit marker, so both ends of a large
  tool output survive; ordinary turns stay well under the caps.
- Requiring `json.loads` for a name-braced candidate may stop recovering
  near-JSON bodies that used to degrade to `{}` and produce a hollow call;
  falling back to plain text is strictly safer.
- `_trim_history` only affects very long sessions; the budget is high
  (12000 chars) and the most recent message is always retained.
