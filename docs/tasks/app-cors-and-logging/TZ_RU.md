# TZ_RU.md — app CORS origins setting and logger-based startup messages

## 1. Goal

Make src/app.py fully settings-driven and log-clean:

1. The CORS allow-list is hardcoded to the two loopback frontend origins while
   every other runtime knob is a `Settings` field. A real (non-localhost)
   frontend origin is silently blocked by the browser and there is no env switch.
   Add a `cors_origins` list setting (defaulting to the current localhost
   entries) and pass it to `CORSMiddleware`.
2. Startup/shutdown progress is emitted with `print()`, bypassing the
   `loomcore` logger configured by `_configure_logging()`, so it cannot be
   silenced by log level. Emit the same messages via `logger.info(...)`.

## 2. Non-goals

- No change to logging handlers, levels, or `_configure_logging()` behaviour
  (startup INFO stays hidden in normal runs, which is intended).
- No change to the `logger.warning(f"Failed to prime plugin tools ...")` branch
  beyond leaving it as is (f-string style is not part of this finding).
- No nginx, frontend, or auth changes.
- No new CORS behaviour (still `allow_credentials=True`, wildcard methods and
  headers).

## 3. Approved decisions (locked)

- The setting is a **list** named `cors_origins` on `Settings`, passed directly
  as `allow_origins=settings.cors_origins`.
- The default list is exactly
  `["http://localhost:3000", "http://127.0.0.1:3000"]`.
- Env parsing is **comma/space separated** (e.g.
  `CORS_ORIGINS=https://hub.example.com,https://alt.example.com`), matching the
  existing `TRUSTED_PROXIES` convention, not JSON. This is required for the
  Docker deployment, where `docker-compose.yml` passes each variable as
  `${VAR:-default}`. Implement with `Annotated[list[str], NoDecode]` plus a
  `mode="before"` `field_validator` that splits a string on commas/whitespace
  and passes a list through unchanged.
- The compose default and `.env.example` value are
  `http://localhost:3000,http://127.0.0.1:3000`.
- Emojis in the three startup messages are dropped (plain ASCII, consistent with
  the `%(levelname)s %(name)s: %(message)s` formatter).

## 4. Data model / interfaces

- `src/config.py`: new `cors_origins: Annotated[list[str], NoDecode]` field on
  `Settings` (env `CORS_ORIGINS`). No DB or API schema change.
- `docker-compose.yml`: the `hub` service gets
  `CORS_ORIGINS: ${CORS_ORIGINS:-http://localhost:3000,http://127.0.0.1:3000}`
  in its `environment:` block, otherwise the variable never reaches the
  container.
- `.env.example`: document `CORS_ORIGINS` with the localhost default.

## 5. Parts

1. **`src/config.py`** — import `Annotated` (from `typing`), `NoDecode` (from
   `pydantic_settings`), and `field_validator` (extend the existing
   `from pydantic import ...` line). Add the `cors_origins` field next to
   `trusted_proxies` with the locked default, and a `field_validator`
   `_split_cors_origins(value)` that returns `value` unchanged unless it is a
   `str`, in which case it returns
   `[item for item in value.replace(",", " ").split() if item]`. Keep the
   existing `model_config` and validator untouched.
2. **`src/app.py`** — `allow_origins=settings.cors_origins` (delete the literal
   list). Replace the three `print(...)` calls in `lifespan` with
   `logger.info("LoomCore starting up")`,
   `logger.info("Plugins loaded: %s", ", ".join(discovered) or "none")`, and
   `logger.info("Tools indexed: %d", len(tools))`. `logger` and
   `_configure_logging()` already exist; do not add a second logger.
3. **`docker-compose.yml`** — add the `CORS_ORIGINS` line to the `hub`
   `environment:` block (place it after `REQUIRE_TLS`).
4. **`.env.example`** — add a short comment and the `CORS_ORIGINS` line
   (comma-separated, localhost default).
5. **Tests** — see section 6.

## 6. Tests

- `tests/test_config.py`
  - `test_cors_origins_default_to_localhost`: `_settings().cors_origins ==
    ["http://localhost:3000", "http://127.0.0.1:3000"]`.
  - `test_cors_origins_parsed_from_comma_separated_env`: `monkeypatch.setenv(
    "CORS_ORIGINS", "https://hub.example.com, https://alt.example.com")` then
    `_settings().cors_origins == ["https://hub.example.com",
    "https://alt.example.com"]`.
  - `test_cors_origins_accepts_a_list_directly`: `_settings(cors_origins=
    ["https://hub.example.com"]).cors_origins == ["https://hub.example.com"]`.
- `tests/test_app.py` (new)
  - `test_cors_middleware_uses_settings_origins`: monkeypatch
    `settings.cors_origins` to `["https://hub.example.com"]`, call
    `src.app.create_app()`, locate the `CORSMiddleware` entry in
    `app.user_middleware` (`m.cls is CORSMiddleware`) and assert
    `m.kwargs["allow_origins"] == ["https://hub.example.com"]`.
- `tests/test_smoke_plugins.py`
  - `test_lifespan_emits_startup_progress_through_logger(monkeypatch, tmp_path,
    caplog, capsys)`: stub `plugin_manager.discover_plugins -> ["demo"]` and
    `get_all_tools -> [{"name": "demo_tool"}]`; inside
    `caplog.at_level(logging.INFO, logger="loomcore.app")` enter
    `app_module.lifespan(app)`; assert `"LoomCore starting up"`,
    `"Plugins loaded: demo"` and `"Tools indexed: 1"` are in `caplog.text` and
    that none of those strings appear in `capsys.readouterr().out` (proves the
    messages go through logging, not `print`).

Gate commands:

```bash
uv run ruff check .
uv run pytest tests/test_config.py tests/test_app.py tests/test_smoke_plugins.py -q
uv run pytest -m "not integration and not e2e" -q
```

## 7. Acceptance criteria

- `settings.cors_origins` exists, defaults to the two localhost origins, and is
  used by `create_app()` (`allow_origins=settings.cors_origins`).
- Setting `CORS_ORIGINS=a,b` (and a Python list) both produce the expected list.
- No `print(` remains in `src/app.py`; the three startup messages are emitted at
  INFO via the `loomcore.app` logger and nothing is written to stdout.
- `docker-compose.yml` and `.env.example` expose the new variable.
- All gate commands pass.

## 8. Commits

1. `feat(config): add configurable cors_origins setting`
   (`src/config.py`, `src/app.py`, `docker-compose.yml`, `.env.example`,
   `tests/test_config.py`, `tests/test_app.py`)
2. `fix(app): log startup progress instead of printing`
   (`src/app.py`, `tests/test_smoke_plugins.py`)

(The coordinator may combine them into one commit if the same lines in
`src/app.py` are touched by both parts.)

## 9. Risks

- `NoDecode` is available in the pinned `pydantic-settings` (2.15.0); if the
  import shape differs, the fallback is to keep the field a comma-separated
  `str` and split in `src/app.py` — not expected.
- An empty `CORS_ORIGINS=` yields `[]` (no cross-origin allowed), which is an
  explicit opt-out, not a crash; the default stays intact when the var is unset.
- The lifespan logging test must keep stubbing the plugin manager, otherwise it
  would spawn real subprocesses; the pattern follows the existing
  `tests/test_smoke_plugins.py` lifespan tests.
