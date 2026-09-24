# TZ_RU.md — assistant-tools-before-message

## 1. Goal

Make the assistant's message come **after** the tools run.

Today, while the planner is streaming, `route()` forwards the growing
`agent_plan` **goal** as `delta` events (via `_GoalTextStream`). The frontend
opens an assistant bubble showing that goal **before any tool runs**; the final
`response` later reconciles it. The plan path on the wire is otherwise already
correct: tool cards -> `response` -> `plan_done` (`_run_plan`).

Change: **stop streaming the plan goal.** The assistant bubble must appear only
when the post-tools `response` arrives. `reply` streaming is unchanged (a `reply`
runs no tools).

## 2. Non-goals

- No change to the planner, tool execution, the summary, approvals, or the
  WebSocket contract. No event type is added or removed; goal `delta` events
  simply stop being emitted.
- No frontend change (the frontend already handles deltas absent/present).
- Do not remove `reply` streaming.
- Not planner-content streaming (that is the separate `streaming-1` task).
- No change to the `core_list_tools` filter, report-tool skip, or compact cards.

## 3. Approved decisions (locked)

**D1 — Remove goal streaming.**
- Delete `class _GoalTextStream(_ToolFieldStream)`.
- Delete `goal_stream = _GoalTextStream() if on_delta is not None else None`.
- Remove `goal_stream.reset()` from the re-ask branch (keep
  `reply_stream.reset()` and `await on_delta("", reset=True)`).
- Simplify the factory: `_make_reply_streamer(reply_stream, on_delta)` — drop the
  `goal_stream` parameter and the goal `feed` branch; only `reply_stream.feed`
  is consulted.
- Update the streaming planner call to
  `on_tool_delta=_make_reply_streamer(reply_stream, on_delta)`.

**D2 — Keep `reply` streaming.**
`_ReplyTextStream` and the per-attempt reset stay exactly as they are.

**D3 — No protocol/UI change.**
After removing the goal stream, check every remaining `AGENT_PLAN_TOOL` usage in
`src/llm/router.py`; keep the import only if still used, otherwise drop it.
`_ToolFieldStream` stays (it is the `_ReplyTextStream` base).

## 4. Data model / interfaces

No change. `delta` events still flow for a streamed `reply`; a streamed
`agent_plan` emits no deltas. The `delta` event and its `reset` flag remain part
of the contract.

## 5. Parts

1. **`src/llm/router.py`** — D1: remove `_GoalTextStream`, `goal_stream`, its
   `reset()`, and the goal branch in `_make_reply_streamer`; simplify the factory
   and its call site.
2. **`tests/test_router.py`** — update: remove/adjust any test asserting goal
   deltas; keep the reply-stream tests; add a regression test proving a streamed
   `agent_plan` emits no deltas.

## 6. Tests

Gate (must pass): `uv run ruff check .` and
`uv run pytest -m "not integration and not e2e"`.

- Keep `test_route_streams_reply_via_on_delta` and the `_ReplyTextStream`
  decode/reset/restart tests unchanged.
- Add `test_route_does_not_stream_agent_plan_goal`: drive the streamed planner
  with an `agent_plan` call whose args contain a `goal`; assert `on_delta` is
  **never** awaited and the result is a `plan`.
- Remove any test that asserted goal fragments were forwarded.

## 7. Acceptance criteria

- On a state/action turn the assistant text (`response`) is sent **only after**
  the tool cards; no `delta` is emitted while the planner generates a plan.
- A `reply` turn still streams `delta` fragments.
- `uv run ruff check .` clean; `uv run pytest -m "not integration and not e2e"`
  green.
- No frontend or protocol change; `delta` is suppressed only for plans.

## 8. Commits

Coordinator commits after acceptance (RULES 0a). Suggested message:
- `fix(agent): stop streaming the plan goal so tools run before the assistant message`

## 9. Risks

- **Silent planner wait returns for plan turns.** The goal preview was the only
  pre-tools feedback; removing it makes plan turns show nothing until the tools
  run. This is the owner's explicit choice ("tools first, then message").
  Re-introducing visible progress is deferred to `streaming-1`.
- **Test fallout**: any test asserting goal deltas must be updated; a regression
  test guards against reintroduction.
- **Unused import**: drop `AGENT_PLAN_TOOL` from `router.py` if no longer used.
