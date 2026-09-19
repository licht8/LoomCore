# TZ_RU.md — chat request hardening and session-id contract reconciliation

## 1. Goal

`POST /api/chat` (`src/api/chat.py`) has three defects:

1. Both LLM-specific error branches return the raw exception text to the client
   **without logging anything**. A provider outage (`LlmError` -> 504) or a
   budget rejection (`LlmBudgetExceeded` -> 429) is therefore invisible in the
   logs: it cannot be diagnosed, counted or alerted on. Only the generic
   `except Exception` branch logs today.
2. The budget `429` uses the `{"error": ...}` envelope while the app-wide
   `RateLimitExceeded` handler (`src/api/deps.py:78`) uses `{"detail": ...}`.
   Two responses with the same status code carry different shapes.
3. `ChatRequest.message` and `.session_id` are unconstrained. The body is parsed
   by Pydantic before `require_admin_token` runs, so an unauthenticated caller
   can post an arbitrarily large payload; an accepted oversized `message` also
   amplifies LLM token cost.

Bounding `session_id` exposes a **pre-existing cross-layer contract mismatch**:
the persistence layer stores the id in `String(36)` columns
(`src/db/models.py:36-37,57-58,88`; `alembic/versions/001_init.py:32,40`), but
the frontend already generates `sess_<32 hex>` = **37 chars**
(`frontend/lib/chat-api.ts:7-11`), and the default is a 36-char UUID. Every
frontend chat therefore overflows `sessions.id` on Postgres: `ensure_session`
(`src/db/session.py:107-125`) fails the INSERT with a value-too-long `DataError`,
the re-read finds no row, the original error is re-raised, and the caller sees a
`500`. Both `src/api/websocket.py:503` and `src/llm/router.py:366` call
`ensure_session` with the raw id, so the WebSocket path is broken too.

Fix: (a) log the two LLM branches, (b) make the budget `429` use `detail`,
(c) bound the request fields, and (d) **reconcile the id contract by widening
the three columns to `String(64)`** — matching the field cap — so the frontend
format, the API bound and the schema agree.

## 2. Non-goals

- No ASGI/Content-Length body-size middleware. `deploy/nginx/default.conf:18`
  already caps requests at `client_max_body_size 10m`; field bounds shrink the
  accepted message but do not stop pre-auth buffering (§9).
- No change to `LlmError`, `LlmBudgetExceeded`, `AIRouter`, `require_admin_token`
  or the auth/extraction order.
- No change to the `500` (`{"error": "Internal server error"}`) or `504`
  (`{"error": ...}`) envelopes — only the `429` changes (§3).
- No change to the frontend id format (`sess_<32 hex>` stays) and no change to
  `User.id` / `Message.id` / `AuditEntry.id` (still UUID `String(36)`).
- No validation added to the WebSocket route `/ws/{session_id}`
  (`src/api/websocket.py`) — noted as a follow-up in §9.
- No dependency or config changes.

## 3. Approved decisions (locked)

- **Budget `429` -> `{"detail": str(exc)}`**, matching the app-wide SlowAPI
  handler (`src/api/deps.py:78`). `504` and `500` keep `{"error": ...}`.
- **Logging:** both branches emit exactly one `logger.warning(...)` on the
  existing module logger `loomcore.api.chat` before returning, using `%s` lazy
  formatting (mirroring the `500` branch). Log the session id and the exception
  text; do **not** log the user `message`. Do not use `logger.exception` here.
- **Bounds:** `message: str = Field(min_length=1, max_length=8000)` and
  `session_id: str = Field(default_factory=lambda: str(uuid.uuid4()),
  max_length=64, pattern=r"^[A-Za-z0-9_-]{1,64}$")`.
- **Widen the DB to `String(64)`, do not tighten to 36.** The frontend's
  `sess_<32 hex>` (37) and the UUID default (36) must both fit; `36` is not a
  real upper bound today and tightening would break existing clients. The field
  cap (64) becomes the single contract the schema honours.
- The new migration is **`006`**, chained after `005`; earlier revisions are
  never edited (the 004/005 docstrings state this rule).

## 4. Data model / interfaces

- `src/db/models.py`: widen `Session.id`, `Message.session_id` and
  `AuditEntry.session_id` to `String(64)`. (`messages.session_id` is a FK to
  `sessions.id`; both are widened together. `audit_log.session_id` has no FK.)
- New `alembic/versions/006_session_id_length.py`:
  - `upgrade()`: `op.alter_column(..., existing_type=sa.String(36),
    type_=sa.String(64), ...)` for `sessions.id` (nullable=False),
    `messages.session_id` (nullable=False), `audit_log.session_id`
    (nullable=True). If Postgres refuses to alter a column referenced by the
    `messages.session_id -> sessions.id` FK, drop and recreate that constraint
    (`messages_session_id_fkey`) around the alter, then re-add it.
  - `downgrade()`: reverse to `sa.String(36)`. Document that it fails if any
    row is longer than 36 chars (acceptable: the data could not exist before
    this migration).
- `ChatRequest` (`src/api/chat.py`): `message` gains `min_length=1,
  max_length=8000`; `session_id` gains `max_length=64` and
  `pattern=r"^[A-Za-z0-9_-]{1,64}$"`. Default unchanged.
- `POST /api/chat` error contract:
  - `429` -> `{"detail": "<budget message>"}` (changed from `{"error": ...}`).
  - `504` -> `{"error": "<llm message>"}` (unchanged).
  - `500` -> `{"error": "Internal server error"}` (unchanged).
  - `422` -> FastAPI validation error for an out-of-bounds body.

## 5. Parts

1. `src/db/models.py`: widen the three columns to `String(64)` (§4).
2. `alembic/versions/006_session_id_length.py`: new migration (§4).
3. `src/api/chat.py`:
   - `LlmBudgetExceeded`: add
     `logger.warning("LLM budget exceeded (session=%s): %s", payload.session_id, exc)`
     and return `content={"detail": str(exc)}` (429).
   - `LlmError`: add
     `logger.warning("LLM error (session=%s): %s", payload.session_id, exc)`
     and keep `content={"error": str(exc)}` (504).
   - Add the `Field(...)` constraints to `ChatRequest`.
   - Leave the `except Exception` (500) branch unchanged.
4. `tests/test_chat_api.py`: update/add chat tests (§6).
5. `tests/test_db_models.py` (new): column-width/contract guard (§6).
6. `tests/test_smoke_integration.py`: add the Postgres boundary test (§6).

## 6. Tests

`tests/test_chat_api.py`:

- Update `test_chat_maps_budget_exceeded_to_429` — expect
  `{"detail": "daily LLM budget exhausted"}`; wrap in
  `caplog.at_level(logging.WARNING, logger="loomcore.api.chat")` and assert
  `"LLM budget exceeded (session=s1)"` in `caplog.text`.
- Add `test_chat_logs_llm_error` — `LlmError("upstream timeout")` -> 504
  `{"error": "upstream timeout"}` and `caplog.text` contains
  `"LLM error (session=s1)"`.
- Add `test_chat_rejects_empty_message` (`message=""` -> 422);
  `test_chat_rejects_overlong_message` (`"x"*8001` -> 422);
  `test_chat_accepts_max_length_message` (`"x"*8000` -> 200).
- Add `test_chat_rejects_invalid_session_id` (`"bad id"` -> 422; 65-char id
  -> 422); `test_chat_accepts_default_session_id` (`{"message": "hi"}` -> 200);
  `test_chat_accepts_sess_prefix_session_id` (`"sess_" + "a"*32` -> 200).
  Add a comment that the storage width is guarded by `tests/test_db_models.py`;
  the monkeypatched router intentionally does not persist.

`tests/test_db_models.py` (new, no DB — ORM metadata only):

- `test_session_id_columns_are_wide_enough` — assert
  `Session.__table__.c.id.type.length == 64`,
  `Message.__table__.c.session_id.type.length == 64`,
  `AuditEntry.__table__.c.session_id.type.length == 64`.
- `test_chat_bound_matches_column_width` — assert the `session_id`
  `max_length` constraint on `ChatRequest` (64) is `<=` the
  `Session.id` column length, so the API bound and the schema cannot drift
  again.

`tests/test_smoke_integration.py` (needs Postgres; CI integration job):

- Add a session-scoped fixture that applies migrations once with
  `alembic.command.upgrade(Config("alembic.ini"), "head")`.
- Add `test_ensure_session_accepts_long_ids` — call
  `ensure_session(db, "sess_" + "a"*32)` (37 chars) and `ensure_session(db,
  "b"*64)` (64 chars) against the real DB; assert both return the id; delete
  the created rows afterwards. This is the boundary check the reviewer asked
  for and only passes once the column is widened.

Gates:

```
uv run ruff check .
uv run pytest tests/test_chat_api.py tests/test_db_models.py -q
uv run pytest -m "not integration and not e2e" -q
# optional, needs Postgres:
LOOMCORE_INTEGRATION=1 uv run pytest tests/test_smoke_integration.py -q
```

## 7. Acceptance criteria

- A budget rejection emits a WARNING on `loomcore.api.chat` before the `429`,
  and the body is `{"detail": ...}`.
- An LLM error emits a WARNING on `loomcore.api.chat` before the `504`, and the
  body stays `{"error": ...}`.
- `message` empty or > 8000 chars -> 422; exactly 8000 -> 200.
- A `session_id` outside `^[A-Za-z0-9_-]{1,64}$` -> 422; the UUID default and
  `sess_<32 hex>` -> 200.
- The three `session_id` columns are `String(64)` in both `models.py` and
  migration 006; `ensure_session` persists 37- and 64-char ids on Postgres.
- `ruff check .` is clean and
  `pytest -m "not integration and not e2e"` passes.

## 8. Commits

- `fix(db): widen session id columns to 64 chars`
- `fix(api): log LLM chat failures and bound chat request input`

## 9. Risks

- **Client-visible 429 change.** Switching the budget `429` from `error` to
  `detail` changes the shape. No in-repo consumer uses the REST `/api/chat`
  body (the frontend chats over `/ws/{session_id}`); the change aligns `429`
  with the app-wide handler. Note it in the report.
- **Migration touches a primary key and a foreign key.** Widening
  `sessions.id` / `messages.session_id` is a type change on an indexed PK/FK.
  Postgres allows the length widening; if the FK blocks it, drop/recreate
  `messages_session_id_fkey` inside the migration. Existing rows are preserved.
  `downgrade()` to 36 fails if longer rows exist — acceptable and documented.
- **Body buffering is not fully mitigated.** Field bounds reject an oversized
  *accepted* message but Pydantic still buffers/parses first; only
  `client_max_body_size 10m` bounds the actual bytes. Content-Length middleware
  is out of scope — follow-up.
- **422 before auth.** FastAPI validates the body before the handler, so an
  invalid body from an unauthenticated caller returns 422, not 401. Pre-existing
  ordering; leaks no data.
- **Previously accepted long messages now 422.** Intentional (LLM cost).
- **Log content.** The exception text may include provider details; logs are
  internal-only.
- **WebSocket parity.** `/ws/{session_id}` remains unvalidated; the DB widening
  fixes its 37-char failure, but adding a length/format guard there is a
  follow-up rather than part of this task.
