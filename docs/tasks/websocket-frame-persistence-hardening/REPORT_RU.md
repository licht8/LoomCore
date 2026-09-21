# REPORT_RU.md — websocket frame validation + tool-message persistence hardening

Spec: docs/tasks/websocket-frame-persistence-hardening/TZ_RU.md

## Goal

Keep the WebSocket turn loop alive when tool-message persistence fails, and ignore
inbound frames that are not usable, without hanging the client or tearing the
connection down on malformed JSON.

## Delivered

### D1 — persistence failure no longer aborts the plan

`src/interfaces/websocket.py`, `_run_plan.emit`: both `_save_tool_message(...)`
awaits (the `"done"` and `"failed"` phases) are wrapped in
`try/except Exception` that logs
`logger.warning("Failed to persist success|error tool message for %s: %s", ...)`
and continues. The terminal `send(tool_success_payload(...))` /
`send(_tool_error_payload(...))` calls and `await audit_step(...)` remain
outside the guard, so every step still gets its terminal event and the plan
reaches `plan_done` with status `done` while persistence failures stay visible
in the logs.

(Review fix: the first cut used `contextlib.suppress(Exception)`, which hid DB
outages; it was replaced with an explicit logged `except`.)

### D2 — frame-shape validation in `handle_message`

The regular-text fall-through now discriminates explicitly:

- `isinstance(data, str)` -> the frame itself is the message;
- `isinstance(data, dict) and isinstance(data.get("message"), str)` -> its
  `message`;
- otherwise log a warning and return (no turn started, no exception).

The pre-existing `if not user_text: return` is preserved.

### D3 — malformed JSON frame tolerated

The receive loop wraps `await websocket.receive_json()` in
`try/except ValueError`; on failure it first applies the per-identity throttle
(`if not _allow_ws_message(identity)` -> `error_payload("Rate limit exceeded...")`
and `continue`), then logs a warning, sends
`error_payload("Malformed JSON frame ignored.")` under `contextlib.suppress`, and
`continue`s. Running the throttle in the malformed branch stops an attacker from
streaming unbounded bad frames (log flooding / send amplification) without a
limit. `WebSocketDisconnect` is not a `ValueError`, so it still propagates to the
outer handler unchanged.

(Review fix: the `continue` previously ran before the throttle, letting malformed
frames bypass `_allow_ws_message` entirely.)

## Tests

tests/test_websocket.py:

- Rewrote `test_run_plan_survives_persistence_crash` as
  `test_run_plan_completes_when_tool_persistence_fails`: with `_save_tool_message`
  raising, a WARNING "Failed to persist success tool message" is logged, there is
  no `"error"` event, two `tool_response` events with status `"success"`, and
  `plan_done` status `"done"`.
- Added `_BadJsonWebSocket` (raises `json.JSONDecodeError` on its first
  `receive_json`).
- Added `test_websocket_ignores_non_string_message`: `{"message": 123}` is
  ignored, the following `{"message": "hello"}` is handled.
- Added `test_websocket_ignores_non_dict_frame`: non-dict frame ignored, loop
  survives and handles a following valid frame.
- Added `test_websocket_skips_malformed_json_frame`: an `"error"` event with
  `"Malformed JSON frame ignored."` is sent and a following valid frame is
  handled.
- Added `_RepeatedBadJsonWebSocket` and
  `test_websocket_rate_limits_malformed_frames`: with the limit set to 1, the
  first bad frame yields `"Malformed JSON frame ignored."` and the second yields
  `"Rate limit exceeded..."`, proving malformed frames consume the throttle.
- Kept `test_plan_failure_emits_plan_done_failed` (executor failure path
  unchanged).

## Verification

- `uv run ruff check .` -> All checks passed!
- `uv run pytest tests/test_websocket.py -q` -> 46 passed
  (tmp/tests_ws_review_fix.log)
- `uv run pytest -m "not integration and not e2e" -q` -> 998 passed, 11 skipped,
  5 deselected (tmp/tests_ws_review_fix_full.log; was 994 before this task)
- Integration not run (no Postgres).

## Changed files

- src/interfaces/websocket.py
- tests/test_websocket.py

## Notes / risks

- Persistence failures are caught and logged (not silent): the UI always receives
  a terminal event and the plan keeps flowing, while a sustained DB outage is
  still visible in the logs for diagnosis.
- `except ValueError` around `receive_json()` intentionally covers
  `json.JSONDecodeError`; the only realistic `ValueError` source is JSON decoding.
- Malformed frames now consume the per-identity throttle, so the bad-frame path
  can no longer be used for unbounded log flooding / send amplification.
- No changes to `execute_plan`, the agent, persistence schemas, approval logic,
  rate limiting, the event schema, or the action branches.

## Suggested commits

- `fix(interfaces): keep plan steps alive when tool persistence fails`
- `fix(interfaces): validate inbound websocket frames and tolerate bad JSON`

Commits created: none
