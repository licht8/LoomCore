# TZ_RU.md — assistant-report-tools-and-compact-cards

## 1. Goal

Fix three owner-visible problems in one focused change:

1. **Duplicated output** — a tool turn currently renders the raw tool output
   twice: once in the tool card and again in the assistant bubble (because a
   direct plan skips the summary and returns the raw output, while the tool
   card also carries the full output).
2. **Broken formatting** — the tool card renders the raw tool `text` in a
   monospace `<pre>` block, so Markdown tool output (the `**Core tools**` /
   bullet lists added recently) shows as literal text; and `_core_list_tools`
   glues the next section header onto the last bullet line.
3. **Latency** — every report/list turn pays a second LLM call (the summary)
   that adds nothing when the tool output already is the answer.

Plus a documentation and measurement deliverable:

4. **Settings defaults** are recorded in docs (so the UI/ops do not silently
   re-override them).
5. **Measurement is a hard gate with before/after** — the "after" run must show
   **no summary LLM call for report tools**.

## 2. Non-goals

- **No planner-content streaming** — that is about *perceived* latency and has a
  different risk surface (WS + frontend + the streamer). It is a separate TZ,
  `streaming-1`.
- No read-only-command allow-list (fragile; future).
- No plugin-manifest `report` flag (the curated heuristic ships first).
- No `WS_SCHEMA_VERSION` bump; the WS contract stays additive-only.
- No change to the plan/approval safety model, tool risk, `agent_plan` schema,
  or `execute_plan` semantics.
- No change to the REST `POST /api/chat` path.

## 3. Approved decisions (locked)

**D1 — "One copy of the output" (format + compact cards).**
The raw tool output appears exactly once:
- **Report tools** (listings/reports) -> no summary; the **assistant bubble**
  shows the raw (Markdown) output; the **tool card is compact** (command +
  `Done`, no body).
- **Command/action tools** -> the summary runs (own words); the **tool card
  shows the raw output**; the bubble shows the summary.

Changes:
- `src/interfaces/events.py`: `ToolResponseEvent` gains optional
  `format: Literal["markdown", "text"] = "text"` and `compact: bool = False`.
  `tool_success_payload(tool_name, content, risk, artifacts, *, format="text",
  compact=False)` and `tool_error_payload(tool_name, message, risk, *,
  format="text", compact=False)` pass them through. Additive only — **do not**
  bump `WS_SCHEMA_VERSION`.
- `src/interfaces/tool_events.py`: `save_tool_message(...)` gains keyword-only
  `format="text"` and `compact=False` and persists them in the `tool_calls`
  entry (`{name, arguments, status, summary, risk?, artifacts?, format,
  compact}`) so a reload renders identically to the live turn.
- `src/interfaces/websocket.py` `_run_plan`:
  - Compute `answers_with_raw = _plan_answers_with_raw(plan, raw_requested=...)`
    **before** `execute_plan` (it depends only on the plan's steps and the raw
    request, both known up front).
  - In `emit` (the `done`/`failed` branches) pass
    `compact=answers_with_raw` and
    `format="markdown" if _is_report_tool(step["tool"]) else "text"` to
    `_save_tool_message(...)` and to `tool_success_payload(...)` /
    `tool_error_payload(...)`.
- `frontend/components/chat-message.tsx` tool card: if `message.compact` ->
  render the header only (no body); else if `message.format === "markdown"` ->
  render with the existing `<Markdown>`; otherwise the current `<pre>`.
- `frontend/lib/chat-types.ts`: the tool message variant gains optional
  `format?: "markdown" | "text"` and `compact?: boolean`.
- `frontend/lib/chat-hydrate.ts` (and the `tool_response` case in
  `frontend/lib/chat-ws.ts`): carry `format`/`compact` from the WS payload /
  persisted entry onto the tool card message.

**Explicit accepted trade-off v1:** a **mixed plan** (report + command steps)
sets `uses_llm_summary = True`, so **all** its cards are non-compact and the
summary runs. Splitting compactness per-step within a mixed plan is out of
scope for v1.

**D2 — Report-tool classification + summary skip (the main latency win).**
- New `_is_report_tool(name: str) -> bool` in `src/core/core_tools.py`: a
  curated exact-name frozenset (v1), covering
  - core: `core_health`, `core_list_plugins`, `core_plugin_status`,
    `core_list_tools`, `core_capabilities`;
  - LoomWG: `wg_list_interfaces`, `wg_list_peers`, `wg_interface_status`,
    `wg_show_interface_config`, `wg_stats`, `wg_diagnostics`;
  - LoomOps: `ops_list_processes`, `ops_failed_units`, `ops_scan_health`,
    `ops_network_status`, `ops_dmesg`, `ops_disk_usage`, `ops_system_metrics`.
  Principle: **a table/list of facts -> report tool** (raw output, no summary);
  **a single value or short status needing interpretation -> command tool**
  (own-words summary).
- `src/interfaces/websocket.py`:
  ```python
  def _plan_is_all_reports(plan: dict) -> bool:
      steps = plan.get("steps", [])
      return bool(steps) and all(
          _is_report_tool(s.get("tool", "")) for s in steps
      )

  def _plan_answers_with_raw(plan: dict, *, raw_requested: bool) -> bool:
      return raw_requested or _plan_is_all_reports(plan)

  def _uses_llm_summary(plan, observations, *, raw_requested) -> bool:
      return bool(observations) and not _plan_answers_with_raw(
          plan, raw_requested=raw_requested
      )
  ```
  This **replaces** the previous `... and not plan_is_direct(plan)` rule: a
  direct **command** plan now summarizes again (own words), while a direct (or
  multi-step) **all-report** plan skips the summary and answers with the raw
  output.
- `_run_plan` uses `_plan_answers_with_raw(...)` for `compact` (D1) and
  `_uses_llm_summary(...)` for the summary decision, so the two never disagree.

**D3 — Formatting robustness.**
`src/core/core_tools.py` `_core_list_tools`: join the section blocks with a
blank line (`"\n\n"` between the `**Core tools**` block and each
`**<plugin>**` block) so even a plain (non-Markdown) rendering is clean. No
change to the `data` payload.

**D4 — Settings-defaults documentation.**
Record the intended defaults in `.env.example`, `docs/setup/DEPLOY.md`, and
`docs/architecture/AGENT.md`: `LLM_READ_TIMEOUT=30`, `LLM_MAX_RETRIES=1`,
`LLM_PLANNER_MAX_TOKENS=1024`, `LLM_SUMMARY_MAX_TOKENS=512`,
`LLM_PLANNER_MODEL=` / `LLM_SUMMARY_MODEL=` (empty = main model; a smaller
model is recommended for both). State that the Settings UI must not silently
re-override these to `90`/`2`.

**D5 — Measurement hard gate (before/after).**
With `LOOMCORE_TIMING=1`, run one "list processes" turn and capture
`llm_usage.latency_ms` grouped by `purpose` (`planner` / `summary` / tools)
**before** and **after** the change. Acceptance requires the **after** run to
show **no `purpose="summary"` call for a report-tool turn** (and the same
number of planner calls).

## 4. Data model / interfaces

- No DB/migration change.
- WS (additive): `ToolResponseEvent.format`, `ToolResponseEvent.compact`;
  the persisted `tool_calls` tool entry gains `format` / `compact`.
- New `src/core/core_tools.py` `_is_report_tool(name)`.
- `src/interfaces/tool_events.py` `save_tool_message(..., *, format, compact)`.
- New websocket helpers `_plan_is_all_reports`, `_plan_answers_with_raw`;
  updated `_uses_llm_summary`.

## 5. Parts

1. **`src/interfaces/events.py`** — D1: `format`/`compact` on
   `ToolResponseEvent` + payload builders.
2. **`src/interfaces/tool_events.py`** — D1: persist `format`/`compact`.
3. **`src/core/core_tools.py`** — D2 `_is_report_tool`; D3 blank-line join.
4. **`src/interfaces/websocket.py`** — D1/D2: compute `answers_with_raw` before
   execution, pass `compact`/`format`, replace the summary rule.
5. **`frontend/lib/chat-types.ts`, `frontend/lib/chat-ws.ts`,
   `frontend/lib/chat-hydrate.ts`, `frontend/components/chat-message.tsx`** —
   D1: carry and render `format`/`compact`.
6. **`.env.example`, `docs/setup/DEPLOY.md`, `docs/architecture/AGENT.md`** —
   D4.
7. **Tests** — §6.
8. **`CHANGELOG.md`** — short dated entry (RULES 14).

## 6. Tests

Gates: `uv run ruff check .`; `uv run pytest -m "not integration and not e2e"`.
Frontend: `npx tsc --noEmit` and `npm run build` in `frontend/`.

- **`tests/test_events.py`** (add)
  - `test_tool_response_payload_format_and_compact`: `tool_success_payload(...,
    format="markdown", compact=True)` serializes `format` and `compact`; the
    defaults (`"text"`, `False`) are omitted or present as documented.
- **`tests/test_core_tools.py`** (add)
  - `test_is_report_tool_membership`: the curated names return `True`;
    representative command tools (`ops_run_command`, `wg_create_peer`,
    `core_enable_plugin`) return `False`.
  - `test_core_list_tools_blank_line_between_sections`: the text contains
    `\n\n**<plugin>**` (blank line, not a glued header).
- **`tests/test_websocket.py`** (add/replace)
  - Replace `test_summarize_skips_llm_for_direct_plan` with
    `test_summarize_skips_llm_for_all_report_plan`: an all-report plan with
    observations does **not** await `summarize_plan` and returns the raw output.
  - `test_summarize_runs_for_direct_command_plan`: a direct plan whose single
    step is a command tool **does** await `summarize_plan`.
  - `test_mixed_plan_uses_llm_summary_and_non_compact_cards`: a report+command
    plan -> `uses_llm_summary is True`, and the emitted `tool_response` events
    have `compact=False`.
  - `test_report_turn_emits_compact_cards`: an all-report plan emits
    `tool_response` with `compact=True` and `format="markdown"`.
  - `test_command_turn_emits_text_format`: a command tool emits
    `format="text"`, `compact=False`.
- **`tests/test_prompt.py`** — unaffected assertions still pass (no prompt
  change in this TZ); keep the suite green.

## 7. Acceptance criteria

- `uv run ruff check .` clean; `uv run pytest -m "not integration and not e2e"`
  green; `npx tsc --noEmit` clean and `npm run build` succeeds in `frontend/`.
- A report-tool turn ("show me tools", "list peers", "list processes") renders
  the output **once** (assistant bubble), with a **compact** tool card, and
  makes **no** `purpose="summary"` LLM call.
- A command-tool turn still gets an own-words summary, and its tool card shows
  the raw output exactly once.
- A mixed (report + command) plan summarizes and renders all cards
  non-compact (accepted trade-off v1).
- Tool-card Markdown output renders as Markdown; command stdout stays `<pre>`.
- **Measurement hard gate**: with `LOOMCORE_TIMING=1`, the before/after
  `llm_usage` capture for a "list processes" turn shows the summary call
  present **before** and **absent after**, with an unchanged planner call.
- No frontend protocol break; `WS_SCHEMA_VERSION` unchanged; reloaded history
  renders compact/format identically to the live turn.

## 8. Commits

Coordinator commits after acceptance (RULES 0a). Suggested messages:
- `feat(interfaces): mark report tool cards compact and carry a format flag`
- `perf(agent): skip the summary for all-report plans`
- `fix(core): separate tool-list sections with a blank line`
- `docs: record the intended LLM defaults`
- `feat(frontend): render tool cards as markdown or compact`

## 9. Risks

- **Report-tool set is curated (v1)**: a mis-classified tool either echoes raw
  (too terse) or summarizes a table (extra call). The frozenset is the single
  place to edit; a manifest `report` flag is the follow-up.
- **Mixed plans stay non-compact** (accepted): a report step inside a mixed plan
  is summarized; revisit if it proves noisy.
- **`compact` persisted in history**: an older tool entry without the fields
  must default to `format="text"`, `compact=False` (the frontend must tolerate
  missing fields).
- **Direct command tools now summarize** (+1 LLM call each): intended
  ("own words"); frequent read-only `ops_run_command` calls remain the main
  per-turn cost until the (out-of-scope) allow-list lands.
- **No frontend test runner**: the card renderer and hydration are verified by
  `tsc`/`build` and manual e2e (one report turn, one command turn, one mixed
  plan, plus a reload).
