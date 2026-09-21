# TZ_RU.md — websocket frame validation and tool-message persistence hardening

## 1. Goal

Make the WebSocket turn loop resilient to two classes of failure in
`src/interfaces/websocket.py`:

1. A failure to persist a finished plan step currently propagates out of
   `emit`, aborts `execute_plan`, and is only turned into a generic
   `error_payload` by `_run_plan` — after the UI already received the
   `tool_call` card, leaving the step without a terminal event (stuck card).
2. Inbound frames are not shape-checked: a non-string `message` reaches
   `_wants_raw_output`/`route` (a `TypeError` swallowed by `_guarded`, so the
   client never gets a reply and the turn hangs) and a non-dict frame raises
   `AttributeError`. A malformed JSON frame also escapes `receive_json()` to
   the outer handler and tears the connection down.

## 2. Non-goals

- No changes to `execute_plan`, the agent, persistence schemas, approval logic,
  rate limiting, or the event schema.
- No new error event type; malformed frames reuse `error_payload`.
- No change to the *content* of `handle_message` actions (`cancel`,
  `plan_approve`, `plan_reject`, `plan_edit`) — only the fall-through text path
  and the receive loop.

## 3. Approved decisions (locked)

- **D1** — In `_run_plan.emit`, wrap both `_save_tool_message(...)` awaits
  (`"done"` and `"failed"`) in `with contextlib.suppress(Exception):`. The
  terminal `send(...)` (`tool_success_payload` / `_tool_error_payload`) and
  `await audit_step(...)` MUST still run. This matches `audit_step` and the
  existing cancel-path persistence calls (`:560`, `:602`).
- **D2** — In `handle_message`'s regular-text fall-through, validate the frame
  explicitly:

  ```python
  if isinstance(data, str):
      user_text = data
  elif isinstance(data, dict) and isinstance(data.get("message"), str):
      user_text = data["message"]
  else:
      logger.warning("Ignoring frame with no string message for session %s", session_id)
      return
  if not user_text:
      return
  ```

  This replaces `user_text = data if isinstance(data, str) else data.get("message", "")`
  and preserves the existing `if not user_text: return`.
- **D3** — In the receive loop, parse inside a guard so a JSON error does not
  kill the connection:

  ```python
  try:
      data = await websocket.receive_json()
  except ValueError:
      logger.warning("Ignoring malformed JSON frame for session %s", session_id)
      with contextlib.suppress(Exception):
          await send(error_payload("Malformed JSON frame ignored."))
      continue
  ```

  (`json.JSONDecodeError` is a `ValueError`; `WebSocketDisconnect` is not, so it
  still propagates to the outer handler unchanged.) The rate-limit check and the
  inner `handle_message` try/except stay as they are.

## 4. Data model / interfaces

None. No schema, event, or API changes.

## 5. Parts

1. `src/interfaces/websocket.py` — D1: suppress the two `_save_tool_message`
   awaits in `emit`; keep `send` + `audit_step` outside the suppression.
2. `src/interfaces/websocket.py` — D2: frame-shape validation in
   `handle_message` (log a warning and return for a non-dict frame or a
   non-string `message`).
3. `src/interfaces/websocket.py` — D3: guard `receive_json()` with
   `except ValueError` → warn + `error_payload("Malformed JSON frame ignored.")`
   + `continue`.

## 6. Tests

Add/update in `tests/test_websocket.py`:

- Rewrite `test_run_plan_survives_persistence_crash` as
  `test_run_plan_completes_when_tool_persistence_fails`: with
  `_save_tool_message` raising, assert no `"error"` event, at least one
  `tool_success` event, and `plan_done` with status `done`. Use
  `_risky_two_step_plan`/`_safe_two_step_plan` and `_patch_plan_runtime`.
- Add `test_websocket_ignores_non_string_message`: drive `websocket_endpoint`
  with a `FakeWebSocket` whose first frame is `{"message": 123}` followed by a
  valid `{"message": "..."}`; assert no exception, no turn for the bad frame,
  and the valid frame is handled.
- Add `test_websocket_ignores_non_dict_frame`: first frame is `123` (or `[1,2]`),
  then a valid frame; assert the loop survives.
- Add `test_websocket_skips_malformed_json_frame`: a `FakeWebSocket` subclass
  whose `receive_json` raises `json.JSONDecodeError` on the first call, then
  delegates; assert an `error` event with `"Malformed JSON frame ignored."` is
  sent and a following valid frame is handled (connection not closed by the
  outer fatal handler).
- Keep `test_plan_failure_emits_plan_done_failed` (executor failure path
  unchanged).

Gates:

```
uv run ruff check .
uv run pytest tests/test_websocket.py -q
uv run pytest -m "not integration and not e2e" -q
```

## 7. Acceptance criteria

- A `_save_tool_message` failure no longer aborts the plan: every step still
  emits its terminal `tool_success`/`tool_error` event and `plan_done` reports
  `done`.
- A frame whose `message` is not a string (or a non-dict frame) is ignored with
  no unhandled exception and no hung turn.
- A malformed JSON frame produces an `error` event and the connection keeps
  serving subsequent frames.
- `ruff` clean; `tests/test_websocket.py` passes; the fast suite passes.

## 8. Commits

- `fix(interfaces): keep plan steps alive when tool persistence fails`
- `fix(interfaces): validate inbound websocket frames and tolerate bad JSON`

(Coordinator commits only after acceptance — RULES 0a.)

## 9. Risks

- `contextlib.suppress(Exception)` makes persistence failures silent, matching
  the existing `audit_step` and cancel-path behavior; a sustained DB outage will
  no longer abort turns but also will not be surfaced to the client. This is the
  intended trade-off (the UI must always get a terminal event).
- `except ValueError` around `receive_json()` could mask an unexpected
  `ValueError` from the receive implementation; acceptable because the only
  realistic source is JSON decoding, and `WebSocketDisconnect` still propagates.
