# Agent — Conversational-First Loop

LoomCore's assistant is a real agent, not a reactive tool-caller: the language
model never calls a real tool directly. It must call exactly one planner tool
each turn — `agent_plan` to act, or `reply` for plain conversation — and a
deterministic executor in code decides what actually runs. The planner call uses
`tool_choice="required"`, so an unstructured prose turn cannot slip through and
silently skip a tool the user asked for.

The default is conversation. Plans are the exception.

## Modes

- **Conversation** (default) — greetings, questions, explanations, and
  clarifying questions. The model calls `reply` with the text to show the user.
- **Direct** — a single, obvious action ("show disk usage", "list running
  processes"). The model calls `agent_plan` with `mode: "direct"` and exactly
  one step. Code runs it without showing a plan card.
- **Plan** — multi-step work, an explicit request for a plan, or genuinely
  ambiguous scope. The model calls `agent_plan` with `mode: "plan"`; the user
  sees a plan card and approves, edits, or rejects it.

The shape is clamped in code, not left to the model: a declared `plan` always
wins, and any plan with more than one step is forced to `plan`. A single step
with no declared mode becomes `direct`. See `build_plan` / `_effective_mode` in
`src/core/agent.py`.

`validate_plan` also checks each step against the tool's `inputSchema`: a step
that omits a required argument (for example `core_disable_plugin` without
`plugin`) is rejected and the planner is re-asked, so a call that could only fail
never reaches execution. The same schema is enforced again at dispatch time —
plugin tools in `execute_tool_by_name`, core meta-tools in `dispatch_core_tool` —
so an edited or hand-crafted plan cannot slip past the planner. See
`src/core/contracts.py`.

## Flow

1. **Understand** — the planner LLM is offered exactly two callable tools,
   `agent_plan` and `reply`; the real tool catalog is passed as context, never as
   callable functions. The call is forced (`tool_choice="required"`). A question
   about the current state of the system, or any request to act, must go through
   `agent_plan`; `reply` is only for conversation. A read question is never
   answered from memory: the answer is whatever the read tool returns this turn.
2. **Classify** — every step's `risk` is set in code: a tool's **declared** risk
   (from its `inputSchema` entry, or `CORE_TOOLS_SCHEMA` for core tools) always
   wins; otherwise `classify_tool_risk` (`src/core/security.py`) falls back to its
   name heuristic. Values: `safe`, `moderate`, or `dangerous`.
3. **Confirm**:
   - direct + safe — runs immediately, no card;
   - direct + non-safe — the assistant asks in plain text ("... Reply yes to
     proceed.") and waits for a short confirmation;
   - plan + all safe — runs immediately and shows the plan card;
   - plan + any non-safe — waits for approve / edit / reject.
4. **Execute** — the executor replays the stored steps verbatim: the approved
   `tool` and `args`, in order. It never re-asks between steps and never
   paraphrases arguments. Consecutive `safe` steps marked `parallel: true` run
   concurrently (results are collected in plan order); every other step runs
   serially, and non-safe steps still require approval. A failed step stops the
   plan.
5. **Summarize** — every tool-backed turn, direct actions included, gets a second
   LLM call voiced by `src/llm/SOUL.md`. It answers in its own words using only
   the tool facts: it must not paste raw tables/JSON, but must copy status words
   and numbers verbatim (e.g. a health scan's `0 critical, 1 warning, 0 info`).
   The raw tool output is shown only when the user explicitly asks for it
   ("show the raw output", "list every row"), when the summary call fails, or
   when the summary changes a reported count or status.

## Plain-text confirmation

Direct actions that are not safe are confirmed conversationally, not with
buttons. `src/core/confirmations.py` parses the user's reply:

- Only a short message (three words or fewer) that is *entirely* an approved
  phrase counts. "yes" approves; "да, но сначала логи" is `unclear`.
- Approve and reject phrase lists live in `src/core/confirmations.json`, not in
  code. Override the path with `CONFIRMATIONS_FILE`.
- A pending confirmation is bound to the plan id, the session, and a 300-second
  timeout. An expired or unclear reply is treated as a new message (with one
  re-ask for the first unclear reply), so the agent never hangs waiting.

The result is code, not prompt: a reply only approves when `parse_confirmation`
returns `approve`, and that routes into the same approval path used by
`plan_approve`.

## Plan object

A plan proposed in plan mode is persisted in `messages.tool_calls` JSONB under
`role="plan"` (no migration needed):

```json
{
  "type": "plan",
  "plan_id": "pl_...",
  "revision": 1,
  "goal": "One line: what the plan achieves",
  "steps": [
    {"id": "s1", "tool": "ops_disk_usage", "args": {}, "why": "...",
     "risk": "safe", "parallel": false, "status": "pending"}
  ],
  "mode": "plan",
  "status": "proposed",
  "created_at": "2025-01-01T00:00:00+00:00"
}
```

- Step status: `pending`, `running`, `done`, `failed`, `blocked`.
- Plan status: `proposed`, `approved`, `running`, `done`, `failed`, `rejected`,
  `cancelled`.
- Editing keeps the `plan_id`, bumps `revision`, and re-classifies every step
  (`next_revision`). A different goal is a new plan. A failed step stops the
  plan; there is no automatic re-planning.
- Direct actions run without a plan card, so they are not persisted as a plan.
- A step may set `parallel: true` (only safe, read-only steps that do not depend
  on another step's output). The executor groups **consecutive** such steps and
  runs each group concurrently; a run is never created from a non-safe step, so
  approval and ordering semantics are unchanged. Because a plugin serializes its
  own calls (`_PluginLock`), the wall-clock gain comes from steps that hit
  different plugins.

## Safety invariant

A non-safe step runs only when the plan status is `approved`. This is enforced in
`execute_plan`, not by the prompt: the planner shapes quality, but code decides
what runs. Direct-dangerous actions set `approved` only after a clean text
confirmation.

## Events and actions

- Every server-to-client event is registered in `src/interfaces/events.py`
  (`WS_SCHEMA_VERSION = 1`) and carries `schema_version`; the registry mirrors the
  frontend `WsMessage` union field-for-field and is **additive-only**.
- Server to client: `plan_proposed`, `plan_step`, `plan_done` for plan mode;
  `tool_call`, `tool_response`, and `response` for both modes.
- The planner's `reply` text and the plan summary stream as `delta` events
  (`message_id`, `content`, optional `reset`); the final `response` carries the
  same `message_id` so the client reconciles the streamed bubble. A `reset`
  delta (emitted before a planner re-ask) replaces the partial text.
- The `agent_plan` goal streams as `plan_progress` events (`message_id`,
  `content`, optional `reset`) — a transient "Planning: …" status line, never an
  assistant message. It is cleared when a tool card or the final `response`
  arrives, so the assistant bubble still appears only after the tools run.
- Client to server: `plan_approve`, `plan_edit`, `plan_reject` for plan cards.
- Direct confirmation is an ordinary text message; the server parses it.
- On reload, plan revisions are collapsed by `plan_id` (last one wins).

## Latency

- **Planner model** is the primary planning-latency lever. `planner_model` (env
  `LLM_PLANNER_MODEL`, the settings file, or the Settings tab) routes the planner
  call to a smaller/faster model while the main model keeps quality for
  summaries. Empty means the main model is used for both.
- Independent read-only steps marked `parallel: true` execute concurrently
  (see Execute and Plan object above), so a multi-step status check costs one
  round of latency instead of the sum of its steps.
- The planner and summary calls are bounded by `LLM_PLANNER_MAX_TOKENS` /
  `LLM_SUMMARY_MAX_TOKENS` and streamed as `delta` events. The summary explains
  the result in the assistant's own words, and `LLM_SUMMARY_DISABLE_THINKING=1`
  (default) makes the summary always send `enable_thinking=false` so a reasoning
  model does not think before the own-words reply, even without
  `LLM_THINKING_TEMPLATE=1`; a provider that rejects the flag is retried once
  without it, and the planner still honors `LLM_ENABLE_THINKING`.
- The summary call is **unbounded by default** (`LLM_SUMMARY_TIMEOUT=0`): it uses
  the normal read timeout and retries, so a slow "thinking" model still produces
  the full explanation. When a positive `LLM_SUMMARY_TIMEOUT` is set, the summary
  is capped to a single attempt; if it then times out, returns nothing, or
  contradicts the observed numbers, the assistant posts a short deterministic
  **digest** (`_digest_summary`: the goal plus one line per step with a capped
  excerpt) instead of the raw tool dump.
- **Every plan is summarized in the assistant's own words**: report-ness is
  declarative — core meta-tools set `report: true`, plugins declare `report` in
  their `TOOLS_SCHEMA` (read into `plugin_manager`), so the core never hardcodes
  plugin tool names. Report tools only control *rendering*: their cards render as
  Markdown (`format: "markdown"`), everything else as `<pre>`. In the UI the tool
  card body is collapsed by default (click to expand; a failed tool auto-expands),
  so the feed leads with the assistant's message. The raw output is
  surfaced (and the card rendered compact) only when the user explicitly asks for
  it, or for an allow-listed read-only command (`LOOMCORE_REPORT_COMMANDS`, empty
  by default). If a configured `summary_model` fails for a non-timeout reason,
  the summary is retried once with the main model.
- Intended defaults are `LLM_READ_TIMEOUT=30`, `LLM_MAX_RETRIES=1`,
  `LLM_PLANNER_MAX_TOKENS=1024`, `LLM_SUMMARY_MAX_TOKENS=512`,
  `LLM_SUMMARY_TIMEOUT=0`, `LLM_SUMMARY_DISABLE_THINKING=1`; the Settings UI must
  not silently re-override them to `90`/`2`. A fast `LLM_SUMMARY_MODEL` is the
  main lever for the post-tool wait.
- **Long-conversation memory**: the planner prompt keeps only the newest
  `_MAX_HISTORY_CHARS` (6000) of verbatim history, so in a long session the start
  of the conversation used to vanish. Once the history of a session grows past
  `MEMORY_COMPACT_AFTER_CHARS` (12000 chars, `src/core/memory.py`), everything
  older than the last `MEMORY_KEEP_CHARS` (6000) of verbatim tail is folded into a
  short factual brief stored on the session (`sessions.memory`, plus the
  `memory_updated_at` coverage marker = the newest folded message) and injected as
  a *condensed* system block ahead of the tail. Compaction runs in the background
  **after** the turn (it never delays the plan or the reply), at most one refresh
  per session at a time, deduplicated by the coverage marker so messages are never
  folded twice, and reuses the summary settings (`LLM_SUMMARY_MODEL`,
  `LLM_SUMMARY_MAX_TOKENS`, `LLM_SUMMARY_TIMEOUT`,
  `LLM_SUMMARY_DISABLE_THINKING`) under `purpose="memory"`. A short session is
  byte-identical to before; any failure leaves the stored memory and the turn
  untouched.

## Files

| File | Responsibility |
| --- | --- |
| `src/core/agent.py` | Plan model, mode clamp, validation, executor; `agent_plan` + `reply` schemas |
| `src/core/confirmations.py` | Plain-text confirmation parser |
| `src/core/confirmations.json` | Approve/reject phrase lists |
| `src/llm/prompt.py` | Planner and summary message builders, SOUL loader |
| `src/llm/SOUL.md` | Voice for the summary call (never planning) |
| `src/llm/router.py` | Planner call, `agent_plan` parsing, summary |
| `src/interfaces/websocket.py` | Direct run, plan events, approval, execution |
| `src/interfaces/events.py` | Versioned WebSocket event registry (`schema_version`) |
| `src/interfaces/tool_events.py` | Payloads and persistence |
| `src/core/contracts.py` | Tool argument/result validation (`jsonschema`) |
| `src/core/memory.py` | Long-conversation memory: fold the older history into a per-session brief |
| `src/core/security.py` | `classify_tool_risk` (heuristic fallback) |

## Non-goals

- No autonomous multi-turn loop; v1 stops at each step and on failure.
- Parallel execution is opt-in, safe-only, and limited to consecutive steps; a
  non-safe step always runs alone.
- No cross-session or user-level memory: the condensed brief is per session and
  never shared between sessions.
- SOUL never influences planning or safety.
