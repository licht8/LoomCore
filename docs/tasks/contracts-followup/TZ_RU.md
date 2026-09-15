# TZ — Contracts follow-up (core-tool dispatch + plugin-less/integration verification)

## Goal

Close the one validation gap left by the contracts task and prove the core is
self-sufficient without plugins:

- **Phase A** — core meta-tools (`core_*`) are currently validated at plan time
  only; validate them again at dispatch time, and record the deliberate contract
  deviations/decisions so they are not mistaken for bugs.
- **Phase B** — a documented, repeatable plugin-less run plus the integration
  smoke, with an explicit checklist and acceptance criteria.

Phase A is a small correctness fix; Phase B is verification and documentation.
No new contracts are introduced.

## Non-goals

- No change to plugin transport, plan-and-execute flow, or the WebSocket protocol.
- No frontend changes.
- No new architecture document; deviations live in the report plus short notes.
- No attempt to make a *missing* plugins directory graceful — an existing empty
  directory is the supported "no plugins" configuration.

## Phase A — core-tool dispatch gap + decisions

**Facts:** `PluginManager.execute_tool_by_name` validates arguments against the
plugin schema (`src/plugins/manager.py:889`), but the core path
`src/interfaces/websocket.py:101-106 _execute_tool -> dispatch_core_tool`
(`src/core/core_tools.py:298-303`) never validates. A step whose args were not
checked at plan time (or a direct/REST caller) reaches the handler unchecked.

**Changes:**

- `src/core/core_tools.py`:
  - import `validate_tool_args` from `src.core.contracts`;
  - add `CORE_TOOL_SCHEMAS = {tool["name"]: tool.get("inputSchema") or {} for tool in CORE_TOOLS_SCHEMA}`
    next to `CORE_TOOL_RISK`;
  - in `dispatch_core_tool`, resolve the handler first (so an unknown tool keeps
    its own `ValueError`), then `validate_tool_args(name, arguments, CORE_TOOL_SCHEMAS.get(name))`
    before calling it. `ToolContractError` subclasses `ValueError`, so existing
    callers/tests keep working.
- `tests/test_core_tools.py`: `test_dispatch_validates_core_arguments` — a
  schema-violating `core_plugin_status {}` raises `ToolContractError` mentioning
  `missing required argument(s): plugin`, a valid call still reaches the handler,
  and `core_nope` still raises "Unknown core tool".

**Decisions recorded (no behavior change):**

- **Validation scope** — full JSON Schema Draft 2020-12, not just
  required/type/enum. `additionalProperties` is honoured only when a plugin
  declares it; no bundled schema does, so extra arguments still pass. Plugins opt
  into strictness by setting `additionalProperties: false`.
- **Trust** — a plugin-declared `risk` is authoritative; a plugin declaring a
  tool `safe` bypasses approval by design. Plugins are trusted local
  subprocesses; only the LLM is untrusted, and the declaration is visible in plan
  steps and audit records.
- **`exclude_none`** — `event_payload` keeps `model_dump(exclude_none=True)`, so
  unset optional fields (e.g. `artifacts`) are absent rather than `null`. The
  frontend fields are optional and `schema_version` is ignored by the TS union,
  so this stays additive.
- Also noted: the original five planned commits collapsed to three because
  `manager.py`/`router.py`/`websocket.py` interleave all phases; no `prompt.py`
  risk catalog was needed; internal tools validate warn-only; a structurally
  invalid schema degrades to a no-op with a warning.

## Phase B — plugin-less run + integration smoke

**Changes:**

- `tests/test_smoke_plugins.py` (new):
  - an existing but empty plugins directory makes `discover_plugins` a no-op
    (`[]`, no plugin names);
  - `GET /api/tools` returns exactly `CORE_TOOLS_SCHEMA` (no `wg_*`/`ops_*`);
  - `GET /api/plugins/status` returns `{"plugins": {}}`;
  - a missing directory raises `FileNotFoundError` (pins the documented
    unsupported behaviour).
- `docs/setup/TESTING.md`: add a "Plugin-less run" section with the exact
  commands and the "directory must exist" caveat.
- Run the manual checklist below and record the results in `REPORT_RU.md`.
- Integration smoke: `docker compose up -d db` → `alembic upgrade head` →
  `LOOMCORE_INTEGRATION=1 pytest -q -m integration` (existing
  `test_postgres_available`). Record the outcome; if no Docker/Postgres is
  available, record it as blocked with the reason.

### Plugin-less checklist

1. `New-Item -ItemType Directory -Force tmp\no-plugins`
2. `$env:PLUGINS_DIR="$PWD\tmp\no-plugins"; .venv\Scripts\uvicorn.exe src.main:app --port 8000`
3. Startup logs `🔌 Plugins loaded: none`; no child process is spawned.
4. `GET /health` → `200 {"status":"ok"}`.
5. `GET /api/tools` (Bearer admin) → only the 8 `core_*` tools.
6. `GET /api/plugins/status` (Bearer admin) → `{"plugins": {}}`.
7. A WebSocket turn does not crash; the planner catalog contains only core tools
   (a conversational reply needs an LLM key; without one an `error` event is
   expected, not a crash).
8. Negative: pointing `PLUGINS_DIR` at a non-existent path crashes startup —
   documented as unsupported.

## Minimal diff

- `src/core/core_tools.py` — validate core args in `dispatch_core_tool`.
- `tests/test_core_tools.py` — dispatch validation test.
- `tests/test_smoke_plugins.py` (new) — plugin-less discovery and API.
- `src/interfaces/events.py` — one-line docstring note on `exclude_none`.
- `docs/architecture/AGENT.md` — dispatch validation now covers core tools.
- `docs/setup/TESTING.md` — plugin-less run section.
- `docs/tasks/contracts-followup/TZ_RU.md` / `REPORT_RU.md`.

## Tests

- `pytest tests/test_core_tools.py` — new dispatch-validation case plus the
  existing unknown-tool / requires-plugin cases.
- `pytest tests/test_smoke_plugins.py` — discovery no-op, core-only `/api/tools`,
  empty `/api/plugins/status`, missing-dir behaviour.
- Full fast run: `pytest -m "not integration and not e2e"`; `ruff check .`.
- Integration: `LOOMCORE_INTEGRATION=1 pytest -q -m integration`.

## Acceptance criteria

1. A `core_*` call with arguments that violate its `inputSchema` is refused at
   dispatch with `ToolContractError`; a valid call is unaffected.
2. An unknown core tool still raises "Unknown core tool" (validation never masks
   it).
3. With an existing empty plugins directory the core starts with
   `🔌 Plugins loaded: none`, serves `/health`, and `/api/tools` lists only core
   tools.
4. The integration smoke passes, or is explicitly recorded as blocked (with
   reason).
5. `ruff check .` clean; `pytest -m "not integration and not e2e"` green.
6. The deviations/decisions (extra args, trust, `exclude_none`) are documented.

## Commits

1. `fix(core): validate core meta-tool arguments at dispatch time`
2. `test(smoke): cover plugin-less startup and tool catalog`
3. `docs: record contract deviations and the plugin-less run checklist`

## Risks

- Dispatch validation could reject a core call that previously slipped through →
  core schemas are minimal (`plugin` required only) and all existing call sites
  already pass valid args; tests cover both paths.
- `/api/tools` and `/api/plugins/status` are rate-limited (10/min) → the suite
  stays well under the limit.
- The plugin-less run is only meaningful for `/health`-style routes on Windows;
  the project targets Linux, so chat needs an LLM key.
- Integration requires Docker/Postgres; if unavailable the check is recorded as
  blocked rather than silently skipped.
