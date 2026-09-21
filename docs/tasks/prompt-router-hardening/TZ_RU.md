# TZ_RU.md — prompt/router hardening

## 1. Goal

Fix six review findings in the LLM prompt builders and the textual-plan recovery path:

1. `load_soul` only catches `OSError`, so a present-but-non-UTF-8 `SOUL.md`
   raises `UnicodeDecodeError`; a present-but-blank file silently yields `""`.
2. `_render_plan` mis-keys observations (an id of `None`/missing collapses to the
   key `"None"`, and duplicate ids overwrite), so one step's result can be shown
   for another.
3. Rendered tool results, the user request, and history are spliced into prompts
   unbounded.
4. `_NAME_BRACED_JSON_RE` matches any identifier followed by `{`, so prose (or a
   non-JSON brace body) that follows a known tool name is hijacked into a tool
   call with empty args.
5. The re-ask loop appends `{"role": "assistant", "content": ""}` for a
   content-less completion, which some providers reject.

## 2. Non-goals

- No change to `SYSTEM_PROMPT` / `PLANNER_PROMPT` / `SUMMARY_INSTRUCTION` text,
  the planner tool schemas, approval logic, or `build_messages`.
- No new configuration settings; the caps are module-level constants.
- No frontend, DB, websocket, or agent-executor changes.
- The `[tool name]`, fenced-JSON, `<tool_call>` and `Tool result (...)` recovery
  paths are unchanged.

## 3. Approved decisions (locked)

- **D1 `load_soul`** — add `from functools import lru_cache`; private
  `@lru_cache(maxsize=8) def _read_soul(path: Path) -> str` reads with
  `encoding="utf-8"`, catches `(OSError, UnicodeDecodeError)` returning
  `DEFAULT_SOUL`, otherwise returns `text.strip() or DEFAULT_SOUL`;
  `load_soul(path: str | Path | None = None)` maps `Path(path) if path else
  _SOUL_PATH` and delegates. Docstring notes the fallback and the memoisation.
- **D2 `_render_plan`** — build the result map in one `enumerate(observations)`
  loop: `continue` on a non-dict observation or a non-dict/`None` `step`; key =
  `str(step.get("id") or f"s{index}")`; store only when `result is not None` and
  the key is absent (first wins). In the plan loop use `enumerate(plan.get("steps",
  []))` and look up `str(step.get("id") or f"s{index}")`.
- **D3 size caps** — module constants `_MAX_RESULT_CHARS = 2000`,
  `_MAX_USER_CHARS = 8000`, `_MAX_HISTORY_CHARS = 12000`,
  `_MAX_PLAN_BODY_CHARS = 16000`; helper
  `_truncate(text: str, limit: int) -> str` returns `text` when
  `len(text) <= limit`, else `text[:limit // 2] + f"\n… (truncated
  {len(text) - limit} chars) …\n" + text[-(limit - limit // 2):]`. Apply
  `_truncate(str(result), _MAX_RESULT_CHARS)` per rendered result;
  `_truncate(user_text, _MAX_USER_CHARS)` in both builders; and
  `_truncate(body, _MAX_PLAN_BODY_CHARS)` in `build_summary_messages`. Add
  `_trim_history(history: list[dict], max_chars: int) -> list[dict]` in
  `build_planner_messages` that drops the oldest messages while the summed
  `len(str(content))` exceeds the budget, always keeping the last message (and
  `_truncate`-ing that one if oversized).
- **D4 router** — in `_recover_textual_plan_args`, a `_NAME_BRACED_JSON_RE`
  candidate is appended only when `name in known_tools or name in
  _BARE_TOOL_NAMES`, the balanced body is found, and `json.loads(body)` yields a
  `dict`; otherwise skip it.
- **D5 router re-ask** — replace
  `messages.append({"role": "assistant", "content": content or ""})` with
  `if content: messages.append({"role": "assistant", "content": content})` so no
  empty assistant turn is appended.

## 4. Data model / interfaces

None. No schema, event, or API change. Public signatures
(`load_soul`, `build_planner_messages`, `build_summary_messages`) are unchanged;
`_render_plan`, `_truncate`, `_trim_history`, `_read_soul` stay module-private.

## 5. Parts

1. `src/llm/prompt.py` — D1 (`load_soul` / `_read_soul`), D3 (constants +
   `_truncate` + `_trim_history`, applied in both builders), D2 (`_render_plan`).
2. `src/llm/router.py` — D4 (parse-gated, name-filtered name-braced candidates),
   D5 (non-empty assistant re-ask append).
3. Tests — `tests/test_prompt.py`, `tests/test_router.py` (see §6).

## 6. Tests

Add to `tests/test_prompt.py`:

- `test_load_soul_falls_back_on_invalid_utf8` — write raw bytes
  (`b"\xff\xfe\x00"`) to a temp file; `load_soul(path) == DEFAULT_SOUL`.
- `test_load_soul_uses_default_for_blank_file` — write `"  \n\t"`; expect
  `DEFAULT_SOUL`.
- `test_render_plan_ignores_non_dict_and_none_step_entries` — observations
  containing `None`, `{}`, `{"step": None, "result": "x"}` and
  `{"step": {"id": "a"}, "result": "ok"}` must not raise and must not attribute
  `"x"`/`"None"` to a step.
- `test_render_plan_prefers_first_result_for_duplicate_ids` — two observations
  with the same id show only the first result.
- `test_render_plan_truncates_long_result` — a result of `5 * 2000` chars renders
  with `"truncated"` in the output and shorter than the raw result.
- `test_build_summary_messages_truncates_long_user_text` — a very long
  `user_text` is bounded.

Add to `tests/test_router.py`:

- `test_route_unwrapped_invalid_json_body_not_executed` — `_text_response(
  "core_list_tools {oops}")` returns plain text (no plan executed, calls == 1).
- `test_route_prose_with_known_name_and_brace_not_executed` — `_text_response(
  "I could call core_health { maybe }")` returns plain text.
- `test_route_reask_skips_empty_assistant_content` — queue a malformed tool-call
  reply with empty text then a valid reply; assert the messages sent on the
  second call contain no assistant message with empty content.

Keep all existing `tests/test_prompt.py` and `tests/test_router.py` tests green
(the unwrapped `agent_plan {json}`, `reply { "text": ... }` and
`core_list_tools {}` recovery tests must still pass).

Gates:

```
uv run ruff check .
uv run pytest tests/test_prompt.py tests/test_router.py -q
uv run pytest -m "not integration and not e2e" -q
```

## 7. Acceptance criteria

- A blank or non-UTF-8 `SOUL.md` yields `DEFAULT_SOUL` with no exception; a normal
  `SOUL.md` still loads (contains "Loom" and "voice").
- `_render_plan` tolerates non-dict/`None` observations and steps, never collapses
  distinct id-less steps onto one key, and shows the first result for duplicate ids.
- Long results, `user_text`, the summary body, and history are bounded with an
  explicit `… (truncated N chars) …` marker.
- A known tool name followed by non-JSON braces in prose is not executed; a valid
  `name {json}` (e.g. `core_list_tools {}`) still is.
- The re-ask loop never appends an empty-content assistant message.

## 8. Commits

Suggested (coordinator commits only after acceptance — RULES 0a):

1. `fix(llm): harden soul loading, plan rendering, and prompt size caps`
2. `fix(llm): require parseable textual tool calls and skip empty re-ask turns`

## 9. Risks

- Truncation can drop detail from very large tool outputs; head/tail plus a marker
  keeps both ends and `SUMMARY_INSTRUCTION` already forbids pasting raw output.
  Caps are generous, so ordinary turns are unaffected.
- Requiring `json.loads` for name-braced candidates may stop recovering a model
  that emits near-JSON; such bodies previously degraded to `{}` and produced a
  hollow call, so falling back to plain text is strictly safer.
- `_trim_history` changes what the planner sees in very long sessions; it keeps
  the most recent messages and the budget is high.
