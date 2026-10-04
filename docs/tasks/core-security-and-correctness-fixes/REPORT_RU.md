# REPORT_RU.md — core-security-and-correctness-fixes

Task spec: `docs/tasks/core-security-and-correctness-fixes/TZ_RU.md`.

## Goal

Fix five audited defects in the core, in two logical commits:

- **A** — a valid session cookie bypassed `REQUIRE_TLS` (and its security events)
  in `/api/auth/session`; re-validate it through `require_admin_token` while
  keeping the Bearer fallback intact.
- **B** — Dockerized plugin enable/disable state lived outside the `loomdata`
  volume, so it was lost on container recreate; default it to
  `/data/plugins.json`.
- **C** — plugin tool calls used a fixed 30s timeout and `proc.kill()` left
  orphaned grandchildren; add per-tool timeouts from `TOOLS_SCHEMA` and kill the
  whole process group.
- **D** — a failing parallel plan step returned early, leaving sibling steps
  stuck in `running` and never reporting their outcomes.
- **E** — `ops_run_command.command` accepted only a string; accept an argv list
  too (`anyOf`), with no change to the command splitter.

Scope was limited to these five findings; no new plugins, DB/migration changes,
frontend work, or weakening of TLS, approvals, audit or the agent safety
invariant.

## Delivered

### Part A — TLS on the session cookie (`src/api/auth.py`)

In `auth_session`, a cookie that passes `is_valid_admin_credential` is now also
run through `require_admin_token(cookie, request)`, so `REQUIRE_TLS` is enforced
and a security event is emitted exactly like the Bearer path. The Bearer
fallback below is unchanged, and `is_valid_admin_credential` itself was not
touched (it is still used for rate-limit keying, where TLS must not apply). A
stale/invalid cookie still falls through to a valid Bearer token.

### Part B — plugin state on the volume

- `docker-compose.yml`: `PLUGINS_STATE_FILE` now defaults to
  `${PLUGINS_STATE_FILE:-/data/plugins.json}` (the existing `loomdata` volume),
  so a disabled plugin survives container recreate.
- `.env.example`: comment updated to explain the Compose default
  (`/data/plugins.json` on `loomdata`) while the value stays empty for the local
  default `<plugins_dir>/../.loomcore/plugins.json`.
- Docs aligned in the same commit: `AGENTS.md`, `docs/setup/VDS_RUNBOOK.md` and
  `docs/setup/DEPLOY.md` (backup note). `resolve_state_path` itself was already
  correct and was not changed.

### Part C — per-tool timeout + process-group kill (`src/plugins/manager.py`)

- New module constants `DEFAULT_TOOL_TIMEOUT = 30.0` and
  `MAX_TOOL_TIMEOUT = 600.0`, plus `_normalize_timeout` (rejects bool,
  non-numeric and `<= 0` with a warning; clamps positives to 600).
- `_index_tools` caches a valid declared `timeout` in `self._tool_timeouts`;
  `_rebuild_tool_cache` purges stale entries.
- `execute_tool` resolves `call_timeout` per tool (default 30s) and passes it to
  `_exchange`; the `TimeoutError` message now reports the real value.
- `_spawn` launches the plugin in its own session (`start_new_session=True` on
  POSIX, `CREATE_NEW_PROCESS_GROUP` on Windows) with explicit UTF-8 decoding.
- New `_kill_process_tree` sends `SIGKILL` to the process group on POSIX (falls
  back to `proc.kill()`), used by `_kill_and_stop`; `_terminate_process` sends
  `SIGTERM` to the group first, then kills the tree on timeout.
- Plugin schemas declare real budgets: LoomWG `wg_install_dependencies` and
  `wg_setup_server` = 360, `wg_interface_start`/`restart` and
  `wg_service_enable`/`disable` = 90; LoomOps `ops_run_command` = 600,
  `ops_scan_health` = 120, and the journal/dmesg/units/systemctl/network tools =
  60.
- `docs/architecture/PLUGIN_MANIFEST.md` documents the optional tool `timeout`
  next to `risk`/`report`.

### Part D — process every parallel outcome (`src/core/agent.py`)

The concurrent branch no longer returns on the first failing step: successes are
emitted `done` and added to `results`, the failure marks the step `failed` and
the plan `failed`, and no step is left `running`. Execution still stops before
the next run, and the serial branch and approval invariant are untouched.

### Part E — argv-list command (`plugins/LoomOps/LoomOps/schemas.py`)

`ops_run_command.command` is now
`anyOf: [string, array of strings]` and the tool description mentions the argv
form. The existing command splitter was not changed.

### Documentation

`CHANGELOG.md` gained a `## 2026-10-04` section with one entry per fix;
`AGENTS.md`, `docs/setup/VDS_RUNBOOK.md`, `docs/setup/DEPLOY.md` and
`docs/architecture/PLUGIN_MANIFEST.md` were updated alongside the behaviour.

## Verification

- `python -m ruff check .` — clean ("All checks passed!"), run before and after
  the suite.
- `pytest tests/test_agent.py tests/test_plugin_manager.py tests/test_api_auth.py tests/test_plugin_state.py tests/test_contracts.py tests/test_LoomOps_tools.py -q`
  — 322 passed, 9 skipped.
- `python -m pytest -m "not integration and not e2e" -q` — 1395 passed, 13
  skipped, 5 deselected (baseline 1370 passed, 12 skipped). Full log in
  `tmp/tests_fast.log`.
- `docker-compose.yml` parsed as valid YAML; the `hub` env value resolves to
  `${PLUGINS_STATE_FILE:-/data/plugins.json}`.
- New tests cover the Part A 403/200 matrix, the `/data/plugins.json` path
  resolution (no Docker execution), timeout parsing/clamp/purge, per-tool
  `call_timeout` propagation, POSIX process-group signaling (skipped on
  Windows), all-parallel-outcomes reporting, and argv-list acceptance/rejection
  through `execute_tool_by_name`.

## Changed files

- `src/api/auth.py` — re-validate a valid cookie via `require_admin_token`.
- `src/plugins/manager.py` — per-tool timeouts, process-group spawn/kill.
- `src/core/agent.py` — process every concurrent outcome.
- `docker-compose.yml` — `PLUGINS_STATE_FILE` default `/data/plugins.json`.
- `.env.example` — document the Compose default; value stays empty.
- `AGENTS.md` — document the Docker state path.
- `docs/setup/VDS_RUNBOOK.md` — document the Docker state path.
- `docs/setup/DEPLOY.md` — back up the plugin state on `loomdata`.
- `docs/architecture/PLUGIN_MANIFEST.md` — document the tool `timeout` field.
- `plugins/LoomOps/LoomOps/schemas.py` — tool timeouts + `anyOf` command.
- `plugins/LoomWG/LoomWG/schemas.py` — tool timeouts.
- `tests/test_api_auth.py` — TLS/cookie/Bearer matrix.
- `tests/test_plugin_state.py` — Docker volume path cases.
- `tests/test_plugin_manager.py` — timeout indexing + process-group kill.
- `tests/test_agent.py` — all-parallel-outcomes on first failure.
- `tests/test_LoomOps_tools.py` — timeouts + argv-list contract tests.
- `tests/test_LoomWG_unit.py` — schema timeout assertions.
- `CHANGELOG.md` — dated entries for both fixes.

## Commits

None — the executor does not commit; the coordinator commits the two labelled
changesets (Commit 1: session-cookie TLS + volume state; Commit 2: per-tool
timeouts, process-group kill, parallel-plan results, argv-list command).

## Notes and follow-ups

- Windows cannot kill a process group; `_kill_process_tree` is best-effort
  there, and the real-process assertions are POSIX-only (skipped on Windows).
- Timeout numbers track the plugins' current internal budgets; raising a plugin
  budget again means revisiting its declared `timeout` (core value = budget +
  ~30s margin, capped at 600).
- The TLS tests drive `is_https` through the ASGI scheme/`x-forwarded-proto`
  header rather than a real socket.
- The double credential check on a valid cookie (validate, then
  `require_admin_token`) is intentional and only applied to a valid cookie.
- The Compose env default takes effect on the next container recreate.

Commits created: none
