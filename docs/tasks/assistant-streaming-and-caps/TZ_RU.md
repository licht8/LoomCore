# TZ_RU.md — assistant-streaming-and-caps

## 1. Goal

Make the assistant's answer appear incrementally instead of after a long silent
wait, and bound the worst-case generation time. The owner-visible symptom:
"output still takes very long, sometimes 3+ minutes".

The turn currently waits for the **entire** non-streaming generation of the
planner and (for multi-step plans) the summary. There is no `stream`, no
`max_tokens`, and the planner prompt is large every turn. This task:

1. Caps planner and summary output length (`max_tokens`), so no single call can
   generate unbounded text.
2. Trims the planner prompt (history/user budgets, tool-description length) to
   cut prefill / time-to-first-token.
3. Streams the **summary** text to the UI as it is generated.
4. Streams the **reply** text (the planner's `reply` tool argument) as it is
   generated.

The WebSocket contract is extended **additively** (new `delta` event, optional
`message_id` on `response`); the frontend reconciles the streamed message on the
final `response`.

## 2. Non-goals

- No change to the plan/approval safety model, the planner's forced
  `tool_choice="required"`, the `agent_plan` schema, tool risk classification,
  or the re-ask limit.
- No change to the DB schema or migrations.
- No plugin changes.
- No streaming of `tool_call` / `tool_response` / plan events (tool cards stay
  as they are).
- No change to the REST `POST /api/chat` path: it stays non-streaming (it calls
  `route()` without a delta callback).
- Do not remove the non-streaming `chat_completion`; both paths coexist.

## 3. Approved decisions (locked)

**D1 — `max_tokens` caps (configurable via the LLM settings path).**
- `src/llm/client.py` `chat_completion`: add a **keyword-only**
  `*, max_tokens: int | None = None` and include `"max_tokens": max_tokens` in
  the payload only when it is not `None`. It must be keyword-only because the
  existing callers pass `messages, tools, ...` positionally — adding a positional
  parameter would shift them.
- `src/config.py` `Settings`: add `llm_planner_max_tokens: int = 1024` and
  `llm_summary_max_tokens: int = 512`.
- `src/llm/config.py` `LlmConfig`: add `planner_max_tokens: int = 1024` and
  `summary_max_tokens: int = 512`; `default_config()` maps them from
  `settings.llm_planner_max_tokens` / `settings.llm_summary_max_tokens`;
  `load_config()` reads them with the same **key-present** fallback used for
  `summary_model` (absent key → `base.*`) and coerces to a positive int —
  non-numeric or `<= 0` falls back to `base.*` (a `max_tokens: 0` would produce
  empty completions). Add a small `_as_positive_int(value, default)` helper next
  to `_as_int`.
- `src/llm/router.py`: read the caps from the **active config**, not module
  constants — `config = get_active()`; pass `max_tokens=config.planner_max_tokens`
  to every planner call and `max_tokens=config.summary_max_tokens` to the summary
  call. If module constants are kept they are only the defaults; the config value
  always wins.
- `src/api/llm.py`: expose the two fields like `summary_model` so they are
  live-tunable from the Settings tab — add `planner_max_tokens: int | None = None`
  and `summary_max_tokens: int | None = None` to `LlmSettingsUpdate`, to
  `_serialize`, and to `_merge`
  (`planner_max_tokens=_unchanged(data, "planner_max_tokens", current.planner_max_tokens)`,
  `summary_max_tokens=_unchanged(data, "summary_max_tokens", current.summary_max_tokens)`),
  plus a validation that a supplied value is `> 0` (HTTP 400 otherwise, mirroring
  the timeout check).
- `frontend`: add the two numeric fields to the LLM settings form
  (`frontend/lib/llm-settings-api.ts` type + `frontend/components/settings-view.tsx`)
  mirroring the existing `summary_model` field.
- `.env.example`: document `LLM_PLANNER_MAX_TOKENS=1024` /
  `LLM_SUMMARY_MAX_TOKENS=512`.
- `docker-compose.yml`: `LLM_PLANNER_MAX_TOKENS: ${LLM_PLANNER_MAX_TOKENS:-1024}`
  and `LLM_SUMMARY_MAX_TOKENS: ${LLM_SUMMARY_MAX_TOKENS:-512}`.
- The caps assume `enable_thinking` is off (see the previous task's D6). A
  reasoning model spends part of the cap on hidden reasoning tokens; with
  thinking on, raise the caps (env, settings file, or the Settings tab) — no code
  change needed (documented in §9).

**D2 — Planner prompt trim.**
- `src/llm/prompt.py`: change `_MAX_HISTORY_CHARS = 12000` → `6000` and
  `_MAX_USER_CHARS = 8000` → `4000`.
- `src/llm/router.py` `_catalog_text`: truncate each tool description to a new
  constant `_MAX_TOOL_DESC_CHARS = 160` (append `…` when truncated) so a verbose
  plugin description cannot bloat the system prompt.

**D3 — Stream the summary.**
- `src/llm/client.py`: add
  `async def stream_chat_completion(self, messages, *, model=None, tools=None,
  tool_choice=None, temperature=None, max_tokens=None, purpose="chat",
  session_id=None, on_content=None, on_tool_delta=None) -> dict`.
  - Builds the same payload as `chat_completion` plus `"stream": True` and
    `"stream_options": {"include_usage": True}`.
  - POSTs with `client.stream("POST", url, ...)`, iterates
    `response.aiter_lines()`, parses `data: ` SSE lines (ignore `[DONE]`).
  - For each chunk: `delta = choices[0]["delta"]`. If `delta.get("content")`,
    append it to the accumulated content and `await on_content(fragment)` when
    `on_content` is set. If `delta.get("tool_calls")`, accumulate per
    `index` the `function.name` and concatenated `function.arguments`, and
    `await on_tool_delta(name_so_far, args_so_far)` when `on_tool_delta` is set.
    Capture `usage` from the last chunk that carries it.
  - Returns a dict shaped like the non-streaming response:
    `{"choices": [{"message": {"content": ..., "tool_calls": [...]}}], "usage": {...}}`.
  - Retries: reuse the existing attempt/retry/budget logic, but **only retry
    while nothing has been emitted yet** (`emitted` flag). After the first
    emitted delta, an error is recorded and raised (retrying would duplicate
    text). Log `log_stage("llm_call", ..., stream=True, ...)` and
    `record_llm_usage` as today.
  - Non-SSE fallback: some OpenAI-compatible gateways ignore `stream: true` and
    return a single JSON body. If no `data:` line was parsed but the body parses
    as a JSON object with `choices`, treat it as a normal non-streaming response
    (return it as-is; do not yield deltas).
  - Timeout semantics: with `client.stream(...)`, `read_timeout` applies **per
    chunk**, not to the whole generation, so the wall-clock bound for a streamed
    call now rests on `max_tokens`. The non-streaming `chat_completion` — and
    therefore the REST `POST /api/chat` path — keeps the old total-response
    `read_timeout` behavior.
- `src/llm/router.py` `summarize_plan`: add keyword-only `on_delta=None`. When
  set, call `client.stream_chat_completion(messages,
  model=config.summary_model or None, max_tokens=config.summary_max_tokens,
  purpose="summary", on_content=on_delta)`; otherwise the existing
  `chat_completion(..., max_tokens=config.summary_max_tokens)`. Parse content the
  same way in both branches.

**D4 — Stream the reply.**
- `src/llm/router.py` `route`: add keyword-only `on_delta=None`. When set, the
  planner call uses `stream_chat_completion(..., on_tool_delta=...)`; otherwise
  the existing `chat_completion`. Both pass
  `max_tokens=config.planner_max_tokens` (read from the active config).
- Add a `_ReplyTextStream` helper + `_make_reply_streamer(stream, on_delta)`
  factory in `router.py` that extracts the `reply` tool's `text` argument
  incrementally and forwards only newly decoded characters to `on_delta`:
  ```python
  _REPLY_TEXT_KEY_RE = re.compile(r'"text"\s*:\s*"')

  def _decode_json_string_prefix(raw: str, start: int) -> tuple[str, int]:
      """Decode a JSON string body from ``start`` (after the opening quote).

      Returns (decoded, end_index); end_index is the closing-quote index, or
      len(raw) while the string is still open. A trailing partial escape is left
      undecoded for the next call.
      """
      simple = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\",
                "/": "/", "b": "\b", "f": "\f"}
      out: list[str] = []
      i, n = start, len(raw)
      while i < n:
          ch = raw[i]
          if ch == '"':
              return "".join(out), i
          if ch != "\\":
              out.append(ch)
              i += 1
              continue
          if i + 1 >= n:
              break
          esc = raw[i + 1]
          if esc in simple:
              out.append(simple[esc])
              i += 2
          elif esc == "u":
              if i + 6 > n:
                  break
              try:
                  out.append(chr(int(raw[i + 2 : i + 6], 16)))
              except ValueError:
                  out.append("\\u" + raw[i + 2 : i + 6])
              i += 6
          else:
              out.append(esc)
              i += 2
      return "".join(out), n

  class _ReplyTextStream:
      """Incrementally extract the ``text`` argument of a streamed ``reply``."""

      def __init__(self) -> None:
          self._active = False
          self._emitted = 0
          self._done = False

      def reset(self) -> None:
          """Discard state so a re-ask starts from a clean slate."""
          self._active = False
          self._emitted = 0
          self._done = False

      def feed(self, name: str | None, args_so_far: str) -> str:
          if name is not None:
              self._active = name == REPLY_TOOL
          if not self._active or self._done:
              return ""
          match = _REPLY_TEXT_KEY_RE.search(args_so_far)
          if match is None:
              return ""
          decoded, end = _decode_json_string_prefix(args_so_far, match.end())
          if end < len(args_so_far):
              self._done = True
          delta = decoded[self._emitted :]
          self._emitted = len(decoded)
          return delta

  def _make_reply_streamer(stream, on_delta):
      async def _on_tool_delta(name, args_so_far) -> None:
          fragment = stream.feed(name, args_so_far)
          if fragment:
              await on_delta(fragment)

      return _on_tool_delta
  ```
  Only the `reply` tool's text is streamed; an `agent_plan` stream emits
  nothing.
- **Re-ask safety (required).** `route` can call the planner up to
  `PLANNER_REASK_LIMIT + 1` times, and a reply that streams partial text can
  still validate as empty/bare and trigger a re-ask (`router.py:252-258`,
  `291-326`). Create the streamer **once per `route`** and reset it — and tell
  the UI to reset the bubble — at the start of **every** planner attempt:
  ```python
  reply_stream = _ReplyTextStream() if on_delta is not None else None
  for attempt in range(PLANNER_REASK_LIMIT + 1):
      if on_delta is not None:
          reply_stream.reset()
          await on_delta("", reset=True)
      ...
      # inside the attempt:
      on_tool_delta = _make_reply_streamer(reply_stream, on_delta)
  ```
  The `on_delta` contract is therefore
  `Callable[[str], Awaitable[None]]` invoked as `await on_delta(fragment)` for
  text and `await on_delta("", reset=True)` at each attempt start; the WS
  closure forwards `reset` into `delta_payload`. A reset delta replaces the
  streamed content instead of appending (D7), so a re-ask never concatenates two
  attempts' text into one bubble.

**D5 — WebSocket events (additive).**
- `src/interfaces/events.py`: add
  ```python
  class DeltaEvent(_Event):
      type: Literal["delta"] = "delta"
      message_id: str | None = None
      content: str | None = None
      reset: bool = False
  ```
  register `"delta": DeltaEvent` in `EVENT_MODELS`, and add
  `def delta_payload(message_id: str, content: str, reset: bool = False) -> dict:
      return event_payload(DeltaEvent(message_id=message_id, content=content, reset=reset))`.
  A `reset=True` delta means "replace this message's content" (a re-ask started);
  `reset=False` means "append this fragment".
- Add an optional `message_id: str | None = None` field to `ResponseEvent` and
  to `response_payload(content, text=None, message_id=None)`.
- Adding a new `type` and optional fields is additive; **do not** bump
  `WS_SCHEMA_VERSION`.

**D6 — Wire the delta stream into the turn.**
- `src/interfaces/websocket.py`:
  - Import `delta_payload` from `src.interfaces.events`.
  - `_process_user_message`: before calling `route`, create
    `message_id = f"msg_{uuid4().hex}"`. Build
    `async def on_delta(fragment, *, reset=False): await send(delta_payload(message_id, fragment, reset=reset))`.
    Call `airouter_with_plugins.route(user_text, session_id, on_delta=on_delta)`.
    For a `text` result, send `response_payload(content, message_id=message_id)`.
  - `_run_plan`: create `message_id = f"msg_{uuid4().hex}"`; pass an `on_delta`
    closure to `_summarize(...)`; on the final summary send
    `response_payload(summary, message_id=message_id)`. Non-streamed summaries
    (raw/fallback) simply produce no deltas — the same `message_id` still lets
    the frontend append normally. The summary path has no re-ask, so its
    `on_delta` is only ever called with `reset=False`.
  - `_summarize`: add keyword-only `on_delta=None` and forward it to
    `summarize_plan(...)`; only forward when the LLM path is actually taken.
- `src/api/chat.py` is unchanged (no `on_delta`).

**D7 — Frontend (additive, reconciled rendering).**
- `frontend/lib/chat-types.ts`: add
  `| { type: "delta"; message_id?: string; content?: string; reset?: boolean }`
  to `WsMessage`, and add `message_id?: string` to the `response` variant.
- `frontend/lib/chat-hydrate.ts`: add two helpers next to `finalizePendingTool`:
  - `appendAssistantDelta(messages, messageId, fragment, reset = false)` — find
    the message by `id === messageId`; if found, return a copy whose `content` is
    **replaced** by `fragment` when `reset` is true, otherwise concatenated; if
    not found, append a new assistant message
    `{ id: messageId, role: "assistant", content: fragment }`.
  - `finalizeAssistant(messages, messageId, content)` — if a message with that
    id exists, replace its `content`; else append a new assistant message.
- `frontend/lib/chat-ws.ts` `applyWsMessage`:
  - `case "delta"`: if `!msg.message_id`, return `state` unchanged (defensive —
  the server always sets it); else
  `messages: appendAssistantDelta(state.messages, msg.message_id, msg.content ?? "", msg.reset === true)`,
  `isThinking: true`, `error: null`.
  - `case "response"`: if `msg.message_id` is set, use
    `finalizeAssistant(state.messages, msg.message_id, msg.content || msg.text || "")`;
    else keep the current append-new behavior. Clear `isThinking`/`sending` as
    today.
- `frontend/lib/use-chat.ts`: no change (the reducer is the single choke point).
- The streaming message uses a stable `message_id` as the React key, so the
  bubble updates in place; `Markdown` re-renders per delta (acceptable).

## 4. Data model / interfaces

- No DB/migration change.
- New `OpenAICompatClient.stream_chat_completion(...)`.
- New WS event `delta` (`message_id`, `content`, `reset`); `response` gains
  optional `message_id`. Contract stays additive (`src/interfaces/events.py`
  docstring).
- New config fields `planner_max_tokens` / `summary_max_tokens` (`Settings`,
  `LlmConfig`, `LLM_PLANNER_MAX_TOKENS` / `LLM_SUMMARY_MAX_TOKENS`, and the
  Settings API/form); new prompt budget values; new `_MAX_TOOL_DESC_CHARS`.
- `route(..., *, on_delta=None)` and `summarize_plan(..., *, on_delta=None)`,
  where `on_delta` is `Callable[[str], Awaitable[None]]` with an optional
  `reset` keyword.

## 5. Parts

1. **`src/llm/client.py`** — D1 (`max_tokens`) and D3
   (`stream_chat_completion`).
2. **`src/config.py`, `src/llm/config.py`, `src/api/llm.py`,
   `frontend/lib/llm-settings-api.ts`, `frontend/components/settings-view.tsx`**
   — D1 configurable caps (`llm_planner_max_tokens` / `llm_summary_max_tokens`,
   `_as_positive_int`, API + form fields).
3. **`src/llm/prompt.py`** — D2 budget changes.
4. **`src/llm/router.py`** — D1 config-driven caps, D2 catalog cap, D3
   `summarize_plan(on_delta)`, D4 `route(on_delta)` + `_ReplyTextStream`
   (incl. `reset()`) / `_decode_json_string_prefix` / `_make_reply_streamer` +
   the per-attempt reset emission.
5. **`src/interfaces/events.py`** — D5 `DeltaEvent` (incl. `reset`),
   `delta_payload`, `response_payload(message_id)`.
6. **`src/interfaces/websocket.py`** — D6 message ids + `on_delta` wiring.
7. **`frontend/lib/chat-types.ts`, `chat-hydrate.ts`, `chat-ws.ts`** — D7.
8. **Tests** — §6.
9. **`CHANGELOG.md`** — short dated entry (RULES 14).

## 6. Tests

Fast gate: `pytest -m "not integration and not e2e"` and `ruff check .`.
Frontend: `npx tsc --noEmit` and `npm run build` (in `frontend/`).

- **`tests/test_llm_client.py`** (add)
  - `test_stream_chat_completion_emits_content_deltas`: a fake httpx stream
    yielding `data:` chunks with content fragments; assert `on_content` receives
    each fragment in order and the returned dict's `content` is the concatenation.
  - `test_stream_chat_completion_accumulates_tool_args`: chunks with
    `tool_calls` fragments; assert `on_tool_delta` sees growing `arguments` and
    the returned `tool_calls[0].function.arguments` is the full string.
  - `test_stream_does_not_retry_after_first_delta`: fail mid-stream after a
    delta; assert no retry and an error is raised.
  - `test_stream_retries_before_first_delta`: fail before any delta; assert one
    retry then success.
  - `test_stream_non_sse_json_fallback`: the gateway returns a single JSON body
    (no `data:` lines); assert no deltas are emitted and the parsed content is
    returned.
- **`tests/test_router.py`** (add)
  - `test_reply_text_stream_decodes_fragments`: unit-test
    `_ReplyTextStream.feed` across split fragments (including `\"`, `\n`, a
    split `\uXXXX`) and assert the concatenated output equals the decoded text.
  - `test_reply_text_stream_ignores_agent_plan`: `feed("agent_plan", ...)`
    returns `""`.
  - `test_reply_text_stream_reset_restarts`: feed a partial reply, `reset()`,
    feed a new reply; assert the second output starts from the beginning (no
    truncation of its prefix).
  - `test_route_streams_reply_via_on_delta`: a fake
    `stream_chat_completion` that drives `on_tool_delta` with a `reply` call;
    assert `on_delta` got the text and the result is `type == "text"`.
  - `test_route_resets_stream_on_reask`: attempt 1 streams a partial reply that
    validates as bare; assert `on_delta("", reset=True)` was called before
    attempt 2's deltas.
  - `test_summarize_plan_streams_when_on_delta`: `summarize_plan(..., on_delta=...)`
    uses the streaming client and forwards text.
  - `test_route_passes_configured_max_tokens`: with a patched active config
    (`planner_max_tokens=333`, `summary_max_tokens=111`), the planner call
    receives `max_tokens=333` and the summary call `max_tokens=111` (i.e. the
    caps come from the config, not hardcoded).
- **`tests/test_prompt.py`** (add)
  - `test_catalog_truncates_long_descriptions`: a tool with a >160-char
    description renders a ≤160-char (+`…`) description.
  - Budget tests for `_trim_history`/`_truncate` still pass with the new limits.
- **`tests/test_llm_config.py`** (add)
  - `test_max_tokens_from_env`: monkeypatch
    `settings.llm_planner_max_tokens` / `settings.llm_summary_max_tokens`; assert
    `default_config()` carries them.
  - `test_load_config_max_tokens_rejects_nonpositive`: a file value of `0`, `-1`
    or `"abc"` falls back to `base.*` (never sends `max_tokens: 0`).
  - `test_load_config_max_tokens_falls_back_when_absent`: absent keys inherit
    `base.*`.
- **`tests/test_api_llm.py`** (extend): a `PUT /api/llm/settings` with
  `planner_max_tokens=0` returns 400; a valid value round-trips through
  `_serialize` and is preserved when the field is omitted.
- **`tests/test_websocket.py`** (add)
  - `test_run_plan_streams_summary_deltas`: monkeypatch `_summarize` to call the
    passed `on_delta` with fragments; assert `delta` events are sent and the
    final `response` carries the same `message_id`.
  - `test_text_reply_streams_and_reconciles`: a `route` that streams then
    returns text; assert `delta` events then `response` with `message_id`.
  - `test_reply_reask_emits_reset_delta`: a `route` that emits a reset then new
    deltas; assert the `reset=True` delta is sent before the new fragments.
- **`tests/test_events.py`** (add/extend)
  - `test_delta_payload_shape` and `test_response_payload_includes_message_id`.
  - `test_delta_payload_reset_flag`: `delta_payload(..., reset=True)` serializes
    `reset: true`.

## 7. Acceptance criteria

- `ruff check .` clean; `pytest -m "not integration and not e2e"` green;
  `npx tsc --noEmit` clean and `npm run build` succeeds in `frontend/`.
- With `LOOMCORE_TIMING=1`, a slow turn shows `[timing] stage=llm_call ... stream=True`
  for the summary (multi-step plan) and/or the planner reply.
- The UI renders the assistant text **incrementally** (deltas) for both a
  multi-step summary and a conversational reply, then the final `response`
  reconciles the message (no duplicate bubble).
- Every LLM call carries a bounded `max_tokens`; a runaway generation is capped.
  The caps are configurable (env `LLM_PLANNER_MAX_TOKENS`/`LLM_SUMMARY_MAX_TOKENS`,
  the settings file, or the Settings tab) and a non-positive value is rejected.
  (For a streamed call, `read_timeout` is per-chunk, so the wall-clock bound now
  rests on `max_tokens`; the REST `POST /api/chat` path keeps the old
  total-response `read_timeout`.)
- `POST /api/chat` still returns a single non-streamed response (unchanged).
- Plan/approval behavior, tool cards, and `plan_done` ordering are unchanged.
- Manual e2e (there is no frontend test runner): a conversational reply and a
  multi-step summary both render **incrementally**; a planner re-ask does **not**
  duplicate text (the `reset` delta replaces the bubble); the send-button
  spinner (`isThinking`) stays until the final `response`/`plan_done`.

## 8. Commits

Coordinator commits after acceptance (RULES 0a). Suggested messages:
- `feat(llm): stream chat completions over SSE`
- `perf(llm): cap planner/summary output and trim the planner prompt`
- `feat(agent): stream the summary and reply text to the UI`
- `feat(interfaces): add the delta event and reconcile streamed messages`
- `feat(frontend): render streamed assistant deltas`

## 9. Risks

- **Provider SSE quirks**: some OpenAI-compatible gateways reject
  `stream_options`; if so, drop `stream_options` (usage becomes optional) or
  gate it behind a config flag. Document in `.env.example`/DEPLOY if a flag is
  added.
- **Retry after partial output** would duplicate text; the `emitted` guard
  prevents it, at the cost of not retrying a mid-stream failure (surfaced as an
  error, not a silent hang).
- **Non-SSE gateway**: if a gateway ignores `stream: true` and returns one JSON
  body, the non-SSE fallback (D3) handles it without deltas.
- **Malformed streamed reply / re-ask**: a reply that validates as empty/bare
  triggers a re-ask; the per-attempt `reset` delta (D4/D5/D7) replaces the
  partial bubble so two attempts never concatenate. The final `response` still
  reconciles the accepted text.
- **`max_tokens` truncation**: the 1024/512 defaults are generous for the
  planner/summary, but they count reasoning tokens too. With `enable_thinking` on,
  a reasoning model can truncate; the caps assume thinking is off (see D1 and the
  previous task's D6 startup warning). Raise `LLM_PLANNER_MAX_TOKENS` /
  `LLM_SUMMARY_MAX_TOKENS` (env, settings file, or the Settings tab) or disable
  thinking if needed.
- **Catalog truncation**: `_catalog_text` (D2) truncates only the text shown to
  the planner; `message_extra` and tool-name/schema validation keep using the
  full schema, so truncation cannot hide a valid tool. Keep it that way.
- **Prompt trim** (`_MAX_HISTORY_CHARS=6000`, `_MAX_USER_CHARS=4000`) reduces
  context; revisit if multi-turn references regress.
- **Frontend has no test runner**; correctness is enforced by `tsc`/build and
  the reducer being pure. The `delta` case must keep `isThinking` true until the
  final `response`/`plan_done`, or the send button will unspinner early.
