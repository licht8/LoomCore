# TZ — Contracts (tool I/O, manifest/version, WebSocket events)

## Goal

Make three implicit interfaces explicit and enforced, without bloating the core (RULES 1):

1. **Tool/plugin I/O** — validate tool `arguments` against the `inputSchema`
   plugins already publish, at both plan time and dispatch time. The LLM is an
   untrusted producer; today args go to the wire unchecked.
2. **Manifest/version compatibility** — make `requires_core` and `protocol`
   real: an incompatible plugin is **not launched** and is **surfaced** in
   plugin status.
3. **WebSocket event contract** — one exhaustive, versioned event registry
   mirroring the frontend's existing `WsMessage` union
   (`frontend/lib/chat-types.ts:55-106`), so a new event type cannot be emitted
   without being registered.

Phase 1b additionally moves per-tool `risk`/`internal` declaration from core
hardcoded name lists to the plugin (the tool schema), so adding a plugin tool no
longer requires editing the core.

Phase 4 (generated TS client / OpenAPI models) is explicitly **out of scope**
(separate task).

## Non-goals

- No Design-by-Contract asserts in hot paths.
- No core import of plugin code; no plugin launched differently.
- No frontend code changes (doc-only note; contract is additive-only).
- No result-payload hard failure — a malformed plugin result only warns (RULES 2).
- No change to MCP transport, timeouts or the plan-and-execute flow.

## Phase 1 — Tool/plugin I/O contract

**Facts:** `PluginManager.execute_tool` sends `arguments or {}` unchecked
(`src/plugins/manager.py:758-780`); schemas are cached only as name→plugin maps
(`_index_tools`/`_rebuild_tool_cache`, `manager.py:475-503`); `validate_plan`
checks required args only when schemas are passed (`src/core/agent.py:160-200`),
and the WS `plan_edit` path omits them (`src/interfaces/websocket.py:624-625`).

**Changes:**

- `PluginManager`: add `self._tool_schemas: dict[str, dict]` (`manager.py:72`),
  populated/cleared in `_index_tools` and `_rebuild_tool_cache`; expose
  `tool_schema(name)`.
- New `src/core/contracts.py`:
  - `ToolContractError(ValueError)`;
  - `validate_tool_args(name, args, schema) -> None` using `jsonschema`
    (Draft 2020-12, tolerate a missing `$schema`), skipping required-check only
    when no schema is known;
  - `validate_tool_result(result) -> list[str]` — lenient warnings only.
- Enforce at both layers (defense in depth):
  - **Plan time:** extend `validate_plan` to full-schema validation via the
    helper (router already passes `tool_schemas`, `src/llm/router.py:142-144,199`);
    fix `websocket.py:624-625` to pass schemas so edited plans are validated
    (currently name-only).
  - **Dispatch time:** `execute_tool_by_name` (`manager.py:823`) validates
    against `_tool_schemas` and raises `ToolContractError` on violation.
    Internal tools (`execute_internal_tool`) are core-controlled → validate only
    if a schema exists, never block.
- `pyproject.toml`: add `jsonschema` explicitly (already present transitively via
  `mcp`, `uv.lock:777`).

## Phase 1b — Plugin-declared risk/internal

**Facts:** `classify_tool_risk` (`src/core/security.py:44-89`) hardcodes every
`wg_*`/`ops_*`/`core_*` tool name, and `_index_tools` already reads a custom
`internal` key (`manager.py:475-488`).

**Changes:**

- Plugin `tools/list` entries may carry `risk: "safe" | "moderate" | "dangerous"`
  and `internal: true`.
- `PluginManager` retains a `_tool_risk: dict[str, str]` and `_internal_tools`
  from the schema; expose `tool_risk(name)`.
- `classify_tool_risk` keeps its signature and heuristic fallback but accepts an
  optional declared risk that takes precedence. Core tools declare their own risk
  in `CORE_TOOLS_SCHEMA`.
- `_normalize_step` and core dispatch resolve risk through the plugin manager
  first, then the heuristic.
- A plugin declaring a tool `safe` is honored (trust model: plugins are trusted
  subprocesses; only the LLM is untrusted). Declared risk is surfaced in plan
  steps and audit records.
- Both bundled plugins (`LoomWG`, `LoomOps`) declare `risk` per tool.

## Phase 2 — Manifest/version compatibility contract

**Facts:** both plugins declare `schema_version: 1`, `protocol: "2024-11-05"`,
`requires_core: ">=0.0.1"`; `requires_core`/`protocol` are parsed and never used
(`src/plugins/manifest.py:50,71-72`).

**Changes:**

- Central `CORE_VERSION`: `importlib.metadata.version("loomcore")` with fallback
  `"0.0.1"`; reuse in `src/app.py:67`.
- `src/plugins/manifest.py`: `SUPPORTED_PROTOCOLS = {"2024-11-05"}` and
  `check_compatibility(manifest, core_version) -> list[str]` using
  `packaging.specifiers.SpecifierSet` for `requires_core` (PEP 440). Add
  `packaging` explicitly to `pyproject.toml`.
- `PluginManager._resolve_manifest` (`manager.py:197`): when `requires_core` is
  unsatisfied or `protocol` is unsupported → **do not launch**. Register a
  stopped/disabled `ManagedPlugin` record carrying the reason (mirroring the
  disabled branch, `manager.py:182-194`) so it appears via
  `core_list_plugins`/`core_plugin_status` and `/api/plugins/status`. Mismatch
  still never crashes startup.

## Phase 3 — WebSocket event registry (frontend doc-only, additive-only)

**Facts:** events are emitted from `src/interfaces/tool_events.py` and inline
dicts in `src/interfaces/websocket.py`; the frontend already defines the union in
`frontend/lib/chat-types.ts:55-106` and dispatches in `chat-ws.ts`.

**Changes:**

- New `src/interfaces/events.py`: `WS_SCHEMA_VERSION = 1` plus a registry of the
  11 event types — `thinking, response, tool_call, tool_response, plan_proposed,
  plan_step, plan_done, schema_warning, cancelled, busy, error` — as Pydantic
  models whose field names match the TS union (all optional except `type`).
- `tool_events.py` builders return registry models; replace inline
  `{"type": ...}` dicts in `websocket.py` with builders. Add `schema_version: 1`
  to every payload (additive; TS ignores it).
- Additive-only rule documented; **no frontend edits** (read
  `frontend/AGENTS.md` first).

## Minimal diff

- `src/core/contracts.py` (new), `src/core/agent.py`, `src/core/security.py`
- `src/plugins/manager.py`, `src/plugins/manifest.py`, `src/core/version.py` (new)
- `src/app.py`, `src/core/core_tools.py`, `pyproject.toml`
- `src/interfaces/events.py` (new), `src/interfaces/tool_events.py`,
  `src/interfaces/websocket.py`
- `plugins/LoomWG/LoomWG/tools.py`, `plugins/LoomOps/LoomOps/tools.py`
- `src/llm/router.py`, `src/llm/prompt.py` (risk catalog rendering)
- `docs/architecture/AGENT.md`, `docs/architecture/PLUGIN_MANIFEST.md`,
  `docs/setup/TESTING.md`

## Tests

- `tests/test_contracts.py` (new) — arg validation: required, type, enum, unknown
  schema, unknown tool.
- `tests/test_agent.py` — `validate_plan` full-schema path; direct/edited plans.
- `tests/test_plugin_manager.py` — `_tool_schemas` cache, dispatch-time
  rejection, internal-tool exemption, declared-risk precedence.
- `tests/test_plugin_manifest.py` — compatibility check (satisfied / unsatisfied
  `requires_core`, unsupported protocol).
- `tests/test_events.py` (new) — registry completeness and payload shape.
- `tests/test_websocket.py` — edited plan with bad args is rejected (regression
  for `websocket.py:624`).

## Acceptance criteria

1. Tool args that violate an advertised `inputSchema` are refused before
   dispatch; the planner is re-asked at plan time.
2. Edited plans are validated with schemas, not name-only.
3. A plugin whose `requires_core`/`protocol` is incompatible is not launched and
   is visible in plugin status.
4. Every emitted WS `type` exists in one registry and matches the frontend union
   field names (additive only).
5. Unknown/unavailable schema never blocks a call; malformed results warn only.
6. Plugin-declared `risk`/`internal` overrides the core heuristic; core no longer
   needs editing to add a plugin tool.
7. `ruff check .` clean; `pytest -m "not integration and not e2e"` green.
8. No frontend code changed.

## Commits

1. `feat(core): validate tool arguments against plugin schemas`
2. `feat(plugins): declare per-tool risk and internal flags`
3. `feat(plugins): enforce manifest core/protocol compatibility`
4. `feat(interfaces): add a versioned WebSocket event registry`
5. `docs: document the tool, manifest and event contracts`

## Risks

- Stricter validation may reject args some plugins silently tolerated → start
  with the required/type/enum subset, log the rejected call, and exempt internal
  tools.
- `requires_core` becomes meaningful; a mis-declared range can stop a launch —
  surfaced in status, never a startup crash.
- New direct deps (`jsonschema`, `packaging`) are already in the lock; declare
  them so a lock refresh cannot drop them.
- A plugin self-declaring `safe` bypasses approval by design; plugins are
  trusted local subprocesses, and the declaration is visible in audit records.
