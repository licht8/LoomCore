# TZ_RU.md — DB prepared-statement hardening

Task id: `db-prepared-statement-hardening`. Follow RULES.md (English only, no
comments unless existing style has them, ruff clean, tests mandatory). Executor
does NOT commit; the coordinator commits after acceptance (RULES 0a).

## 1. Goal

Eliminate the `InvalidCachedStatementError` a running hub raises on the first
query after `deploy/install.sh` applies migrations:

```
NotSupportedError: asyncpg.exceptions.InvalidCachedStatementError:
cached statement plan is invalid due to a database schema or configuration change
[SQL: SELECT sessions.id, sessions.user_id, sessions.status FROM sessions WHERE sessions.id = $1::VARCHAR]
```

Root cause: `deploy/install.sh` starts the hub (`docker compose up -d --build
--wait`) and **only then** runs `docker compose exec -T hub alembic upgrade head`
in a separate process. The long-running hub keeps pooled asyncpg connections
whose cached prepared statements were planned against the pre-migration schema.
When `alembic` alters a table the hub already has a plan for (`sessions`, touched
by `006_session_id_length`), Postgres rejects re-execution with SQLSTATE `0A000`
("cached plan must not change result type"). SQLAlchemy clears its caches in
response (so it self-heals) but cannot retry — the failed transaction is dead, so
one user-visible request fails.

Fix at both ends: stop the app from ever triggering it (deploy order) **and**
make the app immune to external DDL (no prepared-plan reuse).

## 2. Non-goals

- No schema change and no new Alembic migration.
- No change to query logic, models, auth, plugins, or the REST/WS surface.
- No PgBouncer / connection-pooler setup.
- No change to `alembic/env.py` (its `pool.NullPool` stays).
- No generic retry-on-error wrapper.

## 3. Approved decisions (locked)

### D1 — disable asyncpg prepared-statement caches (`src/db/database.py`)

```python
ENGINE_CONNECT_ARGS = {"statement_cache_size": 0, "prepared_statement_cache_size": 0}

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    connect_args=dict(ENGINE_CONNECT_ARGS),
)
```

Both layers must be disabled:
- `prepared_statement_cache_size` is SQLAlchemy's own per-DBAPI-connection cache
  (default 100); the asyncpg dialect pops it before calling `asyncpg.connect`.
- `statement_cache_size` is asyncpg's internal cache (default 100); the dialect
  forwards it to `asyncpg.connect`, so it must be passed as a `connect_args` key.

With both at `0`, each execution prepares fresh and no stale plan can be reused,
so `InvalidCachedStatementError` cannot be raised. Add a short comment stating
the race and that the statement-preparation cost is negligible for this workload.
Expose `ENGINE_CONNECT_ARGS` at module level so it is testable without a DB.

### D2 — apply migrations before the hub starts (`deploy/install.sh`)

Postgres service is named `db` (container `loom_db`); the hub service is `hub`.
Replace the current block:

```bash
docker compose up -d --build --wait
docker compose ps

echo "=== Applying database migrations ==="
docker compose exec -T hub alembic upgrade head
```

with:

```bash
echo "=== Building and starting the database ==="
docker compose build
docker compose up -d --wait db

echo "=== Applying database migrations ==="
docker compose run --rm hub alembic upgrade head

echo "=== Starting LoomCore ==="
docker compose up -d --wait
docker compose ps
```

`docker compose run --rm hub alembic upgrade head` overrides the hub command
(`uvicorn ...`) with `alembic upgrade head`, starts `db` via `depends_on`, and
exits; the long-running hub then starts against the already-migrated schema.
Leave the "no `.env` -> create and exit" branch unchanged.

### D3 — docs + changelog

- `docs/setup/DEPLOY.md` and `docs/setup/VDS_RUNBOOK.md`: state that migrations
  run **before** the hub starts, and that applying a migration manually must be
  followed by `docker compose restart hub`.
- `CHANGELOG.md`: one short dated entry (RULES 14).

## 4. Data model / interfaces

None.

## 5. Parts

1. `src/db/database.py` — D1.
2. `deploy/install.sh` — D2.
3. `docs/setup/DEPLOY.md`, `docs/setup/VDS_RUNBOOK.md`, `CHANGELOG.md` — D3.

## 6. Tests

- New `tests/test_db_database.py` (no live DB — `create_async_engine` does not
  connect until first use):
  - monkeypatch `sqlalchemy.ext.asyncio.create_async_engine` to capture kwargs,
    `importlib.reload` `src.db.database`, and assert
    `kwargs["connect_args"]["statement_cache_size"] == 0`,
    `kwargs["connect_args"]["prepared_statement_cache_size"] == 0`, and
    `kwargs["echo"] == settings.debug`; restore the real module afterward.
  - assert `src.db.database.ENGINE_CONNECT_ARGS ==
    {"statement_cache_size": 0, "prepared_statement_cache_size": 0}`.
- Existing suite stays green (no DB required).
- Static check (report only): `deploy/install.sh` runs `alembic upgrade head`
  before the final `docker compose up -d --wait`.

Gate commands:

```
uv run ruff check .
uv run pytest -m "not integration and not e2e"
```

## 7. Acceptance criteria

- `src/db/database.py` builds the engine with both caches disabled; observable
  app behaviour is otherwise unchanged.
- `deploy/install.sh` migrates before the hub starts.
- New test passes; full fast suite green; `ruff check .` clean.
- Docs and CHANGELOG updated.

## 8. Commits (coordinator commits only after acceptance, RULES 0a)

1. `fix(db): disable asyncpg prepared-statement caches to survive live DDL`
2. `fix(deploy): apply migrations before starting the hub`
3. `docs: document the migrate-before-hub deploy order`

## 9. Risks

- Small statement-preparation overhead from disabling caching; acceptable here.
- The two-step `up` is idempotent: the second `docker compose up -d --wait`
  starts hub/frontend/nginx; `run --rm` needs the hub image built first (added
  `build` step) and `db` healthy.
- Anyone altering the schema outside migrations must restart the hub; documented.
- Integration/e2e needs Postgres/Linux and is out of scope for CI here.
