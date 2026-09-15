# Report — Contracts (tool I/O, manifest/version, WebSocket events)

## Goal

Make three implicit interfaces explicit and enforced without bloating the core
(RULES 1): tool/plugin I/O validation, manifest core/protocol compatibility, and
one versioned WebSocket event registry. Phase 1b additionally moves per-tool
`risk`/`internal` declaration from core hardcoded name lists to the plugin.
Phase 4 (generated TS client / OpenAPI models) stayed out of scope.

## Delivered

### Phase 1 — tool/plugin I/O

- `src/core/contracts.py` (new) — `ToolContractError`; `validate_tool_args`
  (jsonschema Draft 2020-12; a missing/opaque schema is a no-op; required-argument
  failures keep the friendly `... is missing required argument(s): ...` message);
  `validate_tool_result` (lenient warnings only, never blocks — RULES 2).
  `_validator` calls `check_schema` first, so a structurally invalid schema only
  warns (`Ignoring invalid tool schema: ...`) instead of crashing.
- `src/plugins/manager.py` — retains `_tool_schemas`/`_tool_risk`, cleared and
  repopulated in `_rebuild_tool_cache`/`_index_tools`; `tool_schema()`/`tool_risk()`
  accessors. `execute_tool_by_name` validates arguments before dispatch and raises
  `ToolContractError` on violation; `execute_internal_tool` validates only when a
  schema exists and merely warns (core-controlled path).
- `src/core/agent.py` — `validate_plan` validates step `args` through the shared
  helper (plan time), keeping its structural checks.
- `src/llm/router.py` — new `tool_schemas()`; already passed schemas to
  `validate_plan`.
- `src/interfaces/websocket.py` — `plan_edit` now fetches schemas
  (`airouter_with_plugins.tool_schemas()`) and passes them to `validate_plan`, so
  edited plans are validated with schemas, not name-only.
- `pyproject.toml` — `jsonschema` declared explicitly (was transitive via `mcp`).

### Phase 1b — plugin-declared risk/internal

- `src/core/security.py` — `classify_tool_risk(tool_name, declared=None)`; a valid
  declared risk wins, otherwise the original heuristic is unchanged (all existing
  expectations preserved).
- `src/core/core_tools.py` — `risk` added to every `CORE_TOOLS_SCHEMA` entry
  (safe for health/list/status/tools/capabilities; dangerous for
  enable/disable/reload); `resolve_tool_risk(name)` prefers the core declaration,
  then the plugin declaration, then the heuristic.
- `src/core/agent.py` — `_normalize_step`/`build_plan`/`next_revision` take an
  optional `risk_of` resolver; `router.py` and `websocket.py` pass
  `resolve_tool_risk`.
- `plugins/LoomWG/LoomWG/schemas.py`, `plugins/LoomOps/LoomOps/schemas.py` —
  per-tool `risk` declared in `TOOLS_SCHEMA` (`internal` already existed for
  `wg_get_client_artifact`). Adding a plugin tool no longer requires editing core.

### Phase 2 — manifest/version compatibility

- `src/core/version.py` (new) — `CORE_VERSION` via
  `importlib.metadata.version("loomcore")` with `"0.0.1"` fallback; `src/app.py`
  now uses it.
- `src/plugins/manifest.py` — `SUPPORTED_PROTOCOLS = {"2024-11-05"}` and
  `check_compatibility(manifest, core_version)` using
  `packaging.specifiers.SpecifierSet` (PEP 440; a bad specifier counts as
  incompatible).
- `src/plugins/manager.py` — `_resolve_manifest` records `manifest["_compatibility"]`;
  `discover_plugins` does not launch an incompatible plugin and registers it as a
  stopped, disabled `ManagedPlugin` carrying the reasons (mirroring the disabled
  branch), so it is visible in `core_list_plugins`/`core_plugin_status` and
  `/api/plugins/status`. Startup never crashes.
- `pyproject.toml` — `packaging` declared explicitly.

### Phase 3 — versioned WebSocket event registry

- `src/interfaces/events.py` (new) — `WS_SCHEMA_VERSION = 1`; 11 Pydantic models
  (`thinking, response, tool_call, tool_response, plan_proposed, plan_step,
  plan_done, schema_warning, cancelled, busy, error`) whose optional field names
  mirror `frontend/lib/chat-types.ts:55-106`; `EVENT_MODELS`; `event_payload`
  (`model_dump(exclude_none=True)`); builders for the non-tool events.
- `src/interfaces/tool_events.py` — the six builders now return registry payloads.
- `src/interfaces/websocket.py` — all inline `{"type": ...}` dicts replaced with
  builders; every payload carries `schema_version`.
- Additive only: no frontend code changed; `schema_version` is ignored by the
  existing TS union.

### Docs

- `docs/architecture/AGENT.md` — plan-time + dispatch-time validation, declared
  risk precedence, event registry, updated file table.
- `docs/architecture/PLUGIN_MANIFEST.md` — compatibility enforcement, new "Tool
  declarations" section (`risk`/`internal`), "Adding a plugin" step 7 rewritten
  (no core edit needed).
- `docs/setup/TESTING.md` — contracts covered by the unit run.

## Verification

- `ruff check .` — clean.
- `pytest -m "not integration and not e2e"` — 525 passed, 2 deselected.
- New/extended tests: `tests/test_contracts.py`, `tests/test_events.py`,
  `tests/test_plugin_manifest.py` (`check_compatibility`),
  `tests/test_plugin_manager.py` (schema cache, dispatch rejection, incompatible
  plugin surfaced without launch), `tests/test_security.py` (declared-risk
  precedence), `tests/test_websocket.py` (edited plan with missing required args
  is rejected).

## Commits

1. `feat(core): validate tool arguments against plugin schemas`
2. `feat(plugins): declare per-tool risk and internal flags`
3. `feat(plugins): enforce manifest core/protocol compatibility`
4. `feat(interfaces): add a versioned WebSocket event registry`
5. `docs: document the tool, manifest and event contracts`

## Notes / follow-ups

- The TZ's Minimal diff named `plugins/*/tools.py` and a `src/llm/prompt.py` risk
  catalog line; the actual risk declarations live in `plugins/*/schemas.py`
  (`tools.py` re-exports them) and no prompt change was needed — the catalog text
  is unchanged and risk resolution happens in code.
- A plugin self-declaring a tool `safe` bypasses approval by design (plugins are
  trusted subprocesses; only the LLM is untrusted); the declaration is visible in
  plan steps and audit records.
- A malformed tool result warns only; it is never turned into a hard failure.
- Phase 4 (generated TS client / OpenAPI response models) remains a separate task.
