# REPORT_RU.md — DB prepared-statement hardening

Task spec: `docs/tasks/db-prepared-statement-hardening/TZ_RU.md`.

## Goal

Stop the running hub from raising `InvalidCachedStatementError` on the first
query after `deploy/install.sh` applies migrations, by fixing both the deploy
order (never keep the hub connected during a migration) and the engine (never
reuse a prepared plan that external DDL can invalidate).

## Delivered

### D1 — `src/db/database.py`

- Added a module-level
  `ENGINE_CONNECT_ARGS = {"statement_cache_size": 0, "prepared_statement_cache_size": 0}`
  and pass `connect_args=dict(ENGINE_CONNECT_ARGS)` to `create_async_engine`
  (with the existing `echo=settings.debug` unchanged).
- Both caches are disabled: `prepared_statement_cache_size` is SQLAlchemy's
  per-DBAPI-connection cache, `statement_cache_size` is asyncpg's internal one.
  Each execution now prepares fresh, so `InvalidCachedStatementError` (SQLSTATE
  `0A000`) cannot be raised for a plan invalidated by DDL. A short comment states
  the race and that the extra preparation cost is negligible here.
- `ENGINE_CONNECT_ARGS` is exported so it is testable without a database.

### D2 — `deploy/install.sh`

- Replaced the single `docker compose up -d --build --wait` + late
  `exec hub alembic upgrade head` with:
  `docker compose build` -> `docker compose up -d --wait db` ->
  `docker compose run --rm hub alembic upgrade head` -> `docker compose up -d --wait`
  -> `docker compose ps`.
- The hub therefore starts only after the schema is migrated; `run --rm` builds
  on the image from `build` and needs `db` healthy. The "no `.env` -> create and
  exit" branch is unchanged.

### D3 — docs + changelog

- `docs/setup/DEPLOY.md` — Step 1 now lists the new script order; Step 2 states
  migrations run before the hub starts, uses `docker compose run --rm hub alembic
  upgrade head`, and says a manual migration against a running hub must be
  followed by `docker compose restart hub`; the Update block now mirrors the
  migrate-before-start order.
- `docs/setup/VDS_RUNBOOK.md` — the manual equivalent (section 2), the migrations
  section (section 3) and the Update block (section 9) now use the
  migrate-before-hub order and document `docker compose restart hub` after a
  manual migration against a running hub.
- `CHANGELOG.md` — dated 2026-09-22 entry for the engine cache change and the
  deploy-order change.

## Tests

New `tests/test_db_database.py` (no live DB; `create_async_engine` does not
connect):

- `test_engine_connect_args_constant` asserts `ENGINE_CONNECT_ARGS ==
  {"statement_cache_size": 0, "prepared_statement_cache_size": 0}`.
- `test_engine_disables_prepared_statement_caches` monkeypatches
  `sqlalchemy.ext.asyncio.create_async_engine` to capture kwargs,
  `importlib.reload`s `src.db.database`, and asserts the URL, `echo ==
  settings.debug` and both `connect_args`, then `monkeypatch.undo()` +
  reloads the real module in `finally`.

## Verification

- `uv run ruff check .` — clean.
- `uv run pytest tests/test_db_database.py -q` — 2 passed.
- `uv run pytest -m "not integration and not e2e" -q` — 1043 passed, 12 skipped,
  5 deselected (`tmp/tests_db_hardening_full.log`; 1041 pre-existing + 2 new).
- Static check: `deploy/install.sh` lines 40-47 show `docker compose build` ->
  `up -d --wait db` -> `run --rm hub alembic upgrade head` -> `up -d --wait`,
  i.e. migrations run before the final stack start.

## Review fix round 1 (`deploy/install.sh`)

Two findings, both applied:

1. Re-run safety: `docker compose build` + `up -d --wait db` left an already-running
   `hub` untouched, so on an update the old hub stayed connected during `alembic
   upgrade head` — the exact race. Added `docker compose stop hub 2>/dev/null ||
   true` before bringing up `db`; the final `up -d --wait` restarts it.
2. Non-interactive run: `docker compose run` allocates a TTY/STDIN by default, which
   breaks unattended runs (CI/cron, `bash deploy/install.sh </dev/null`, `curl … |
   bash`) and injects CR/LF/ANSI into captured logs. Restored the previous behaviour
   with `docker compose run --rm -T hub alembic upgrade head`.

Verification: `uv run ruff check .` clean; LF-normalized `bash -n deploy/install.sh`
-> syntax OK; static grep shows `build` -> `stop hub` -> `up -d --wait db` -> `run
--rm -T hub alembic upgrade head` -> `up -d --wait`. Only `deploy/install.sh`
changed (no Python/frontend), so the full-suite result above stands.

## Changed files

- `src/db/database.py`
- `deploy/install.sh`
- `tests/test_db_database.py` (new)
- `docs/setup/DEPLOY.md`, `docs/setup/VDS_RUNBOOK.md`, `CHANGELOG.md`

## Commits

None (executor does not commit; the coordinator commits after acceptance).

## Notes and follow-ups

- Disabling the caches adds a small per-statement preparation cost; negligible
  for this workload (locked decision D1).
- The two-step start is idempotent; `docker compose run --rm hub` needs the hub
  image built first (hence the explicit `build`) and `db` healthy.
- Anyone applying schema changes outside Alembic must still restart the hub;
  documented in DEPLOY.md and VDS_RUNBOOK.md.
- Integration/e2e requires Postgres/Linux and stays out of scope.
