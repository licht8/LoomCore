# TZ_RU.md — deployment safety hardening

> Copy to `docs/tasks/<kebab-name>/TZ_RU.md` and fill in **before** starting
> (RULES 16). Content is English (RULES 0). Keep it specific and testable.

## 1. Goal

Close three deployment-safety gaps found in the project audit:

- **`.env.example` ships publicly-known credential placeholders.** With
  `ADMIN_PASSWORD_HASH` and `ADMIN_TOTP_SECRET` both set to the placeholder
  strings, `admin_jwt_mode_enabled()` is True (`src/core/security.py:38-42`) and
  the admin JWT is verified against the public `SESSION_SECRET`
  (`src/core/security.py:53-58`). A deployment that copies `.env.example` and
  edits only `POSTGRES_PASSWORD` therefore exposes a forgeable admin JWT.
- **Documented `.env` knobs are never passed by compose, and one is read from
  the wrong source.** `docker-compose.yml` never forwards
  `LOOMCORE_PLUGINS_ALLOWED`, `TRUSTED_PROXIES` or `LOOMCORE_REPORT_COMMANDS`,
  so the plugin trust boundary and the report-command allow-list cannot be
  configured in the default deployment. Separately, `allowed_plugins()` reads
  `os.environ` directly (`src/plugins/manager.py:40-53`), so the value is
  ignored when supplied via a plain `.env` file outside compose.
- **Plugin subprocesses are never terminated.** The lifespan `finally`
  (`src/app.py:96-110`) cancels the healthcheck and closes LLM clients but never
  stops the plugin children spawned by `_spawn` (`src/plugins/manager.py:159-168`);
  a non-container `uvicorn` run or reload leaks plugin processes.

## 2. Non-goals

- No change to the auth flow, JWT format, TOTP, cookie flags or rate limiting.
- No new plugin and no manifest schema change.
- The audit items "cache-served `/api/tools`" and "WS session-id validation"
  are a separate TZ (`tools-cache-and-ws-session-validation`).
- No secret-generation helper beyond documenting the existing
  `python -m src.auth <password>`.

## 3. Approved decisions (locked)

### D1 — placeholder credentials fail closed

- `.env.example`: set `ADMIN_PASSWORD_HASH=`, `ADMIN_TOTP_SECRET=` and
  `SESSION_SECRET=` to empty, with comments pointing at
  `python -m src.auth <password>` and noting that JWT stays off while they are
  empty. Keep `ADMIN_USERNAME=admin`.
- `src/core/startup_checks.py`: add `_check_placeholder_credentials()` and call
  it from `check_security_posture()`. It raises `RuntimeError` when any of
  `settings.admin_password_hash`, `settings.admin_totp_secret`,
  `settings.session_secret` or `settings.admin_token` equals a known shipped
  placeholder. The placeholder set (exact, case-sensitive) is
  `{"generate-with-the-auth-helper", "base32-secret",
  "generate-a-long-random-session-secret"}`. Empty values are always allowed.
- Rationale: these strings are public; a deployment that edits only the DB
  password must not verify JWTs against a known key. The check runs inside
  `check_security_posture()` (already invoked via `asyncio.to_thread` in the
  lifespan), matching the `_check_tls` fail-closed pattern
  (`src/core/startup_checks.py:57-63`).

### D2 — compose passes every documented knob; allow-list comes from Settings

- `src/config.py`: add `plugins_allowed: str = Field(default="",
  validation_alias="LOOMCORE_PLUGINS_ALLOWED")`.
- `src/plugins/manager.py`: `allowed_plugins()` reads `settings.plugins_allowed`
  (add `from src.config import settings`) and keeps the existing comma/space
  parsing. No other behavior change.
- `docker-compose.yml` `hub.environment`: add
  `LOOMCORE_PLUGINS_ALLOWED: ${LOOMCORE_PLUGINS_ALLOWED:-}`,
  `TRUSTED_PROXIES: ${TRUSTED_PROXIES:-127.0.0.1,::1}` and
  `LOOMCORE_REPORT_COMMANDS: ${LOOMCORE_REPORT_COMMANDS:-}`.

### D3 — terminate plugin subprocesses on shutdown

- `src/plugins/manager.py`: add a sync, blocking `PluginManager.terminate_all()`
  that terminates every registered plugin process (reuse `_terminate_process`,
  `src/plugins/manager.py:593-600`), then sets `plugin.proc = None` and
  `plugin.state = PluginState.STOPPED`. It must never raise (log and continue on
  error).
- `src/app.py` lifespan `finally`: after the healthcheck task is stopped, call
  `with contextlib.suppress(Exception): await asyncio.to_thread(plugin_manager.terminate_all)`.

## 4. Data model / interfaces

- `Settings` gains one field: `plugins_allowed: str` (env
  `LOOMCORE_PLUGINS_ALLOWED`, default `""`). No API, DB or WS change.
- `PluginManager.terminate_all() -> None` is the only new public method.

## 5. Parts

1. **Credential placeholders** — `.env.example` (empty the three values + comments)
   and `src/core/startup_checks.py` (`_check_placeholder_credentials` + call it
   in `check_security_posture`).
2. **Env passthrough** — `src/config.py` (`plugins_allowed`),
   `src/plugins/manager.py` (`allowed_plugins()` from settings),
   `docker-compose.yml` (three environment keys).
3. **Plugin shutdown** — `src/plugins/manager.py` (`terminate_all`),
   `src/app.py` (lifespan finally).

## 6. Tests

- `tests/test_startup_checks.py`: add `test_placeholder_credentials_raise` (set
  each placeholder via `monkeypatch.setattr(settings, ...)`, assert
  `check_security_posture()` raises `RuntimeError`) and
  `test_real_credentials_do_not_raise` (non-placeholder values pass). Follow the
  existing style (monkeypatch settings attrs, `tmp_path`, `caplog`); patch
  `loomcore_revoked_file` to a temp path and keep the LLM unconfigured.
- `tests/test_plugin_manager.py`: add `test_allowed_plugins_reads_settings`
  (monkeypatch `manager.settings.plugins_allowed`); UPDATE the existing
  env-driven tests (lines ~1375-1436) and the autouse `delenv` helper (line ~20)
  to patch `settings.plugins_allowed` instead of `os.environ`. Add
  `test_terminate_all_stops_plugins` using a fake `proc` exposing `poll()` ->
  None, `terminate()` and `wait(timeout=...)`; assert it was terminated and that
  `plugin.proc is None` and `plugin.state == PluginState.STOPPED`.
- `tests/test_app.py`: lightweight guard that `plugin_manager.terminate_all` is
  a callable (full lifespan assertions not required).
- Gates: `uv run ruff check .`; `uv run pytest -m "not integration and not e2e" -q`.

## 7. Acceptance criteria

- Copying `.env.example` without setting the three credentials can no longer
  enable JWT verification: startup raises with the placeholder values present,
  and JWT mode stays off when they are empty.
- `docker compose config` lists `LOOMCORE_PLUGINS_ALLOWED`, `TRUSTED_PROXIES`
  and `LOOMCORE_REPORT_COMMANDS` in the `hub` environment.
- Setting `LOOMCORE_PLUGINS_ALLOWED` in a plain `.env` (non-compose run) now
  restricts discovery (previously ignored).
- On lifespan shutdown every registered plugin `proc` is terminated and
  `terminate_all()` does not raise even when a process is already dead.
- `uv run ruff check .` clean; `uv run pytest -m "not integration and not e2e" -q`
  green.

## 8. Commits

1. `fix(core): reject shipped credential placeholders at startup`
2. `fix(deploy): pass documented env knobs and read the plugin allow-list from settings`
3. `fix(plugins): terminate plugin subprocesses on application shutdown`

## 9. Risks

- The placeholder check is a hard startup failure: an existing deployment that
  currently runs with the shipped placeholders will refuse to start until real
  values are set. That is intended (fail closed) and operators must generate
  credentials.
- Moving `LOOMCORE_PLUGINS_ALLOWED` into `Settings` changes when it is read
  (import-time cached singleton vs per-call `os.environ`). Tests that mutated
  `os.environ` must be updated; runtime behavior is unchanged when the env var is
  set normally.
- `terminate_all()` blocks: it must run via `asyncio.to_thread` and be wrapped
  in `suppress`, so a stuck plugin cannot block or crash shutdown beyond the 5s
  wait + kill in `_terminate_process`.
