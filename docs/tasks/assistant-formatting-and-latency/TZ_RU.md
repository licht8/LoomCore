# TZ_RU.md — assistant-formatting-and-latency

## 1. Goal

Two related fixes for the assistant:

1. **Formatting.** Tool output and summaries are rendered as a wall of plain
   text (e.g. the tool list, the plugin list, a list of WireGuard peers). The
   chat already renders GitHub-flavored Markdown (`frontend/components/markdown.tsx`
   uses `react-markdown` + `remark-gfm`), so the fix is on the **content** side:
   tool `text` outputs and the summary/planner prompts must produce Markdown
   (headings, bullet lists, bold labels), not comma-joined prose.
2. **Latency.** Keep the existing LLM-driven flow (the model decides *which*
   tools and *how many*, then answers in its own words) and make it faster:
   run independent read-only plan steps concurrently instead of strictly
   serially, and keep the planner on a fast model.

Explicit invariant (owner): **no deterministic shortcut that answers a user
question by calling a tool directly, bypassing the LLM.** The assistant must
always plan via the model, execute the chosen tools, and then describe the
result in its own words. (The meta fast-path removed in `87a4110` must not
return.)

## 2. Non-goals

- No frontend renderer change: `react-markdown`/`remark-gfm` already supports
  everything needed. Do not add a Markdown library or rewrite `markdown.tsx`.
- No deterministic intent→tool shortcut, no keyword router, no regex fast-path
  in `route`.
- No change to the plan/approval safety model, `WS_SCHEMA_VERSION`, the WebSocket
  contract, or the DB schema.
- Do not remove streaming, token caps, `planner_model`, the planner recovery
  (D3/D4 of the previous task), or the `core_list_tools` optional `plugin`
  filter.
- No new LLM call per turn.

## 3. Approved decisions (locked)

**D1 — Markdown output contract in the prompts.**
- `src/llm/prompt.py` `SUMMARY_INSTRUCTION`: require Markdown — short paragraphs,
  `-` bullet lists for any enumeration, `**bold**` for labels/keys, no raw
  JSON/tables unless the data is tabular. Keep the existing "preserve every
  number/status exactly" rule.
- `src/llm/prompt.py` `PLANNER_PROMPT`: add one line — when the plan produces a
  list (tools, plugins, peers, processes, services), the final answer must be a
  Markdown bullet list, never a comma-joined sentence.
- `src/llm/SOUL.md`: add a short "Formatting" section (see D2).

**D2 — `src/llm/SOUL.md` formatting rules (the voice).**
Add an explicit section, e.g.:
```
## Formatting
- Reply in Markdown: use `-` bullet lists for any enumeration (tools, plugins,
  peers, processes, services), `**bold**` for labels, and short headings when it
  helps.
- Never dump a comma-separated wall of text. One item per line.
- Keep the admin's numbers and statuses exactly as the tools reported them.
- Prefer a short lead-in sentence, then the list.
```

**D3 — Tool `text` outputs are Markdown (core tools, then plugins).**
Keep every `_result(...)` `data` payload byte-for-byte identical (programmatic
consumers depend on it); only change the human-readable `text`.
- `src/core/core_tools.py`:
  - `_core_list_tools`: replace the comma-joined `core_line` and the
    `"{name} ({n}): " + ", ".join(names)` lines with Markdown:
    `"**Core tools** ({n}):\n" + "\n".join(f"- `{name}`" for name in core_tools)`
    and, per plugin, `f"**{name}** ({n}):\n" + "\n".join(f"- `{name}`" ...)`.
    Keep the `(disabled)` / `(unavailable)` markers as their own bullet lines.
  - `_core_health`: replace `" Plugins enabled: a, b, c."` with a `**Plugins
    enabled** (n):` heading followed by `- name` bullets (or `- none`).
  - `_core_plugin_status`: keep the one-line summary, render `Warnings:` as a
    bullet list.
  - `_core_list_plugins` already emits `- ` bullets; leave it (verify).
- **Plugins (LoomWG peers/status).** Audit `plugins/LoomWG` (and LoomOps if it
  formats lists) and convert any comma-joined/prose enumeration of peers,
  interfaces, or services into Markdown bullet lists in the tool `text`,
  keeping the `data` payload unchanged. Follow the plugin's existing formatting
  helpers; do not change tool names, args, or risk.

**D4 — Execute independent read-only plan steps concurrently.**
Today `execute_plan` (`src/core/agent.py:231-300`) replays steps strictly
serially. Let the planner mark independent steps so the executor can run them
together.
- `src/core/agent.py` `AGENT_PLAN_SCHEMA`: add an optional `parallel: boolean`
  (default `false`) to each step's properties. `build_plan` preserves it as
  `step["parallel"]` (default `False`).
- `src/core/agent.py` `execute_plan`: split `steps` into runs of **consecutive**
  steps where every step is `risk == "safe"` **and** `parallel is True`; each
  such run executes concurrently with `asyncio.gather` (per-step `emit`
  `started`/`done` still fires, results collected in plan order); every other
  step (non-safe, or `parallel` not set) executes alone, serially, exactly as
  today. Preserve the current semantics: a step whose `risk != "safe"` and the
  plan is not `approved` is blocked and stops the plan; the first failed step
  stops the plan (cancel/await the rest of its run); final `plan["status"]` is
  `done`/`failed` as before. `dispatch` is already async and per-step safe.
- `src/llm/prompt.py` `PLANNER_PROMPT`: instruct the planner to set
  `"parallel": true` on read-only steps that do not depend on each other's
  output (e.g. several independent status checks), and to leave it off when a
  later step needs an earlier step's result. Add one example.
- Safety: never parallelize a `moderate`/`dangerous` step; a run containing any
  non-safe step degrades to serial.

**D5 — Keep the planner fast (no code change, documented lever).**
`planner_model` already exists end-to-end (env `LLM_PLANNER_MODEL`, settings
file, Settings tab). Document in `docs/architecture/AGENT.md` (and `.env.example`
comment) that setting a small/fast `planner_model` is the primary latency lever
for planning, while the main model keeps quality for summaries. Do not add a new
config field.

## 4. Data model / interfaces

- No DB/migration, no WS/API contract change.
- Plan step dicts gain an optional `"parallel": bool` (default `False`); the plan
  JSON stored in `messages.tool_calls` and the plan card remain backward
  compatible (extra optional field).
- `AGENT_PLAN_SCHEMA` step properties gain `parallel`.
- Tool `text` strings change to Markdown; `data` payloads are unchanged.

## 5. Parts

1. **`src/llm/SOUL.md`** — D2 formatting section.
2. **`src/llm/prompt.py`** — D1 (SUMMARY_INSTRUCTION + PLANNER_PROMPT Markdown
   rules) and D4 (planner `parallel` guidance + example).
3. **`src/core/core_tools.py`** — D3 Markdown `text` for `_core_list_tools`,
   `_core_health`, `_core_plugin_status` (verify `_core_list_plugins`).
4. **`plugins/LoomWG`** (and LoomOps if applicable) — D3 Markdown bullet lists
   for peer/interface/service enumerations; `data` unchanged.
5. **`src/core/agent.py`** — D4 `AGENT_PLAN_SCHEMA` `parallel` + `build_plan`
   default + `execute_plan` concurrent runs.
6. **`docs/architecture/AGENT.md`**, **`.env.example`** — D5 note.
7. **Tests** — §6.
8. **`CHANGELOG.md`** — short dated entry (RULES 14).

## 6. Tests

Fast gate: `uv run ruff check .`; `uv run pytest -m "not integration and not e2e" -q`.
If frontend is untouched, no `tsc`/`build` needed (but the renderer must stay
compatible — it already is).

- **`tests/test_agent.py`** (add)
  - `test_execute_plan_runs_parallel_safe_steps_concurrently`: three safe steps
    with `parallel: true`, a dispatch that awaits an `asyncio.Event`/sleep;
    assert total wall time < serial sum and all `done`.
  - `test_execute_plan_serial_when_parallel_unset`: unchanged serial order.
  - `test_execute_plan_does_not_parallelize_unsafe_steps`: a `moderate` step in a
    `parallel` run degrades to serial and still requires `approved`.
  - `test_execute_plan_stops_on_parallel_failure`: one step in a run raises;
    assert the plan is `failed` and later steps did not run.
- **`tests/test_core_tools.py`** (add)
  - `test_core_list_tools_text_is_markdown`: `text` contains `- \`name\`` bullets
    and no `", ".join`-style single line; `data.core_tools`/`data.plugin_tools`
    unchanged.
  - `test_core_health_text_lists_plugins_as_bullets`.
- **`tests/test_prompt.py`** (add)
  - `test_summary_instruction_requires_markdown` and
    `test_planner_prompt_mentions_parallel_steps`.
- **`tests/test_router.py`** (regression, no new shortcut)
  - `test_route_has_no_meta_fast_path`: a tool-listing utterance still goes
    through the planner (the planner call is made); assert no direct
    `dispatch_core_tool` bypass.
- **`plugins/LoomWG` tests** (extend the existing plugin tests)
  - peer/interface listing `text` is a Markdown bullet list; `data` unchanged.

## 7. Acceptance criteria

- `uv run ruff check .` clean; `uv run pytest -m "not integration and not e2e"`
  green.
- "show me the tools" / "покажи инструменты" and "list plugins" / "список
  плагинов" are answered by the **planner** (an LLM call happens) and rendered as
  Markdown bullet lists, not a comma-joined paragraph.
- The WireGuard peer list and similar enumerations render as Markdown bullets.
- A multi-step plan of independent read-only steps runs concurrently (timing
  shows a wall-clock saving; per-step tool cards still appear in order).
- Non-safe steps are never parallelized and still require approval; a failed step
  still stops the plan.
- No deterministic tool-answer shortcut exists anywhere in `route` (regression
  test passes); streaming, caps, `planner_model`, and the `core_list_tools`
  filter are intact.

## 8. Commits

Coordinator commits after acceptance (RULES 0a). Suggested messages:
- `docs(llm): require Markdown tool output and answers`
- `feat(core): render core tool output as Markdown`
- `feat(plugins): render LoomWG enumerations as Markdown`
- `perf(agent): run independent read-only plan steps concurrently`
- `docs(agent): document planner_model as the latency lever`

## 9. Risks

- **Planner over-marking `parallel`** on dependent steps would break ordering.
  Mitigation: parallel is opt-in, only for `safe` steps, runs are limited to
  consecutive marked steps, and the prompt states the dependency rule; a
  mis-marked dependent step is a planner-quality issue, not a safety issue.
- **Concurrency vs plugin serialization**: a plugin serializes calls internally
  (`_PluginLock`), so two parallel steps hitting the same plugin won't speed up
  and may queue; the gain is across different plugins. Note this in the docs.
- **Markdown in `text`** could affect the summary input; the summary instruction
  already says to preserve numbers, and Markdown bullets are easy to summarize.
  `data` payloads are untouched, so no programmatic consumer breaks.
- **Plugin formatting churn**: keep changes to `text` only; do not touch tool
  names, args, schemas, or risk, or plugin tests/consumers may break.
- **Wall-of-text root cause**: if some tool still returns prose, the SOUL rule
  lets the summary reformat it — but for a *direct* plan (no summary) the raw
  text is shown, so the tool's own `text` must be Markdown (D3).
