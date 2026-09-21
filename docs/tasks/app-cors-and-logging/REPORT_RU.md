# REPORT_RU.md — app CORS origins setting and logger-based startup messages

Task spec: `docs/tasks/app-cors-and-logging/TZ_RU.md`.

## Goal

1. `src/app.py` hardcoded the CORS allow-list to the two loopback frontend
   origins while every other runtime knob is a `Settings` field, so a real
   (non-localhost) frontend origin was silently blocked with no env switch.
2. Startup progress was emitted with `print()`, bypassing the `loomcore` logger
   configured by `_configure_logging()`, so it could not be silenced by log
   level.

## Delivered

- `src/config.py`:
  - Imports `Annotated` (`typing`), `NoDecode` (`pydantic_settings`), and
    `field_validator` (extended the existing `pydantic` import).
  - New `cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000",
    "http://127.0.0.1:3000"]` placed next to `trusted_proxies`.
  - New `@field_validator("cors_origins", mode="before")` `_split_cors_origins`
    that splits a raw string on commas/whitespace (matching the
    `TRUSTED_PROXIES` convention, needed for compose) and passes a list through
    unchanged. Existing `model_config`/`model_validator` untouched.
- `src/app.py`:
  - `allow_origins=settings.cors_origins` (literal list deleted).
  - The three `print()` calls in `lifespan` replaced with
    `logger.info("LoomCore starting up")`,
    `logger.info("Plugins loaded: %s", ", ".join(discovered) or "none")`, and
    `logger.info("Tools indexed: %d", len(tools))` (emojis dropped). Reused the
    existing `logger`; the `logger.warning(f"Failed to prime plugin tools...")`
    branch left as is. No `print(` remains in the module.
- `docker-compose.yml`: added
  `CORS_ORIGINS: ${CORS_ORIGINS:-http://localhost:3000,http://127.0.0.1:3000}` to
  the `hub` `environment:` block (right after `REQUIRE_TLS`).
- `.env.example`: added a comment plus `CORS_ORIGINS=` with the comma-separated
  localhost default.
- Tests:
  - `tests/test_config.py`: `test_cors_origins_default_to_localhost`,
    `test_cors_origins_parsed_from_comma_separated_env` (via
    `monkeypatch.setenv`), `test_cors_origins_accepts_a_list_directly`.
  - `tests/test_app.py` (new): `test_cors_middleware_uses_settings_origins`
    (locates the `CORSMiddleware` entry in `app.user_middleware` and asserts
    `kwargs["allow_origins"]`).
  - `tests/test_smoke_plugins.py`:
    `test_lifespan_emits_startup_progress_through_logger` (stubs
    `discover_plugins`/`get_all_tools`, asserts the three messages in
    `caplog.text` and absent from `capsys` stdout).

## Verification

- `uv run ruff check .` — clean (`All checks passed!`).
- `uv run pytest tests/test_config.py tests/test_app.py tests/test_smoke_plugins.py
  -q` — 20 passed (log: `tmp/tests_app_cors.log`).
- `uv run pytest -m "not integration and not e2e" -q` — 924 passed, 11 skipped,
  5 deselected (log: `tmp/tests_app_cors_full.log`). Previous suite size was 919;
  the five new tests account for the delta.
- `rg "print\(" src/app.py` — no matches.
- Integration tests not run locally (no Postgres).

## Changed files

- `src/config.py` — `cors_origins` setting + env-splitting validator.
- `src/app.py` — CORS from settings; startup messages via `logger.info`.
- `docker-compose.yml` — `CORS_ORIGINS` on the `hub` service.
- `.env.example` — documented `CORS_ORIGINS`.
- `tests/test_config.py`, `tests/test_app.py`, `tests/test_smoke_plugins.py` —
  new tests.
- `docs/tasks/app-cors-and-logging/REPORT_RU.md` — this report.

## Notes and follow-ups

- `NoDecode` is present in the pinned `pydantic-settings`; the `Annotated`
  import shape worked as specified (no fallback needed).
- An empty `CORS_ORIGINS=` yields `[]` (no cross-origin allowed) — an explicit
  opt-out, not a crash; the default stays when the variable is unset.
- CORS semantics are unchanged beyond the origin list (`allow_credentials=True`,
  wildcard methods/headers). No changes to `_configure_logging`, logging
  levels/handlers, nginx, frontend, or auth.
- Startup INFO stays hidden in normal runs because `_configure_logging()` sets
  the `loomcore` base logger to WARNING unless DEBUG/`LOOMCORE_TIMING` is on
  (intended).
- Suggested commits (coordinator commits after acceptance — RULES 0a):
  1. `feat(config): add configurable cors_origins setting`
  2. `fix(app): log startup progress instead of printing`

Commits created: none
