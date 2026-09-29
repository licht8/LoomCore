# TZ_RU.md — persistent-text-approval

## 1. Goal

Approval is **textual**: the assistant proposes an action/plan and the user
replies with a short phrase (`да`/`нет`, `yes`/`no`, see
`src/core/confirmations.py`). Today that decision is bound only to an
in-memory, per-connection dict (`pending`, `src/interfaces/websocket.py:711`),
so:

- a page **reload / WS reconnect loses the pending plan** — the next `да` is
  routed fresh to the planner as a normal request;
- the connection has no way to prove which plan the user is answering, so a
  `да` typed after a *different* plan was proposed could be applied to the
  wrong plan.

Goal: the pending plan is **persisted per session**, a textual `да` is bound to
exactly the plan the user saw, and a stale/replaced/expired plan produces a
clear message instead of being silently applied or re-planned.

Non-goal rationale: this is about the **text** path. There are no approve/reject
buttons the owner wants to design around and no `/approvals` UI.

## 2. Non-goals

- No approve/reject/edit **buttons**, no `/approvals` UI work, no new REST
  endpoint for approval.
- No reliance on a client-supplied `plan_id`/`revision`: the server is the
  source of truth.
- The legacy WS actions `plan_approve` / `plan_edit` / `plan_reject`
  (`src/interfaces/websocket.py:826-870`) and any plan-card buttons in
  `frontend/components/chat-input.tsx` are left functionally as they are (they
  are not part of the textual flow). Removing them is a separate task.
- No change to which plans require approval (`plan_requires_approval`), to risk
  classification, or to plan execution semantics.
- Multi-user/tenant isolation is out of scope (single shared `"default"` user).

## 3. Approved decisions (locked)

**D1 — Persist the single pending plan on the session.**
Add `Session.pending` (JSONB, nullable) via a new migration; value shape:

```json
{
  "plan": { "...": "the full plan dict, incl. plan_id/revision/mode/status" },
  "user_text": "<the original request that produced the plan>",
  "created_at": "<naive-UTC ISO string>"
}
```

Exactly one pending plan per session. It is set (overwritten) whenever a plan
waits for the user's decision, and cleared (set to `NULL`) on
approve/reject/expiry. Overwriting on a new proposal is what makes a later plan
*supersede* an earlier one.

**D2 — The confirmation prompt is persisted too.**
The plain-language prompt for a direct unsafe action
(`src/interfaces/websocket.py:653-657`, `f"{goal} Reply yes to proceed."`) must
be saved with `add_assistant_message`, so a reload shows the question the
user is answering.

**D3 — Hydrate `pending` from the DB.**
`pending` stays an in-memory cache, but it is (re)filled from the session's
stored pending plan:
- on WS connect (`websocket_endpoint`, `src/interfaces/websocket.py:694-716`);
- before a text frame is handled when the cache is empty
  (`handle_message`, before the confirmation branch at `:889`).

So reload/reconnect restores the same plan and the same `user_text`.

**D4 — Text `да` is bound to the plan the user saw (staleness guard).**
When a text frame parses as `approve`/`reject` while a pending plan exists,
re-read the DB pending and compare `plan_id` with the connection cache:

- **match** → claim it atomically and act (approve → run, reject → reject);
- **DB pending missing / different `plan_id` (superseded)** →
  `"The plan you were approving was replaced by a newer one. Review the latest
  plan and reply yes again."` (hydrate the cache with the current plan), do
  **not** execute and do **not** fall through to a fresh route;
- **DB pending present but no longer `"proposed"`** (already approved/rejected/
  cancelled/expired) → `"That plan is no longer active."`, do not execute.

**How a `да` is recognised as a decision.** A frame counts as a decision iff a
live pending exists **and** `parse_confirmation` (`src/core/confirmations.py:126`)
returns `approve`/`reject` — an exact, punctuation-normalised, at most
`MAX_CONFIRMATION_WORDS` (3) phrase from the `confirmations.json` approve/reject
lists. A longer or unlisted message (e.g. `да, покажи ещё диски`) is `unclear`
and keeps today's behaviour: with a direct pending, ask once more; otherwise
terminalise the plan and route the message fresh. With **no** pending, `да` is
ordinary text and is routed to the planner.

**D5 — Atomic claim (consume-on-claim).**
The stored `status` is always `"proposed"`: the record is only ever written at
proposal time (`build_plan`, `src/core/agent.py:172`; `next_revision`, `:187`),
so it is **never mutated in place**. Claiming *consumes* the record by NULLing
the column in one conditional update, so two connections (or a retry) cannot
both apply the same plan:

```sql
UPDATE sessions
SET pending = NULL
WHERE id = :session_id
  AND pending->'plan'->>'plan_id' = :plan_id;
```

The rowcount decides the winner (1 = won, 0 = already claimed, superseded or
expired); a loser gets `"That plan is no longer active."` and must not execute.
Expiry uses the same claim and then inspects the returned `created_at`. The
plan's own lifecycle statuses (`approved`/`running`/`done`/`rejected`) live in
the plan object that is sent over WS and stored in the `role="plan"` history
message — the approval slot is separate and is cleared, not transitioned.

**D6 — Expiry is measured against the stored timestamp.**
Keep `CONFIRMATION_TIMEOUT_SECONDS = 300` (`src/core/confirmations.py:25`), but
compute the age from the persisted `created_at` (parse the naive-UTC ISO string)
instead of `time.monotonic()`, so the timeout holds across reconnects. An
expired pending plan is cleared with
`"That plan has expired. Please ask again."` and is not applied.

**D7 — An approve/reject phrase never silently re-plans.**
A frame that `parse_confirmation` classifies as `approve`/`reject` is always
handled as a decision on the pending plan: it either acts, or reports
replaced/expired/inactive — it must **never** fall through to
`_process_user_message`. Only `unclear` frames keep the ability to be routed
fresh (see D4).

## 4. Data model / interfaces

- **DB**: `sessions.pending` JSONB NULL (migration `011_session_pending`,
  `down_revision = "010"`; style precedent: `alembic/versions/010_session_metadata.py`).
  Model field `Session.pending: Mapped[dict | None] = mapped_column(JSONB, nullable=True)`
  (`src/db/models.py`; `JSONB` is imported).
- **New helpers** in `src/db/session.py`:
  - `set_pending(session_db, session_id, plan, user_text) -> None` — write
    `{plan, user_text, created_at=now_iso}`.
  - `get_pending(session_db, session_id) -> dict | None` — read it.
  - `claim_pending(session_db, session_id, plan_id) -> dict | None` — the
    conditional update from **D5**; returns the previous value when the claim
    succeeded, else `None`.
  - `clear_pending(session_db, session_id) -> None`.
  (Name collision note: `src/interfaces/websocket.py` already has a local
  `clear_pending()` closure — the DB helper must be imported under the module
  alias, e.g. `clear_pending as clear_pending_plan`, to avoid shadowing.)
- **No event/protocol change**: the confirmation remains a plain
  `response_payload`; no new WS type, no `WS_SCHEMA_VERSION` bump.
- **`/api/approvals`** (`src/api/approvals.py`, `list_pending_plans`) is
  unchanged (out of scope).

## 5. Parts

1. **DB + migration** — `src/db/models.py` (`Session.pending`),
   `alembic/versions/011_session_pending.py`, helpers in `src/db/session.py`.
2. **Persist proposals** — `src/interfaces/websocket.py`:
   `_process_user_message` calls `set_pending(...)` where it currently calls
   `_remember_pending(...)` (`:656`, `:668`) and persists the direct
   confirmation prompt (**D2**); `_run_plan`/`approve_plan`/`reject_plan` clear
   the stored pending when the plan leaves `"proposed"`.
3. **Hydration** — `websocket_endpoint` loads the stored pending on connect;
   `handle_message` re-hydrates before the confirmation branch (**D3**).
4. **Guarded text approval** — rewrite the `pending_plan` branch
   (`src/interfaces/websocket.py:889-917`) to implement **D4**/**D5**/**D6**.
5. **Docs** — `docs/architecture/AGENT.md` (persistent, session-scoped text
   approval; staleness/expiry messages) + `CHANGELOG.md` (`## 2026-09-29`).

## 6. Tests

- `tests/test_db_session.py`: `set_pending`/`get_pending`/`clear_pending`
  round-trip; `claim_pending` returns the value once and `None` on the second
  call (fake session that models the conditional UPDATE rowcount).
- `tests/test_db_models.py`: `sessions.pending` column exists/nullable.
- `tests/test_websocket.py` (extend the existing `_process_user_message`
  fakes — these pin the discrimination rule from D4/D7):
  - `test_direct_plan_persists_pending_and_prompt` — a direct unsafe plan stores
    the pending row and persists the confirmation prompt;
  - `test_yes_after_reconnect_approves_stored_plan` — a fresh `pending` cache
    hydrated from the store runs the same plan (no re-planning);
  - `test_yes_without_pending_routes_to_planner` — no pending → `да` is routed;
  - `test_yes_with_live_pending_approves_and_does_not_route` — matching
    `plan_id` → approve, `_process_user_message` not called;
  - `test_yes_with_superseded_pending_errors_and_does_not_route` — cached
    `plan_id` differs from the stored pending → "replaced", no execution, no
    route;
  - `test_yes_with_expired_pending_errors_and_does_not_route` — stored
    `created_at` older than 300s → "expired", pending cleared, no route;
  - `test_long_phrase_containing_yes_is_not_an_approval_routes_to_planner` —
    `да, покажи ещё диски` (`unclear`, >3 words) → routed fresh;
  - `test_second_yes_after_claim_is_no_longer_active` — the claim consumed the
    record → "no longer active", no second execution.
- **Gates**: `uv run ruff check .`; `uv run pytest -m "not integration and not
  e2e" -q`. Frontend untouched (no `tsc`/`build` needed).

## 7. Acceptance criteria

- After a reload/reconnect, the previously proposed plan is still pending and a
  textual `да` runs **that** plan (no re-planning).
- Between the proposal and the `да`, proposing another plan makes the old `да`
  fail with the "replaced" message; nothing is executed.
- An expired pending yields the "expired" message instead of executing or
  re-planning.
- Two connections cannot both apply the same plan (single conditional claim).
- `sessions.pending` is `NULL` after approve/reject/expiry.
- Gates green; `alembic upgrade head` applies migration 011 cleanly.

## 8. Commits

1. `feat(db): persist the pending plan on the session` (models + migration +
   `src/db/session.py` helpers + their tests).
2. `fix(interfaces): bind text approval to the persisted plan` (websocket
   hydration, guarded approval, prompt persistence, tests, docs, CHANGELOG).

## 9. Risks

- **Double execution** — mitigated by the atomic claim (**D5**); the loser does
  not run.
- **Shadowing the local `clear_pending`** — import the DB helper under an alias
  (see §4).
- **Naive-UTC parsing** — store and compare ISO strings like the rest of the
  codebase (`datetime.now(UTC).isoformat()`); never compare against a local-time
  `datetime.now()`.
- **JSONB-only SQL** — the project already targets Postgres (`list_pending_plans`
  uses JSONB operators), so the conditional update is acceptable.
- **Expiry flapping across reconnects** — one timestamp in the DB, not the
  connection clock.
