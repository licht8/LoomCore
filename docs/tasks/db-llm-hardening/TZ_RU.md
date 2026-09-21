# TZ_RU.md — DB / LLM hardening

Task spec for the executor. Content is in English per RULES 0; the file name keeps
the `_RU.md` convention of `docs/tasks/_template/`.

## 1. Goal

Close review findings on the database session layer and the LLM client:

- make `ensure_session` idempotent when two callers race to create the same user
  (`User.identifier` is UNIQUE and `user_identifier` defaults to `"default"`);
- scope user identity to `(provider, identifier)` instead of a globally unique
  `identifier`, so a web identity and a Telegram identity with the same string do
  not collide;
- reject nonsensical LLM budget costs (0 or negative) instead of silently
  corrupting the counter;
- retry transient provider statuses (429/5xx) with backoff and `Retry-After`;
- stop creating a fresh `httpx.AsyncClient` per LLM turn; reuse one and close it
  on shutdown.

Findings about missing indexes (Session.user_id, Message.session_id,
AuditEntry.created_at) are already satisfied in the tree and are **verify-only**
(see Part 7).

## 2. Non-goals

- No change to the already-present indexes or to the stored timestamp format
  (`created_at` stays naive-UTC ISO strings; the column is already indexed).
- No change to `add_message`, session-status handling, or the session insert's
  existing `except Exception` recovery.
- No new settings; no auth/WS/frontend/migration of unrelated tables.
- No retry for non-transient statuses (e.g. 400/401/403/404).

## 3. Approved decisions (locked)

- **D1** `ensure_session` gains `provider: str = "web"` and looks users up by
  `(provider, identifier)`. The user insert gets the same recovery as the session
  insert: on `IntegrityError`, rollback and re-query; re-raise if still missing.
- **D2** `User.identifier` loses `unique=True`; a table-level
  `UniqueConstraint("provider", "identifier", name="uq_users_provider_identifier")`
  is added.
- **D3** Migration `007` swaps the single-column unique for the composite one.
- **D4** `consume_llm_budget` raises `ValueError` when `cost < 1`.
- **D5** Retry transient statuses `{408, 429, 500, 502, 503, 504}`; honor
  `Retry-After` (capped) else exponential backoff. Non-retryable statuses fail
  immediately as today.
- **D6** The client is created lazily once per `HetznerClient` and reused.
- **D7** `AIRouter.aclose()` closes it; `src/app.py` closes both routers on
  shutdown.

## 4. Data model

`users` currently has an unnamed single-column UNIQUE on `identifier`
(PostgreSQL default name `users_identifier_key`, created in migration `001`).
It is replaced by `uq_users_provider_identifier` on `(provider, identifier)`.
No other column changes. Migration head stays `006` until this adds `007`.

## 5. Parts

### Part 1 — `src/db/session.py` (D1)

- Import `from sqlalchemy.exc import IntegrityError`.
- Signature: `async def ensure_session(session_db, session_id, user_identifier="default", provider="web") -> str:`.
- User lookup: `select(User).where(User.provider == provider, User.identifier == user_identifier)`.
- On create:

  ```python
  user = User(id=str(uuid4()), identifier=user_identifier, provider=provider, role="user")
  session_db.add(user)
  try:
      await session_db.commit()
  except IntegrityError:
      await session_db.rollback()
      result = await session_db.execute(
          select(User).where(User.provider == provider, User.identifier == user_identifier)
      )
      user = result.scalar_one_or_none()
      if user is None:
          raise
  else:
      await session_db.refresh(user)
  ```

- Update the docstring to note provider scoping and the race recovery.
- The session-creation block below is unchanged.

### Part 2 — `src/db/models.py` (D2)

- `identifier: Mapped[str] = mapped_column(String, nullable=False)` (drop `unique=True`).
- Add to `User`:

  ```python
  __table_args__ = (
      UniqueConstraint("provider", "identifier", name="uq_users_provider_identifier"),
  )
  ```

- `UniqueConstraint` is already imported.

### Part 3 — `alembic/versions/007_users_provider_identifier_unique.py` (D3)

- `revision = "007"`, `down_revision = "006"`.
- Upgrade: find the existing UNIQUE constraint on `users.identifier` via
  `op.get_bind()` against `pg_constraint` (contype `'u'`, its `conkey` equal to
  the `pg_attribute.attnum` of `users.identifier`) and `DROP CONSTRAINT` it;
  fall back to the name `users_identifier_key` when the query returns nothing.
  Then `op.create_unique_constraint("uq_users_provider_identifier", "users", ["provider", "identifier"])`.
- Downgrade: drop `uq_users_provider_identifier` and recreate the single-column
  unique on `identifier`.
- Never edit migrations `001`–`006`.

### Part 4 — `src/llm/budget.py` (D4)

- At the top of `consume_llm_budget`:
  `if cost < 1: raise ValueError(f"LLM budget cost must be >= 1, got {cost}")`.
- Docstring mentions the rejection.

### Part 5 — `src/llm/client.py` (D5, D6)

- Module constant `_RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})`.
- Helper for the delay:

  ```python
  def _retry_delay(response: httpx.Response, attempt: int) -> float:
      header = response.headers.get("retry-after")
      if header:
          try:
              return min(float(header), 5.0)
          except ValueError:
              pass
      return min(2 ** (attempt - 1), 5)
  ```

- `except httpx.HTTPStatusError as exc:` branch: read `status`; when
  `status in _RETRYABLE_STATUSES and attempt < max_attempts`, set `last_exc`,
  `logger.warning(...)`, `await asyncio.sleep(_retry_delay(exc.response, attempt))`,
  `continue`; otherwise log an error and `raise LlmError(f"LLM API error {status}") from exc`.
- Replace `async with httpx.AsyncClient(timeout=self._timeout()) as client:` with
  `client = self._get_client()` and dedent the retry loop one level.
- Add `self._client: httpx.AsyncClient | None = None` in `__init__`:

  ```python
  def _get_client(self) -> httpx.AsyncClient:
      if self._client is None:
          self._client = httpx.AsyncClient(timeout=self._timeout())
      return self._client

  async def aclose(self) -> None:
      if self._client is not None:
          await self._client.aclose()
          self._client = None
  ```

### Part 6 — shutdown wiring (D7)

- `src/llm/router.py`: add `async def aclose(self) -> None:` on `AIRouter` that
  `await self.client.aclose()`.
- `src/app.py` `lifespan` `finally` (after the healthcheck await):

  ```python
  from src.interfaces import websocket as ws_runtime

  with contextlib.suppress(Exception):
      await chat.airouter.aclose()
  with contextlib.suppress(Exception):
      await ws_runtime.airouter_with_plugins.aclose()
  ```

  `chat` is already imported in `src/app.py`; `ws_runtime` satisfies the
  `interfaces` import edge (`src/api/websocket.py` already imports it).

### Part 7 — indexes (verify-only)

- Confirm `Session.user_id`, `Message.session_id`, `AuditEntry.created_at`
  already carry `index=True`, backed by migrations `004`/`002`. If they do, make
  no change.

## 6. Tests

- `tests/test_db_session.py`
  - The fake `commit()` currently raises `RuntimeError`; add the ability to raise
    `IntegrityError` for the user insert (or a dedicated fake). Add
    `test_ensure_session_recovers_when_user_insert_races`: the user `commit`
    raises `IntegrityError`, the re-query returns the winning user, and the
    function returns the session id. Add `test_ensure_session_reraises_when_user_recovery_finds_nothing`.
  - Keep existing tests green with the `(provider, identifier)` predicate.
- `tests/test_llm_budget.py`: `cost=0` and `cost=-1` raise `ValueError` and leave
  the counter unchanged.
- `tests/test_llm_client.py`
  - Give `_ErrorResponse` a `headers` dict and a configurable `status_code`.
  - Replace `test_http_status_error_is_not_retried` with a non-retryable
    fast-fail test (400 -> `LlmError`, `calls == 1`).
  - Add retry-transient-then-succeed (e.g. 503 then 200, `calls == 2`) and
    retry-transient-then-exhaust (503 always -> `LlmError`, `calls == max_attempts`)
    tests; monkeypatch `client_module.asyncio.sleep` to a no-op recorder.
  - Add a client-reuse test (two calls on one instance construct `httpx.AsyncClient`
    once) and an `aclose` test (closes and resets; the next call rebuilds).

## 7. Acceptance criteria

- Concurrent `ensure_session` for a new `(provider, identifier)` cannot surface
  an `IntegrityError`.
- Two providers may share an `identifier`; the same provider+identifier reuses
  one user row. Migration `007` performs the constraint swap.
- `consume_llm_budget(cost=0)` / `(cost=-1)` raise `ValueError`.
- Transient statuses retry with backoff/`Retry-After`; non-transient fail on the
  first attempt.
- A single `httpx.AsyncClient` is reused across LLM turns and closed on shutdown.

## 8. Suggested commits

1. `fix(db): scope user identity to provider and recover user insert races`
2. `fix(llm): validate budget cost, retry transient statuses, reuse the HTTP client`

## 9. Risks

- Migration `007` is not exercised locally (no Postgres); it runs only under the
  integration smoke. The constraint lookup falls back to the documented default
  name if the catalog query is inconclusive.
- Holding one long-lived `AsyncClient` requires the `aclose()` wiring to run;
  missing shutdown leaves the pool to be reclaimed at process exit (acceptable).
- `Retry-After` parsing ignores HTTP-date values (numeric seconds only); the
  exponential fallback covers those.
