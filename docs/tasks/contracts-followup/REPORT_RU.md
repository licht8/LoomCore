# Report — Contracts follow-up (core-tool dispatch + plugin-less / integration)

## Goal

Close the remaining contract gap: core meta-tools were validated at plan time
only, not at dispatch time. Then verify the core boots and serves with **zero
plugins**, and run the integration smoke. Record the deviations and decisions
taken while implementing the original contracts task.

## Phase A — core-tool dispatch + decisions

### Delivered

- `src/core/core_tools.py` — new `CORE_TOOL_SCHEMAS` (name → `inputSchema`) built
  from `CORE_TOOLS_SCHEMA`; `dispatch_core_tool` now resolves the handler first
  (an unknown name keeps its own `ValueError`), then runs
  `validate_tool_args(name, arguments, CORE_TOOL_SCHEMAS.get(name))` before
  calling it. `CORE_TOOL_RISK` / `resolve_tool_risk` are unchanged.
- `tests/test_core_tools.py` — `test_dispatch_validates_core_arguments`:
  `core_plugin_status {}` raises `ToolContractError` ("missing required
  argument(s): plugin"), `core_health {}` still works.
- `docs/architecture/AGENT.md` — dispatch-time enforcement now names both paths
  (`execute_tool_by_name` for plugin tools, `dispatch_core_tool` for core
  meta-tools).
- `src/interfaces/events.py` — `event_payload` docstring documents the deliberate
  `exclude_none` behaviour.

With this, both layers are symmetric: plan time (`validate_plan`) and dispatch
time (plugin + core) enforce the advertised `inputSchema`.

### Decisions / deviations

| # | Topic | Decision |
|---|---|---|
| D1 | **Extra arguments** | Full Draft 2020-12 validation, not a required/type/enum subset. `additionalProperties` is honoured **if a plugin declares it**; no bundled schema does, so unknown extra args currently pass (backward compatible). Rejecting extras stays a deliberate per-plugin opt-in. |
| D2 | **Trust** | A plugin-declared `risk` is authoritative; a tool declared `safe` bypasses approval by design — plugins are trusted local subprocesses, only the LLM is untrusted. The declaration is visible in plan steps and audit records. |
| D3 | **`exclude_none`** | `event_payload` keeps `model_dump(exclude_none=True)`: unset optional fields (e.g. `artifacts`) are absent rather than `null`. The frontend `WsMessage` union marks them optional, so this is additive-safe and keeps payloads small. |
| D4 | **Commits** | The original TZ planned 5 phase commits; they were collapsed to 3 because `manager.py` / `router.py` / `websocket.py` interleave all phases (a phase-only split leaves intermediate commits non-importable). |
| D5 | **Risk catalog rendering** | The original Minimal diff mentioned `src/llm/prompt.py`; no change was needed — risk resolution happens in code and the catalog text is unchanged. |
| D6 | **Internal tools** | `execute_internal_tool` validates only when a schema exists and merely warns, never blocks (RULES 2); core-controlled path. |
| D7 | **Malformed schema/result** | A structurally invalid schema is a warn + no-op (`Ignoring invalid tool schema`), and `validate_tool_result` never raises — a bad plugin result only warns. |

## Phase B — plugin-less run + integration smoke

### Automated (`tests/test_smoke_plugins.py`)

- `test_discover_empty_dir_is_a_noop` — an existing empty directory yields `[]`.
- `test_discover_missing_dir_is_unsupported` — a missing path raises
  `FileNotFoundError` (pins the documented, unsupported behaviour).
- `test_tools_endpoint_lists_only_core_without_plugins` — `/api/tools` returns
  exactly `CORE_TOOLS_SCHEMA`.
- `test_plugins_status_is_empty_without_plugins` — `/api/plugins/status` returns
  `{"plugins": {}}`.

### Manual plugin-less run (Windows, port 8111)

`PLUGINS_DIR=tmp/no-plugins`, launched with
`python -m uvicorn src.main:app --port 8111`:

| Check | Expected | Observed |
|---|---|---|
| startup | `🔌 Plugins loaded: none` | `Plugins loaded: none` |
| `GET /health` | `200 {"status":"ok"}` | ok |
| `GET /api/tools` | only the 8 `core_*` tools | `core_health … core_reload_plugin` (8, no `wg_*`/`ops_*`) |
| `GET /api/plugins/status` | `{"plugins":{}}` | `{"plugins":{}}` |
| no subprocesses | none spawned | none |

Environment note: redirecting the process output on Windows requires
`PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8`, otherwise the emoji startup log raises
`UnicodeEncodeError` under the default `charmap` codec. This is a Windows-console
artefact only; on Linux/containers (the supported target) it does not occur.
Documented in `docs/setup/TESTING.md` as a "Plugin-less run" section.

### Integration smoke (Postgres)

```
docker compose up -d db
# the pre-existing volume lacked the DB; created it once:
#   CREATE DATABASE loomcore OWNER loomuser;
alembic upgrade head            # 001 init, 002 audit log
LOOMCORE_INTEGRATION=1 pytest -q -m integration
```

Result: `1 passed, 531 deselected` (`tests/test_smoke_integration.py`).

## Verification

- `ruff check .` — clean.
- `pytest -m "not integration and not e2e"` — **530 passed, 2 deselected**.
- `LOOMCORE_INTEGRATION=1 pytest -q -m integration` — 1 passed.

## Commits

1. `fix(core): validate core meta-tool arguments at dispatch time`
2. `test(smoke): cover plugin-less startup and tool catalog`
3. `docs: record contract deviations and the plugin-less run checklist`

## Notes / follow-ups

- `/api/tools` merges `CORE_TOOLS_SCHEMA` with `plugin_manager.get_all_tools()`;
  with no plugins it is core-only, which is the intended degraded mode.
- A missing `PLUGINS_DIR` still crashes startup (by design of
  `discover_plugins`); the supported "no plugins" path is an existing empty dir.
- Phase 4 (generated TS client / OpenAPI response models) remains a separate
  task.
