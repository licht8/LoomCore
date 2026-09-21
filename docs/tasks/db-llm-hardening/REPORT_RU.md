# REPORT_RU.md — DB / LLM hardening

Executor report for `docs/tasks/db-llm-hardening/TZ_RU.md` (D1–D7).

## What was done

### Part 1 — `src/db/session.py` (D1)

- Added `from sqlalchemy.exc import IntegrityError`.
- `ensure_session(session_db, session_id, user_identifier="default", provider="web")`
  now looks users up by `User.provider == provider, User.identifier == user_identifier`.
- The user insert is wrapped in `try/except IntegrityError`: on a duplicate-key
  race the session is rolled back and the winning row re-read; if the re-read is
  still `None` the original `IntegrityError` is re-raised. On success the row is
  refreshed. The session-creation block below is unchanged.
- Docstring notes provider scoping and the race recovery.

### Part 2 — `src/db/models.py` (D2)

- `User.identifier` lost `unique=True`.
- `User.__table_args__` now declares
  `UniqueConstraint("provider", "identifier", name="uq_users_provider_identifier")`.

### Part 3 — `alembic/versions/007_users_provider_identifier_unique.py` (D3)

- `revision = "007"`, `down_revision = "006"`.
- Upgrade locates the single-column UNIQUE on `users.identifier` through
  `pg_constraint`/`pg_class`/`pg_attribute` (`contype = 'u'`, `conkey` = the
  `identifier` column's `attnum`), drops it (falling back to `users_identifier_key`
  when the query returns nothing) and creates `uq_users_provider_identifier` on
  `(provider, identifier)`; downgrade reverses the swap. Migrations `001`–`006`
  were not touched.

### Part 4 — `src/llm/budget.py` (D4)

- `consume_llm_budget` raises `ValueError(f"LLM budget cost must be >= 1, got {cost}")`
  at the top, before touching the counter. Docstring mentions it.

### Part 5 — `src/llm/client.py` (D5, D6)

- New module constant `_RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})`
  and helper `_retry_delay(response, attempt)` (numeric `Retry-After` capped at
  5s, else `min(2 ** (attempt - 1), 5)`).
- The `HTTPStatusError` branch retries a retryable status when
  `attempt < max_attempts` (records `last_exc`, warns, sleeps, continues);
  otherwise it logs an error and raises `LlmError(f"LLM API error {status}")`.
- The per-call `async with httpx.AsyncClient(...)` was replaced by the lazy
  `_get_client()`; `__init__` initialises `self._client = None`; `aclose()`
  closes and resets it. `chat_completion` docstring updated.

### Part 6 — shutdown wiring (D7)

- `src/llm/router.py`: `AIRouter.aclose()` delegates to `self.client.aclose()`.
- `src/app.py` `lifespan` `finally`: after the healthcheck await, imports
  `from src.interfaces import websocket as ws_runtime` and closes
  `chat.airouter` and `ws_runtime.airouter_with_plugins`, each under
  `contextlib.suppress(Exception)`.

### Part 7 — indexes (verify-only)

Confirmed unchanged and already satisfied: `Session.user_id` (`index=True`),
`Message.session_id` (`index=True`), `AuditEntry.created_at` (`index=True`),
backed by migrations `004`/`002`. No change made.

## Tests

- `tests/test_db_session.py`: `_FakeSession` gained `commit_error` so a commit
  can raise a chosen exception. Added
  `test_ensure_session_recovers_when_user_insert_races` (user commit raises
  `IntegrityError`, re-query returns the winner, session still created) and
  `test_ensure_session_reraises_when_user_recovery_finds_nothing`. Existing
  tests remain green with the `(provider, identifier)` lookup.
- `tests/test_llm_budget.py`: `test_zero_cost_is_rejected`,
  `test_negative_cost_is_rejected` (both `ValueError`, counter unchanged).
- `tests/test_llm_client.py`: `_ErrorResponse` now takes `status_code`/`headers`;
  `_FakeClient` dropped the context-manager protocol, gained `aclose`; added
  `_record_sleeps`. Replaced `test_http_status_error_is_not_retried` with
  `test_non_retryable_status_fails_fast` (400 -> `LlmError`, `calls == 1`) and
  added `test_retries_transient_status_then_succeeds`,
  `test_retries_transient_status_then_exhausts`,
  `test_retry_after_header_sets_delay`, `test_client_is_reused_across_calls`,
  `test_aclose_closes_and_rebuilds_client`.
- `tests/test_smoke_plugins.py`: added `test_lifespan_closes_llm_clients`
  (stubs both routers, enters `lifespan`, asserts both were closed in order).

## Verification

- `uv run ruff check .` — All checks passed.
- `uv run pytest tests/test_db_session.py tests/test_llm_budget.py tests/test_llm_client.py tests/test_smoke_plugins.py -q`
  — 35 passed (`tmp/tests_db_llm_targeted.log`).
- `uv run pytest -m "not integration and not e2e" -q` — 971 passed, 11 skipped,
  5 deselected (`tmp/tests_db_llm_full.log`; was 961, +10 tests).

### Review fix round

- `src/llm/client.py`: dropped the dead `last_exc = exc` assignment in the
  retryable-status branch (it is never read; the post-loop handler is only
  reachable via the transport-error branch).
- `src/llm/budget.py`: docstring reworded to "``cost`` is less than 1" so it
  matches the locked `if cost < 1` guard and message.
- `alembic/versions/007`: `downgrade()` now detects cross-provider duplicate
  identifiers and raises an actionable `RuntimeError` before re-imposing the
  single-column UNIQUE.

## Notes / risks

- Migration `007` is not exercised locally (no Postgres). `alembic upgrade
  006:007 --sql` cannot run offline because the catalog lookup needs a live
  connection (`MockConnection.execute(...)` returns `None`), which is a general
  Alembic offline limitation, not a defect; the migration is covered by the
  integration smoke (`LOOMCORE_INTEGRATION=1`). The fallback name is
  `users_identifier_key`.
- `007.downgrade()` first checks for the same `identifier` under multiple
  providers and raises a clear `RuntimeError` naming the offending value and row
  count, so the un-rollbackable state fails explicitly instead of with a raw
  constraint violation mid-rollback.
- The composite unique means the same `identifier` may now exist under two
  providers; `ensure_session` callers keep passing `provider="web"` by default,
  so existing web behaviour is unchanged.
- `Retry-After` parsing handles numeric seconds only; HTTP-date values fall back
  to the exponential backoff (as documented in the spec's risks).

### Suggested commits (coordinator only)

1. `fix(db): scope user identity to provider and recover user insert races`
2. `fix(llm): validate budget cost, retry transient statuses, reuse the HTTP client`

Commits created: none
