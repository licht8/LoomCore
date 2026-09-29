# TZ_RU.md — unify the WebSocket plan actions with text approval

## 1. Goal

The three WebSocket control actions (`plan_approve`, `plan_reject`,
`plan_edit`) must obey the same persisted-pending guards as a textual
`да`/`нет`: they read the stored `sessions.pending`, verify it is still the plan
they were shown and is still `proposed` and fresh, and only then act.
`plan_edit` stops being an ad-hoc in-memory mutation and becomes a guarded new
revision of the persisted pending.

Context: the `persistent-text-approval` TZ (commits `bac5bd6`, `a61cf9d`) moved
text approval onto `sessions.pending` and introduced the guard ladder inside
`handle_message`'s local `decide_pending`. The WS actions still bypass it:
`plan_approve`/`plan_reject` read `pending["plan"]` from the connection cache
and call `approve_plan`/`reject_plan` directly (`src/interfaces/websocket.py`
`handle_message` branches), and `plan_edit` rewrites only the in-memory cache
and the plan message. So an action can act on a plan that has been superseded,
or apply a second time after the text already approved it — exactly what the
persisted pending was added to prevent.

## 2. Non-goals

- No frontend change: the plan card and its Approve/Edit/Reject buttons stay.
- No client-supplied `plan_id`/`revision`: the session's pending is the authority.
- No change to the textual approval path, its messages, or `decide_pending`'s
  semantics; `cancel` and the busy-state behaviour are unchanged.
- No schema change (migration 011 already provides `sessions.pending`).

## 3. Approved decisions (locked)

**D1 — approve/reject go through `decide_pending`.**
`plan_approve` and `plan_reject` resolve the cached plan (`pending.get("plan")`,
hydrating from the store when the cache is empty) and call
`await decide_pending("approve" | "reject", cached_plan)`. They no longer call
`forget_pending_plan()` + `approve_plan`/`reject_plan` directly. The same
`replaced` / `inactive` / `expired` messages and the same consume-on-claim
therefore apply, so a WS action can never act on a superseded plan or
double-apply one. The `is_busy()` guard stays on `plan_approve` and is added to
`plan_reject`.

**D2 — edit is a guarded new revision.**
`plan_edit` must (a) pass the same pre-claim guards against the stored pending
(`plan_id` match, `status == "proposed"`, age <= `CONFIRMATION_TIMEOUT_SECONDS`)
and (b) replace the persisted pending atomically with the revised plan, so a
stale edit cannot resurrect or clobber a newer pending.

**D3 — `replace_pending` compare-and-set.**
New helper in `src/db/session.py`:

```python
async def replace_pending(session_db, session_id, plan_id, plan, user_text) -> bool
```

Builds the record exactly like `set_pending`, then
`update(Session).where(Session.id == session_id,
Session.pending["plan"]["plan_id"].as_string() == plan_id).values(pending=record)`;
commit; return `result.rowcount == 1`. `False` means the pending was consumed or
replaced meanwhile, so the handler reports it instead of applying a stale edit.

**D4 — shared guard helper.**
Extract the read + staleness checks from `decide_pending` into a local async
`current_pending(cached_plan) -> dict | None` that returns the stored plan when
the cached plan still matches and is `proposed`, and otherwise sends
`replaced_message` / `inactive_message` and returns `None`. `decide_pending`
calls it, then claims, then applies the expiry check. The edit path calls it,
then builds and validates the revision, then `replace_pending`.

**D5 — cache-only update.**
`_remember_pending(session_id, pending, plan, user_text, *, prompt=None,
persist=True)`: with `persist=False` it updates only the connection cache (the
edit path has already persisted via `replace_pending`).

## 4. Data model / interfaces

- `src/db/session.py`: `replace_pending(session_db, session_id, plan_id, record) -> bool`
  (no schema change; migration 011 already added `sessions.pending`).
- No wire change: the same `plan_proposed`, `error`, `busy`, and plain-text
  payloads are used.

## 5. Parts

1. `src/db/session.py` — add `replace_pending`.
2. `src/interfaces/websocket.py`
   - extract `current_pending(cached_plan)` and use it inside `decide_pending`;
   - rewrite the `plan_approve` / `plan_reject` branches to hydrate when needed
     and delegate to `decide_pending`;
   - rewrite `plan_edit`: `is_busy` guard -> `current_pending` -> `next_revision` +
     `validate_plan` (unchanged error payload) -> `replace_pending` with the new
     record (`{"plan": revised, "user_text": ..., "created_at": <utc isoformat>}`)
     -> `_remember_pending(..., persist=False)` -> save the revised plan message +
     `plan_proposed_payload` -> run when the revision no longer needs approval;
   - `_remember_pending` gains `persist: bool = True`.

## 6. Tests

`tests/test_db_session.py`:

- `test_replace_pending_returns_true_when_it_matched` (one UPDATE + one commit,
  rowcount 1);
- `test_replace_pending_returns_false_when_nothing_matched` (rowcount 0, still
  one commit).

`tests/test_websocket.py` (extend the persisted-store fakes):

- `test_ws_plan_approve_uses_decide_pending` — the action runs the matching plan
  once and consumes the record;
- `test_ws_plan_approve_superseded_reports_replaced_and_does_not_run`;
- `test_ws_plan_reject_uses_decide_pending` — clears the record and runs nothing;
- `test_ws_plan_approve_busy_sends_busy`;
- `test_ws_plan_edit_replaces_pending_revision` — the store now holds
  `revision + 1` with the edited steps, a `plan_proposed` was emitted, and the
  cache matches;
- `test_ws_plan_edit_on_consumed_pending_reports_inactive` — `replace_pending`
  returns `False` -> inactive, no run;
- `test_ws_plan_edit_without_approval_runs` — the revised plan needs no approval
  -> `_run_plan` starts and the pending is cleared.

## 7. Acceptance criteria

- `plan_approve`/`plan_reject`/`plan_edit` never act on a plan whose stored
  pending has been superseded, consumed, or expired; each reports the matching
  message instead.
- A second approve of the same plan is a no-op ("no longer active").
- `plan_edit` persists the new revision, and a textual `да` afterwards approves
  that revision.
- `uv run ruff check .` clean; `uv run pytest -m "not integration and not e2e" -q`
  green.

## 8. Commits

1. `feat(db): add an atomic replace for the pending plan`
2. `fix(interfaces): route WS plan actions through the persisted-pending guards`

## 9. Risks

- Hydration timing: an action that arrives before the connect hydration would
  see an empty cache; `current_pending` must hydrate from the store first.
- Expiry is still evaluated after the claim (consume), so an expired plan is
  consumed and reported expired — intended.
- `plan_edit` that makes the plan approval-free runs immediately, matching the
  current behaviour; the pending is cleared before the run.
