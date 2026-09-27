# TZ_RU.md — assistant-perceived-latency

## 1. Goal

Make the assistant feel faster and, where possible, actually faster, in three
linked changes:

1. **Planning progress feedback (perceived latency).** After
   `assistant-tools-before-message` removed goal streaming, a plan turn shows
   only the fixed `thinking` spinner while the planner generates (often several
   seconds) before any tool card appears. Re-introduce the plan **goal** as a
   *transient progress* signal — a new additive `plan_progress` event — that is
   **not** an assistant message, so the user sees what is being planned without
   a bubble appearing before the tools run.
2. **Declarative report tools (correctness + enabler).** Replace the
   hand-maintained `_REPORT_TOOLS` frozenset in `src/core/core_tools.py` (which
   hardcodes LoomWG/LoomOps tool names inside the core, against RULES 1) with a
   per-tool `report` declaration in each plugin's `TOOLS_SCHEMA`, read into the
   plugin-manager cache. Core meta-tools declare `report: true` in their own
   schema.
3. **Skip the summary for allow-listed read-only commands (real latency).**
   Extend report-ness to a plugin-declared `report_arg` (e.g. `ops_run_command`
   on its `command` argument): when the command's first token is in a
   core-configured read-only allow-list (`LOOMCORE_REPORT_COMMANDS`, opt-in,
   empty by default), the step is treated as a report, so the turn skips the
   summary LLM call and the card renders compact. This removes an entire LLM
   round trip for frequent read-only commands (`uptime`, `df -h`, `free -m`).

## 2. Non-goals

- No change to approval/risk semantics, tool execution, or the confirmation
  flow. `report` / `report_arg` affect only (a) whether the post-run summary LLM
  call happens and (b) card compaction/format — never whether a tool runs or
  needs approval.
- No `WS_SCHEMA_VERSION` bump: `plan_progress` is an additive event.
- Do **not** reintroduce a deterministic intent→tool shortcut (the meta
  fast-path removed in `87a4110` stays removed). The LLM still plans, executes
  the chosen tools and writes the final message.
- Not planner-**content** streaming (streaming the plan's step list into a live
  plan card) — only the goal text is surfaced, as progress.
- No change to `reply` streaming.
- No UI redesign: one status line is enough.
- No DB schema change.

## 3. Approved decisions (locked)

### D1 — `plan_progress` WebSocket event (additive)

- `src/interfaces/events.py`: add
  `class PlanProgressEvent(_Event)` with
  `type: Literal["plan_progress"] = "plan_progress"`,
  `message_id: str | None = None`, `content: str | None = None`,
  `reset: bool = False`; register `"plan_progress"` in `EVENT_MODELS`; add
  builder `plan_progress_payload(message_id, content, reset=False)`.
- Semantics mirror `delta`: `reset=True` replaces the accumulated text;
  `reset=False` appends. No `WS_SCHEMA_VERSION` change.

### D2 — Router forwards the plan goal to `on_progress`

- `src/llm/router.py`: extend the signature to
  `route(self, user_message, session_id, *, on_delta=None, on_progress=None)`.
- Restore `class _GoalTextStream(_ToolFieldStream)` with
  `super().__init__(AGENT_PLAN_TOOL, "goal")`.
- Build `goal_stream = _GoalTextStream() if on_progress is not None else None`
  next to `reply_stream = _ReplyTextStream() if on_delta is not None else None`.
- On a re-ask (`attempt > 0`), reset every non-`None` stream and send its reset:
  `await on_delta("", reset=True)` and `await on_progress("", reset=True)`.
- `_make_reply_streamer(reply_stream, goal_stream, on_delta, on_progress)` takes
  optional streams/callbacks; it forwards a `reply` fragment to `on_delta` and an
  `agent_plan` `goal` fragment to `on_progress`, and returns `True` when **either**
  forwarded (so `stream_chat_completion` treats visible progress as emitted and
  does not retry).
- The streamed planner call passes
  `on_tool_delta=_make_reply_streamer(reply_stream, goal_stream, on_delta, on_progress)`.
- `summarize_plan` is unchanged (`on_delta` only; no goal).

### D3 — WebSocket wiring + UI

- `src/interfaces/websocket.py` `_process_user_message`: build
  `async def on_progress(fragment, *, reset=False): await send(plan_progress_payload(message_id, fragment, reset=reset))`
  and pass it to `route(..., on_delta=on_delta, on_progress=on_progress)`.
- `frontend/lib/chat-types.ts`: add the union member
  `{ type: "plan_progress"; message_id?: string; content?: string; reset?: boolean }`
  and add `planProgress: string` to `UseChatState`.
- `frontend/lib/use-chat.ts`: initialise `planProgress: ""`; return it.
- `frontend/lib/chat-ws.ts`: `case "plan_progress"` sets `planProgress` (replace
  with `msg.content ?? ""` when `msg.reset === true`, else concatenate); clear
  `planProgress` to `""` on `tool_call`, `response`, `plan_done`, `error`,
  `cancelled`, `busy`.
- UI: while `isThinking` and `planProgress` is non-empty, render a dimmed status
  line `Planning: {planProgress}` above the composer (`chat-input.tsx` and/or
  `chat-room.tsx`). It is cleared as soon as a tool card or the final response
  arrives, so it never coexists with the assistant bubble.

### D4 — Declarative `report` in the plugin tool schema

- `docs/architecture/PLUGIN_MANIFEST.md` "Tool declarations": document the new
  optional per-tool `report` (boolean; `true` = a listing/report whose raw output
  already is the answer) and `report_arg` (D5), alongside `risk`/`internal`.
- `src/plugins/manager.py`:
  - add `self._tool_report: dict[str, str]` (value `"always"` for
    `report: true`, otherwise the `report_arg` name);
  - `_index_tools`: record it — `True` → `"always"`, elif a non-empty `report_arg`
    string → that name;
  - `_rebuild_tool_cache`: remove this plugin's stale `_tool_report` entries in the
    same loop that clears `_tool_schemas` / `_tool_risk`;
  - add `def report_kind(self, tool_name: str) -> str: return self._tool_report.get(tool_name, "")`.
- `src/core/core_tools.py`:
  - add `"report": True` to `core_health`, `core_list_plugins`,
    `core_plugin_status`, `core_list_tools`, `core_capabilities`;
  - derive `_REPORT_TOOLS` from `CORE_TOOLS_SCHEMA` (the entries with
    `report: True`) and **drop the hardcoded LoomWG/LoomOps names**;
  - `_is_report_tool(name)` = `name in _REPORT_TOOLS or plugin_manager.report_kind(name) != ""`.
  - `data` payloads are unchanged.
- `plugins/LoomWG/LoomWG/schemas.py`: add a `_declare_report(tools, report)` helper
  next to `_declare_risk` (validates declared names exist — no stale entries; it
  does **not** require every tool to be declared). Set `report: true` on
  `wg_list_interfaces`, `wg_list_peers`, `wg_interface_status`,
  `wg_show_interface_config`, `wg_stats`, `wg_diagnostics`.
- `plugins/LoomOps/LoomOps/schemas.py`: set `report: true` on `ops_list_processes`,
  `ops_failed_units`, `ops_scan_health`, `ops_network_status`, `ops_dmesg`,
  `ops_disk_usage`, `ops_system_metrics`.

### D5 — `report_arg` + read-only command allow-list

- New optional tool key `report_arg: "<arg>"`: the tool is a report **iff** the
  named argument's value is a read-only command. LoomOps declares
  `ops_run_command` with `"report_arg": "command"`.
- `src/core/core_tools.py`: add
  `def _report_arg_for(name: str) -> str` returning the `report_arg` name (via
  `plugin_manager.report_kind(name)`), or `""` for `"always"`/undeclared.
- `src/interfaces/websocket.py`:
  - `def _read_only_commands() -> frozenset[str]`: parse `settings.report_commands`
    (comma and/or whitespace separated).
  - `def _command_is_read_only(command: str) -> bool`: reject the command if it
    contains any shell metacharacter from `; | & > < $ \` ( ) newline` (so
    `uptime; rm -rf /` is never classified read-only); otherwise compare the
    `os.path.basename` of the **first** whitespace-separated token against the
    allow-list.
  - `def _step_answers_with_raw(step) -> bool`:
    `_is_report_tool(name)` → True; else if `_report_arg_for(name)` is set and
    `step["args"]` holds that key as a string → `_command_is_read_only(value)`;
    else False.
  - `_plan_is_all_reports(plan)` (rename to `_plan_steps_answer_with_raw` if
    preferred) = `bool(steps) and all(_step_answers_with_raw(s) for s in steps)`.
  - `_plan_answers_with_raw` and `_uses_llm_summary` keep their current logic and
    now consult the extended predicate. `tool_format` stays
    `"markdown" if _is_report_tool(tool) else "text"`, so an `ops_run_command`
    report renders as `<pre>` (`text`), which is correct for stdout.
- `src/config.py`: add
  `report_commands: str = Field(default="", validation_alias="LOOMCORE_REPORT_COMMANDS")`
  (same alias pattern as `timing_enabled`/`LOOMCORE_TIMING`). `.env.example`
  documents it: empty default = no change; a conservative example
  `uptime,df,free,uname,hostname,date,whoami,id,ps,ss`.

## 4. Data model / interfaces

- New WS event `plan_progress` (`message_id`, `content`, `reset`) — additive, no
  schema bump. TS `WsMessage` member + `UseChatState.planProgress: string`.
- New plugin tool-schema keys `report: bool` and `report_arg: str`, documented in
  `PLUGIN_MANIFEST.md` and read only by the plugin manager.
- New setting `LOOMCORE_REPORT_COMMANDS` (empty default; comma/space separated).
- No DB change.

## 5. Parts

1. **Progress event + router + WS** — `src/interfaces/events.py`,
   `src/llm/router.py`, `src/interfaces/websocket.py`.
2. **Progress UI** — `frontend/lib/chat-types.ts`, `frontend/lib/use-chat.ts`,
   `frontend/lib/chat-ws.ts`, `frontend/components/chat-input.tsx` (and/or
   `chat-room.tsx`).
3. **Declarative report metadata** — `src/plugins/manager.py`,
   `src/core/core_tools.py`, `plugins/LoomWG/LoomWG/schemas.py`,
   `plugins/LoomOps/LoomOps/schemas.py`, `docs/architecture/PLUGIN_MANIFEST.md`.
4. **Read-only command skip** — `src/config.py`, `src/interfaces/websocket.py`,
   `.env.example`.
5. **Docs/changelog** — `docs/architecture/AGENT.md`, `docs/plugins/LoomWG.md`,
   `docs/plugins/LoomOps.md`, `CHANGELOG.md`.

## 6. Tests

Gate (must pass): `uv run ruff check .`,
`uv run pytest -m "not integration and not e2e"`,
`npx tsc --noEmit`, `npm run build`.

- `tests/test_events.py`: `plan_progress_payload` shape (type, message_id,
  content, `reset`; `exclude_none` drops unset fields).
- `tests/test_router.py`: a streamed `agent_plan` with a `goal` forwards goal
  fragments to `on_progress` and **never** to `on_delta`; a streamed `reply`
  still forwards to `on_delta`; a re-ask calls `on_progress("", reset=True)` and
  `on_delta("", reset=True)`; with `on_progress=None` no goal is forwarded.
- `tests/test_plugin_manager.py`: `report_kind` returns `"always"` for
  `report: true` and the arg name for `report_arg`; the entry is cleared when the
  plugin is rebuilt/dropped.
- `tests/test_core_tools.py`: `_is_report_tool` is true for a core report tool and
  for a plugin tool the (fake) manager reports; false otherwise.
- `tests/test_websocket.py`: `_step_answers_with_raw` true for a report tool and
  for `ops_run_command` with an allow-listed command, false for a non-listed or
  shell-chained command; `_uses_llm_summary` false for an all-read-only-command
  plan and true for a mixed report+command plan.
- LoomWG/LoomOps plugin schema tests: the listed report tools carry
  `report: true`; `ops_run_command` carries `report_arg: "command"`.
- `tests/test_config.py`: `LOOMCORE_REPORT_COMMANDS` parses into `report_commands`.

## 7. Acceptance criteria

- On a plan turn, `plan_progress` events carry the growing goal; the assistant
  bubble still appears only with the post-tools `response` (no plan `delta`).
- A `reply` turn still streams `delta` fragments.
- A read-only allow-listed `ops_run_command` turn performs **no summary LLM
  call** — verified with `LOOMCORE_TIMING=1` (no `summary` stage) and/or no
  `purpose="summary"` row in `llm_usage` — and its card is compact with
  `<pre>` text.
- With `LOOMCORE_REPORT_COMMANDS` empty, a command turn still gets a summary
  (behavior unchanged).
- The core no longer hardcodes LoomWG/LoomOps tool names; report tools are
  declared in the plugins and reach the core through `tools/list`.
- `uv run ruff check .` clean; `uv run pytest -m "not integration and not e2e"`
  green; `npx tsc --noEmit` clean; `npm run build` succeeds.

## 8. Commits

Coordinator commits after acceptance (RULES 0a). Suggested, one per part so any
can be reverted alone:

- `feat(interfaces): stream the plan goal as a transient progress signal`
- `feat(plugins): declarative report tools replace the hardcoded report set`
- `feat(agent): skip the summary for allow-listed read-only commands`

## 9. Risks

- **Progress text vs the final plan.** On a re-ask the goal can change; mitigated
  by resetting the progress stream per attempt and clearing it when a tool
  starts.
- **Retry suppression.** Forwarding goal progress returns `True` from
  `on_tool_delta`, so the client will not retry once progress is shown; this is
  intended (the goal is user-visible).
- **Command heuristic.** The read-only test is best-effort: it ships opt-in
  (empty default), rejects shell metacharacters, and only affects summarization,
  never execution or approval. A misclassification at worst omits a summary.
- **Plugin-declared report flags.** A plugin that forgets a `report` flag
  regresses to "summarize" (the safe default), not to incorrect execution.
- **Mixed plans** (report + command) still summarize and render non-compact —
  the accepted v1 trade-off from the report-tools task, unchanged here.
- **Scope**: Part 1 is frontend+protocol, Parts 3-4 backend; land them as separate
  commits.
