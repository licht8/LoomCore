# TZ_RU.md — Core security/state persistence + plan/timeout correctness

> Spec for the executor (RULES 16). Content is English (RULES 0). Do not commit
> (RULES 0a). One spec, **two commits** — see §8.

## 1. Goal

Fix five verified defects found in the coordinator audit. Group them into two
logical commits:

- **Commit 1 — security / state persistence** (Parts A–B):
  - (A) `/api/auth/session` accepts a valid JWT session cookie over plain HTTP
    even when `REQUIRE_TLS=true`, bypassing the documented "every admin auth
    path refuses plain HTTP" invariant.
  - (B) Plugin enable/disable state is written to `/app/.loomcore/plugins.json`
    in the shipped Docker deployment, which is not a mounted volume, so it is
    lost on every `docker compose up -d --build` (a disabled plugin silently
    comes back).
- **Commit 2 — correctness** (Parts C–E):
  - (C) The core hard-caps every plugin tool call at 30 s while several shipped
    tools legitimately need longer, and on timeout only the direct child is
    killed (installed children survive as orphans).
  - (D) A failing step inside a parallel plan group aborts the group early:
    already-succeeded siblings are dropped from `results` and stay
    `status="running"` forever.
  - (E) `ops_run_command` advertises `command` as a `string`, so the documented
    argv-list form is rejected by the core's argument validation before it ever
    reaches the handler.

## 2. Non-goals

- Do NOT touch any other audit finding (memory compaction gating, `AIRouter`
  duplication, `list_pending_plans`, `clients.json` traversal, manifest
  `entry`/`internal_tools`, CORS, TOTP replay, frontend responsive/UX, docs
  hygiene, etc.). They are out of scope here.
- No new plugins, no DB schema/model/migration changes.
- No frontend changes.
- Do not change the agent safety invariant (non-safe steps still run only when
  the plan status is `approved`).
- Do not weaken `REQUIRE_TLS`, `SECURE_COOKIES`, approval gating or audit.

## 3. Approved decisions (locked)

1. **Per-tool timeout is declared by the plugin in `TOOLS_SCHEMA`** as an
   optional numeric `timeout` field (seconds), mirroring the existing `risk`
   and `report` declarations. The core reads it at `tools/list` indexing time,
   defaults to `30` when absent, and clamps to `[1, 600]`. A malformed value is
   ignored with a warning (falls back to the default), never fatal.
2. **Plugins are launched in their own session/process group** and are killed as
   a group, so a timed-out tool cannot leave grandchildren (e.g. `apt-get`,
   `wg-quick`, `journalctl`, `sleep`) running.
3. **`/api/auth/session` re-validates a *valid* cookie through
   `require_admin_token(cookie, request)`** so TLS enforcement and
   security-event logging apply. A stale/invalid cookie must still fall through
   to the Bearer branch (preserve the `auth-credential-precedence` behaviour: a
   bad cookie never shadows a good token).
4. **Docker plugin-state file default is `/data/plugins.json`** (on the existing
   `loomdata` volume). The local/non-Docker default is unchanged.
5. **`ops_run_command.command` accepts a string OR an array of strings**
   (`anyOf`). No core validator change is needed: `src/core/contracts.py` uses
   `jsonschema` `Draft202012Validator`, which supports `anyOf`.
6. **A failing parallel step marks that step `failed` and the plan `failed`, but
   every sibling outcome is still processed** (successes emitted `done` and
   included in `results`), and execution stops before the next run. No
   partial-swallow of results.

## 4. Data model / interfaces

- Tool metadata (from `tools/list`): new optional `timeout` number. No DB.
- No REST/WS event shape changes.
- `PLUGINS_STATE_FILE` (env) only; the Docker default changes.

## 5. Parts

### Part A — TLS-enforce the session-cookie branch (Commit 1)

Files: `src/api/auth.py`, `tests/test_api_auth.py`.

- In `auth_session` (`src/api/auth.py:117-137`), replace the bare
  `if is_valid_admin_credential(cookie): return {"authenticated": True}` with a
  form that first proves the cookie is genuine, then enforces TLS/logging:

  ```python
  cookie = request.cookies.get(SESSION_COOKIE)
  if is_valid_admin_credential(cookie):
      require_admin_token(cookie, request)  # TLS + security events
      return {"authenticated": True}
  ```

  Import `require_admin_token` from `src.core.security`. Do NOT change
  `is_valid_admin_credential` (it is used for rate-limit keying where TLS must
  not apply) and do NOT break the Bearer fallback.
- Add tests (under `REQUIRE_TLS=true`, JWT mode configured with a real
  `make_jwt_token`/login helper already used in `tests/test_api_auth.py`):
  - valid cookie + plain HTTP (`scheme http`, no `x-forwarded-proto`) → `403`;
  - valid cookie + `x-forwarded-proto: https` → `200 {"authenticated": true}`;
  - invalid cookie + valid Bearer over HTTPS → `200` (fallback preserved);
  - invalid cookie + no token → `401`.

### Part B — Persist Docker plugin state on the volume (Commit 1)

Files: `docker-compose.yml`, `.env.example`, `AGENTS.md`,
`docs/setup/VDS_RUNBOOK.md`, `docs/setup/DEPLOY.md`, `tests/test_plugin_state.py`.

- `docker-compose.yml:111`: change to
  `PLUGINS_STATE_FILE: ${PLUGINS_STATE_FILE:-/data/plugins.json}` (keep the
  existing comment; note it lives on `loomdata`).
- `.env.example:105-106`: keep the local default empty, but update the comment
  to say Docker sets `/data/plugins.json` on the `loomdata` volume while an
  empty value keeps `<plugins_dir>/../.loomcore/plugins.json`.
- `AGENTS.md:39`: state that in Docker the state persists to `/data/plugins.json`
  (loomdata volume); keep the `PLUGINS_STATE_FILE`/default note for local runs.
- `docs/setup/VDS_RUNBOOK.md:181-182`: update to say the state lives at
  `/data/plugins.json` (loomdata volume) in the compose deployment, so a
  container recreate preserves it.
- `docs/setup/DEPLOY.md`: if it documents the plugin state path, align it.
- Test: add/adjust a `tests/test_plugin_state.py` case asserting
  `resolve_state_path("/app/plugins", "/data/plugins.json")` resolves to exactly
  `/data/plugins.json` and that an empty override still yields the default. (No
  Docker execution in tests.)

### Part C — Per-tool timeout + process-group kill (Commit 2)

Files: `src/plugins/manager.py`, `plugins/LoomWG/LoomWG/schemas.py`,
`plugins/LoomOps/LoomOps/schemas.py`, `docs/architecture/PLUGIN_MANIFEST.md`,
`tests/test_plugin_manager.py`, `tests/test_LoomOps_tools.py`,
`tests/test_LoomWG_unit.py`.

Core (`src/plugins/manager.py`):

- Add module constants `DEFAULT_TOOL_TIMEOUT = 30.0` and
  `MAX_TOOL_TIMEOUT = 600.0`.
- `__init__`: add `self._tool_timeouts: dict[str, float] = {}` with a comment.
- `_index_tools`: read `tool.get("timeout")`; accept `int`/`float` (reject
  `bool`), require `> 0`, clamp to `MAX_TOOL_TIMEOUT`; on a malformed value log a
  warning and skip (default applies). Store in `self._tool_timeouts[tool_name]`.
- `_rebuild_tool_cache`: purge `self._tool_timeouts` for stale tools alongside
  the other caches.
- `execute_tool`: compute
  `call_timeout = self._tool_timeouts.get(tool_name, DEFAULT_TOOL_TIMEOUT)` and
  pass it to `_exchange(..., call_timeout=call_timeout)`. Update the
  `TimeoutError` message (`src/plugins/manager.py:1132-1135`) to use the real
  value, not the literal `30s`.
- `_spawn`: launch in a new process group:
  - POSIX: `start_new_session=True`;
  - Windows: `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP`.
  Pass `encoding="utf-8", errors="replace"` explicitly (fixes locale-pipe
  mojibake and keeps tests deterministic).
- Add `_kill_process_tree(proc)` helper and use it in `_kill_and_stop`
  (replacing `proc.kill()`) and in `_terminate_process` (terminate then kill the
  group):
  - POSIX: `os.killpg(os.getpgid(proc.pid), signal.SIGKILL)` (or `SIGTERM` first
    in the graceful path), falling back to `proc.kill()` on
    `ProcessLookupError`/`PermissionError`/`OSError`;
  - Windows: `proc.kill()` (documented as best-effort; process groups are
    emulated only via `taskkill /T`).
  Keep `terminate_all` and the restart paths using the new helpers.

Plugins (declare `timeout` on every tool whose internal budget exceeds 30 s).
Rule: **core timeout = that tool's plugin-side budget + ~30 s margin, clamped
≤ 600**. The executor must verify each budget against the plugin code before
setting the number; the mandatory minimums are:

- `plugins/LoomWG/LoomWG/schemas.py`:
  - `wg_install_dependencies` → `360` (plugin budget 300 s,
    `LoomWG/ops/dependencies.py`).
  - `wg_interface_start`, `wg_interface_restart`, `wg_service_enable`,
    `wg_service_disable` → `90` (`wg-quick` budget 60 s).
  - `wg_setup_server` (runs firewall/`wg-quick`) → set from its real budget
    (≥ 90).
- `plugins/LoomOps/LoomOps/schemas.py`:
  - `ops_run_command` → `600` (`LOOMOPS_COMMAND_TIMEOUT` max 600 s).
  - `ops_scan_health` → `120` (chained journal/dmesg/systemctl budgets).
  - `ops_dmesg`, `ops_log_search`, `ops_failed_units`, `ops_systemctl_status`,
    `ops_systemctl_restart`, `ops_network_connections` → `60`.
  - Other read-only tools keep the default (no field needed).

Docs: in `docs/architecture/PLUGIN_MANIFEST.md`, document the optional tool
`timeout` field next to `risk`/`report`, its default/`[1,600]` clamp, and that
the core kills the whole process group on timeout.

Tests:

- `tests/test_plugin_manager.py`: `_index_tools` stores a valid timeout, ignores
  a malformed (`"60"`, `0`, `-1`, `True`) one, and clamps `> 600` to `600`.
  Assert `execute_tool` passes the per-tool timeout to `_exchange` (monkeypatch
  `_exchange` and capture `call_timeout`).
- Add a POSIX-only (`@pytest.mark.skipif(os.name != "posix", ...)`) test that
  launches a helper plugin which spawns a long `sleep` child, times the call out,
  and asserts the grandchild is gone after the timeout. If a real grandchild
  test is impractical, at minimum unit-test `_kill_process_tree` by monkeypatching
  `os.killpg`/`os.getpgid` and asserting the group signal.
- Plugin schema tests: assert the tools above carry the expected `timeout`.

### Part D — Parallel-plan failure handling (Commit 2)

Files: `src/core/agent.py`, `tests/test_agent.py`.

- In the concurrent branch (`src/core/agent.py:344-378`), stop returning on the
  first failure. Process every `(step, outcome)` pair:
  - success → `status="done"`, emit `phase="done"`, append to `results`;
  - failure → `status="failed"`, set `step["error"]`, emit `phase="failed"`,
    remember that the plan failed (log a warning as today).
  - After the loop: if any step failed, set `plan["status"]="failed"` and return
    `{"plan": plan, "results": results}`; otherwise continue to the next run.
- Preserve the existing serial branch and the approval invariant unchanged.
- Tests (`tests/test_agent.py`):
  - a parallel group where the **first** step fails and the **second** succeeds:
    both statuses are final (`failed`/`done`), the successful result is in
    `results`, no step is left `running`, and no later run executes;
  - keep the existing mid/last-failure test green.

### Part E — `ops_run_command` argv-list schema (Commit 2)

Files: `plugins/LoomOps/LoomOps/schemas.py`, `tests/test_LoomOps_tools.py`.

- Change the `command` property in `ops_run_command`'s `inputSchema` to:

  ```json
  {
    "anyOf": [
      {"type": "string"},
      {"type": "array", "items": {"type": "string"}}
    ],
    "description": "Command line (shell-quoted string) or an argv list, e.g. 'uptime -p' or ['df','-h']"
  }
  ```

  Update the tool `description` to mention the argv-list form. Do not change
  `ops/commands.py` `_split_command` (it already accepts both).
- Test: call through `plugin_manager.execute_tool_by_name` (i.e. argument
  validation runs) with the list form and assert it is accepted (e.g.
  `["echo","hi"]` with `LOOMOPS_ALLOWED_COMMANDS` allowing `echo`), and that a
  non-list/non-string (e.g. `123`) is still rejected with `ToolContractError`.

## 6. Tests

Added/updated as listed per part. Exact gates (run from the repo root):

```
ruff check .
pytest -m "not integration and not e2e"
pytest tests/test_agent.py tests/test_plugin_manager.py tests/test_api_auth.py tests/test_plugin_state.py tests/test_contracts.py tests/test_LoomOps_tools.py -q
```

No frontend change, so the frontend gates are optional (if touched, run
`npx tsc --noEmit` and `npm run build` in `frontend/`).

## 7. Acceptance criteria

1. With `REQUIRE_TLS=true` and JWT mode, `GET /api/auth/session` returns `403`
   for a valid session cookie over plain HTTP and `200` over HTTPS
   (`x-forwarded-proto: https`); a bad cookie + valid Bearer still returns `200`.
2. `docker-compose.yml` sets `PLUGINS_STATE_FILE` to
   `${PLUGINS_STATE_FILE:-/data/plugins.json}`; a disabled plugin survives a
   container recreate (documented; `/data` is the `loomdata` volume).
3. A plugin tool may declare `timeout`; the core uses it (default 30, clamped
   `[1,600]`); a timed-out call kills the plugin's whole process group; no test
   leaves a lingering child on POSIX.
4. A parallel plan with a failing first step still reports the successful
   sibling as `done` with its result, marks the failed step `failed`, never
   leaves a step `running`, and does not run later runs.
5. `ops_run_command` accepts both `"df -h"` and `["df","-h"]` through
   `execute_tool_by_name`; a number is rejected.
6. `ruff check .` is clean and `pytest -m "not integration and not e2e"` is
   green (currently 1370 passed, 12 skipped baseline).
7. Docs updated in the same commit as the behaviour they describe
   (PLUGIN_MANIFEST.md, AGENTS.md, VDS_RUNBOOK.md, DEPLOY.md, CHANGELOG.md).

## 8. Commits

Coordinator commits only after acceptance (RULES 0a). Two commits, in order:

**Commit 1 — security/state:**
`fix(core): enforce TLS on the session cookie and persist plugin state on the volume`

**Commit 2 — correctness:**
`fix(core): per-tool timeouts, process-group kill, parallel-plan results, argv-list command`

Each commit updates its own `CHANGELOG.md` entry (RULES 14, 1–2 lines) and the
docs changed by that commit. The executor reports changes and
`Commits created: none`.

## 9. Risks

- **Process-group kill on Windows**: `start_new_session` is a no-op there;
  tests must not assert OS-specific kill semantics unconditionally — guard the
  real-process test with `sys.platform`/`os.name` and unit-test the helper via
  monkeypatch.
- **Per-tool timeout numbers can drift** from the plugin's internal budget if
  that budget is later raised. Mitigation: the mandatory minimums are validated
  against the code now; the PLUGIN_MANIFEST.md note states "core timeout must
  exceed the plugin's own budget".
- **TLS test environment**: `test_api_auth.py` must drive `is_https` through the
  request scheme / `x-forwarded-proto` header, not a real TLS socket.
- **Double JWT decode** in `auth_session` is intentional and negligible
  (a valid cookie only); do not cache credentials in a way that skips TLS.
- Changing `docker-compose.yml` env default only affects **new** container
  recreations; an operator can still override `PLUGINS_STATE_FILE`.
