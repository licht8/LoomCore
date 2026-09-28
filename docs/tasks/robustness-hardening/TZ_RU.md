# TZ_RU.md — robustness hardening (audit verify, WS reconnect, dead button, rate limits)

> Copy to `docs/tasks/<kebab-name>/TZ_RU.md` and fill in **before** starting
> (RULES 16). Content is English (RULES 0). Keep it specific and testable.

## 1. Goal

Close four robustness/UX gaps found in the audit:

- **Audit verification loads the whole table, and history has no composite
  index.** `GET /api/audit/verify` calls `all_audit_entries`
  (`src/db/audit_store.py:29-35`), materialising every row before hashing, and
  `messages` only has a single-column `session_id` index while
  `get_session_history` orders by `created_at` (`src/db/session.py`).
- **The WS client reconnects forever on an auth close and silently drops
  sends.** `frontend/lib/use-chat.ts:123-143` reconnects on any `onclose`
  (ignoring the backend's `1008` auth close) and `sendMessage`/`sendPlanResponse`/
  `cancelRequest` return with no feedback when the socket is not OPEN
  (`:208-209`, `:232`, `:289`).
- **A dead "Attach file" button** (`frontend/components/chat-input.tsx:237-240`)
  has no handler — forbidden by RULES 11.
- **Three routes are unthrottled**: `GET/DELETE /api/sessions/{id}/messages`
  (`src/api/sessions.py:48,62`), `GET /api/artifacts/{name}/{kind}`
  (`src/api/artifacts.py:48`), `POST /api/auth/logout` (`src/api/auth.py:106`).

## 2. Non-goals

- No change to the hash-chain format, the append path, or `verify_chain`
  semantics (tamper-evidence, not truncation).
- No change to the WS message protocol or event vocabulary.
- No attachment feature: the dead button is removed, not implemented.

## 3. Approved decisions (locked)

### D1 — stream audit verification and index messages

- `src/db/audit_store.py`:
  - add `async def iter_audit_entries(session_db, *, batch_size: int = 1000)`
    (async generator) yielding rows in chain order `(created_at asc, id asc)`
    using keyset pagination: fetch a page with
    `.where(or_(created_at > last_created, and_(created_at == last_created, id > last_id)))`
    `.order_by(created_at.asc(), id.asc()).limit(batch_size)`, yield the rows,
    advance the cursor from the last row, stop on a short page;
  - add `async def latest_entry_hash(session_db) -> str | None` (one row:
    `select(AuditEntry.entry_hash).order_by(created_at.desc(), id.desc()).limit(1)`);
  - replace `all_audit_entries` with these two (it is only used by
    `src/api/audit.py`); keep `list_audit_entries`/`count_audit_entries`.
- `src/core/audit.py`: widen `verify_chain(entries: Iterable[AuditEntry], ...)`
  to iterate once, counting entries and tracking the last hash, then apply
  `expected_count`/`expected_head`. Behaviour is unchanged for a list input
  (existing `tests/test_audit.py` keep passing); the `expected_count` check now
  runs at the end of the pass.
- `src/api/audit.py` `verify_audit`: inside the session,
  `total = await count_audit_entries(db)`, `head = await latest_entry_hash(db)`,
  `valid = verify_chain(iter_audit_entries(db), expected_count=total, expected_head=head)`;
  return `{"valid": valid, "count": total, "head": head}`.
- New Alembic migration `alembic/versions/009_messages_session_created_index.py`
  (`revision = "009"`, `down_revision = "008"`) creating
  `ix_messages_session_id_created_at` on `messages(session_id, created_at)`
  (downgrade drops it). Add the matching
  `Index("ix_messages_session_id_created_at", "session_id", "created_at")` to
  `Message.__table_args__` in `src/db/models.py` (import `Index`) so
  autogenerate sees no drift — the same pattern `AuditEntry` uses.

### D2 — WS reconnect + send feedback (`frontend/lib/use-chat.ts`)

- `socket.onclose = (event: CloseEvent) => { ... }`: when `event.code === 1008`
  do **not** schedule a reconnect; set `error` to a clear message (e.g.
  `"Session expired. Reload the page to sign in again."`) alongside the existing
  busy-state cleanup. All other close codes keep the capped-backoff reconnect.
- `sendMessage` / `sendPlanResponse` / `cancelRequest`: when the socket is absent
  or not OPEN, `setState` an `error` (`"Not connected to the server. Reconnecting…"`)
  and return. Do not append the user message in that case.

### D3 — remove the dead button (`frontend/components/chat-input.tsx`)

- Delete the `Button` that renders `<Paperclip className="size-4.5" />` (lines
  ~237-240) and drop `Paperclip` from the `lucide-react` import (line 4). Leave
  the `onClear` spacer and everything else unchanged.

### D4 — rate limits

- `src/api/sessions.py`: `@limiter.limit("60 per 60 seconds")` on `get_messages`,
  `@limiter.limit("10 per 60 seconds")` on `delete_messages` (both already take
  `request: Request`).
- `src/api/artifacts.py`: `from src.core.rate_limit import limiter` and
  `@limiter.limit("60 per 60 seconds")` on `download_artifact`.
- `src/api/auth.py`: `@limiter.limit("10 per 60 seconds")` on `logout`.

## 4. Data model / interfaces

- New index `ix_messages_session_id_created_at` (migration 009 + ORM). No other
  schema change.
- `/api/audit/verify` response shape is unchanged (`{valid, count, head}`), now
  streamed.
- WS close code 1008 now stops the client reconnect (no wire change).
- Three routes gain limits (60/60s for reads, 10/60s for delete and logout).

## 5. Parts

1. Audit streaming + index — `src/db/audit_store.py`, `src/core/audit.py`,
   `src/api/audit.py`, `src/db/models.py`,
   `alembic/versions/009_messages_session_created_index.py`.
2. WS reconnect/send feedback — `frontend/lib/use-chat.ts`.
3. Dead button — `frontend/components/chat-input.tsx`.
4. Rate limits — `src/api/sessions.py`, `src/api/artifacts.py`,
   `src/api/auth.py`.

## 6. Tests

- `tests/test_audit.py`: add `test_verify_chain_accepts_iterator` (pass
  `iter([first, second])` -> True; a tampered iterator -> False).
- `tests/test_audit_api.py`: replace the `all_audit_entries` monkeypatch
  (`_patch_entries`, line ~72) with patches for `iter_audit_entries` (an async
  generator over the entries), `count_audit_entries` and `latest_entry_hash`;
  keep the valid / tampered / empty cases and the admin-rejection case.
- `tests/test_db_models.py`: add an assertion that `messages` declares the
  `ix_messages_session_id_created_at` index (via
  `{i.name for i in Message.__table__.indexes}`).
- New `tests/test_api_rate_limits.py`: for each newly-limited route, call the
  decorated function directly with a fake `Request` (unique `client` IP per test;
  auth and `async_session_factory` monkeypatched) and assert
  `slowapi.errors.RateLimitExceeded` is raised once the limit is exceeded; a
  fixture calls `limiter.reset()`. Reuse the fake-request/`client_ip` shape from
  `tests/test_rate_limit.py`.
- Frontend: `npx tsc --noEmit` + `npm run build` (no frontend test runner).
  Manual: a 1008 close shows the error and stops reconnecting; sending while
  disconnected shows the banner; the Attach button is gone.
- Gates: `uv run ruff check .`; `uv run pytest -m "not integration and not e2e" -q`.

## 7. Acceptance criteria

- `/api/audit/verify` never materialises the full `audit_log` (uses
  `iter_audit_entries(batch_size=...)`); the response shape is unchanged and
  tampering is still detected.
- Migration 009 applies and rolls back, and the ORM declares the same index (no
  autogenerate drift).
- On a 1008 close the client stops reconnecting and surfaces an error; a send
  with no open socket surfaces an error instead of doing nothing.
- The Attach button is removed; no dead control remains.
- `uv run ruff check .` clean; `uv run pytest -m "not integration and not e2e" -q`
  green; `npx tsc --noEmit` clean and `npm run build` succeeds.

## 8. Commits

1. `perf(db): stream audit verification and index messages by session and time`
2. `fix(frontend): handle WS auth close, surface dropped sends, drop dead attach button`
3. `fix(api): rate-limit session history, artifacts and logout`

## 9. Risks

- Widening `verify_chain` to an iterable means the `expected_count` check runs
  after the full pass; the result is identical.
- Keyset pagination on `(created_at, id)` matches `all_audit_entries` ordering; a
  write that lands mid-verify could be missed — acceptable for a best-effort
  admin check and consistent with today (no anchor).
- `limiter.reset()` touches the shared limiter: use a unique client IP per test
  to avoid cross-test interference.
- Removing the Attach button drops a non-functional affordance; note it in the
  CHANGELOG.
