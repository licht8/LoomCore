# TZ_RU.md — Always summarize answers in the assistant's own words

## 1. Goal

The assistant must always answer in its own words, describing the tool output in
human text instead of pasting it verbatim. Today a plan whose steps are all
"report" tools skips the post-run summary and echoes the raw Markdown
(`_plan_answers_with_raw`), and a failed/empty summary call silently falls back to
the raw text (`_summarize`). Both produce wall-of-raw output.

## 2. Non-goals

- No changes to the plan/approval model, tool risk, or the WS protocol.
- No frontend change (the `format`/`compact` fields and rendering already exist).
- The opt-in read-only command shortcut (`LOOMCORE_REPORT_COMMANDS`) is kept.
- Report tools keep their declaration (`report: true`) — it now only selects
  Markdown card rendering.

## 3. Approved decisions (locked)

- **D1** Report tools no longer skip the summary. `_step_answers_with_raw`
  (`src/interfaces/websocket.py`) drops the `_is_report_tool(...)` short-circuit;
  only an allow-listed read-only `ops_run_command` (via `_report_arg_for` +
  `_command_is_read_only`) still answers with raw output. As a result
  `_plan_answers_with_raw` is `raw_requested or <read-only command>` and
  `compact` is only true for those cases. Tool cards show their body (non-compact)
  and `format` stays `"markdown"` for report tools.
- **D2** A configured `summary_model` must never degrade the answer to raw
  silently. `AIRouter.summarize_plan` (`src/llm/router.py`) retries once with the
  main model (`model=None`) when the summary-model call raises or returns an empty
  string, logging a warning each time; with `on_delta` set it emits a
  `reset=True` delta before the retry so the client replaces the partial text.
- **D3** Docs updated to match (AGENT.md, PLUGIN_MANIFEST.md, LoomWG.md,
  LoomOps.md) and a CHANGELOG entry added.

## 4. Data model / interfaces

No schema/interface change. `report: true` still drives `_is_report_tool`, which
now only selects `tool_format = "markdown"`. `report_arg` still drives the opt-in
read-only command shortcut.

## 5. Parts

1. `src/interfaces/websocket.py` — D1 (`_step_answers_with_raw`, `_summarize`
   docstring).
2. `src/llm/router.py` — D2 (`summarize_plan` retry + warning logs).
3. Docs + CHANGELOG — D3.

## 6. Tests

- `tests/test_websocket.py`: report tools no longer answer with raw
  (`test_step_answers_with_raw_ignores_report_tools`); a direct report plan is
  summarized (`test_direct_report_action_is_summarized`); an all-report plan is
  summarized (`test_summarize_runs_for_all_report_plan`); a report turn emits a
  non-compact Markdown card (`test_report_turn_emits_non_compact_markdown_cards`);
  the read-only command shortcut still applies.
- `tests/test_router.py`: the summary-model fallback (add a case where the first
  call raises/empties and the main-model retry returns text).

Gates: `uv run ruff check .`; `uv run pytest -m "not integration and not e2e" -q`.

## 7. Acceptance criteria

- Listing/report turns are answered with an own-words summary (no verbatim echo),
  except when the user explicitly asks for the raw output or an allow-listed
  read-only command was run.
- A broken `summary_model` no longer yields raw output when the main model works.
- Gates green; no frontend change needed.

## 8. Commits

- `fix(core): always summarize tool output in the assistant's own words`
  (`src/interfaces/websocket.py`, tests, docs, CHANGELOG, this TZ).
- `fix(llm): retry the summary with the main model when the summary model fails`
  (`src/llm/router.py`, `tests/test_router.py`).

## 9. Risks

- One extra LLM call on every listing/report turn (the report-tool fast-path is
  reverted by owner request); latency returns to the pre-optimization level for
  those turns.
- The read-only command shortcut remains opt-in and off by default.
- A mid-stream summary-model failure may briefly show partial text before the
  reset/final response reconciles the bubble.
