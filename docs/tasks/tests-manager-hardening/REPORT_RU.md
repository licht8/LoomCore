# REPORT_RU.md — tests / plugin-manager hardening

Spec: docs/tasks/tests-manager-hardening/TZ_RU.md

## Goal

Close six review findings: two test-infrastructure hazards (ambiguous plugin
logical names, per-call package purge), one plugin-manager hazard (orphaned
children, busy auto-restart, tool-name collisions, stale tools) and three
test-robustness bugs (unguarded manifest `None`, silent teardown failure,
positional JSON-RPC indexing, literal capability bits).

## Delivered

### D1 — disjoint, asserted logical-name keys (`tests/conftest.py`)

Every LoomOps key is namespaced with the plugin id (`loomops.ops`,
`loomops.commands`, `loomops.files`, `loomops.processes`, `loomops.services`,
`loomops.system_info`, `loomops.monitoring`, `loomops.logs`,
`loomops.diagnostics`, `loomops.tools`, `loomops.schemas`, `loomops.protocol`,
`loomops.server`, `loomops.results`), so no name is shared with the LoomWG map.
The shared loader now asserts the resolved module's root package:

```python
root = module.__name__.split(".", 1)[0]
assert root == package, (
    f"logical name {name!r} resolved to {module.__name__!r}, "
    f"which does not belong to the {package} loader"
)
```

All `tests/test_LoomOps_tools.py` call sites were rewritten from
`load_loomops("X")["X"]` to `load_loomops("loomops.X")["loomops.X"]` (132 calls
across 12 names).

### D2 — purge once, cache modules (`tests/conftest.py`)

Both fixtures use a shared `_make_loader(package, paths)` closure with a
per-fixture `loaded` cache, so a logical name imported twice inside one test
returns the same module object. The package is purged once before the first
load and once at teardown (instead of on every `_load` call).

### D3 — kill the spawned child on a failed (re)start (`src/plugins/manager.py`)

`enable_plugin`, `reload_plugin` and `_restart_plugin` now call
`self._kill_and_stop(plugin_name)` in their `except` blocks before marking the
plugin stopped, so a partially spawned child (and its pipes) cannot outlive the
failure.

### D4 — never auto-restart a busy plugin (`src/plugins/manager.py`)

The `get_all_tools` `failed_plugins` loop now skips a plugin whose state is
`BUSY` (logged at INFO), so a legitimate in-flight call is not terminated.

### D5 — duplicate tool names and stale tools (`src/plugins/manager.py`)

`_index_tools` looks up the tool's current owner in `_tool_plugins` /
`_internal_tools`; when a different plugin already owns the name it logs a
warning and skips the tool without touching any of the four caches.
`get_all_tools` uses `_rebuild_tool_cache` at both extend sites (which now
returns the LLM-facing list), so tools a plugin stopped advertising are dropped.

### D6 — test hardening

- `tests/test_LoomOps_manifest.py`: `assert data is not None` after each
  `load_manifest(LOOMOPS_DIR)` in the three indexing tests.
- `tests/test_LoomWG_e2e.py`: unique per-run interface name (from `os.getpid()`),
  a free ephemeral UDP port, a pre-up `active_interfaces()` assertion, and a
  `_bring_down` teardown that captures the result, warns on stderr for a non-zero
  returncode and swallows `subprocess.TimeoutExpired`.
- `tests/test_LoomWG_plugin.py`: responses correlate by `id` via `_by_id(...)`
  with a loud missing-id failure instead of positional indexing.
- `tests/test_LoomWG_manifest.py`: a `_cap_mask(manifest_module, *names)` helper
  derives every capability mask from `_CAPABILITIES`; no literal bit remains.

## Tests

`tests/test_plugin_manager.py` gained five regression tests:

- `test_rebuild_cache_rejects_duplicate_tool_names` (warning + first owner keeps
  routing/schema/risk);
- `test_get_all_tools_drops_tools_a_plugin_stops_advertising`;
- `test_get_all_tools_does_not_restart_busy_plugin`;
- `test_enable_failure_kills_spawned_child`;
- `test_reload_failure_kills_spawned_child`;
- `test_restart_failure_kills_spawned_child`.

Existing loader, manager, manifest and JSON-RPC suites stay green with the
namespaced keys.

## Verification

- `uv run ruff check .` -> All checks passed!
- `uv run pytest tests/test_plugin_manager.py tests/test_LoomOps_tools.py tests/test_LoomOps_manifest.py tests/test_LoomWG_manifest.py tests/test_LoomWG_plugin.py -q`
  -> 222 passed, 8 skipped (tmp gate)
- `uv run pytest -m "not integration and not e2e" -q` -> 1004 passed, 11 skipped,
  5 deselected (tmp/tests_manager_hardening_full.log; was 998)
- Integration/e2e not run (no Postgres; e2e needs Linux + root).

## Changed files

- tests/conftest.py
- tests/test_LoomOps_tools.py
- tests/test_LoomOps_manifest.py
- tests/test_LoomWG_e2e.py
- tests/test_LoomWG_plugin.py
- tests/test_LoomWG_manifest.py
- tests/test_plugin_manager.py
- src/plugins/manager.py
- CHANGELOG.md

## Notes / risks

- D5 changes behaviour only in the already-invalid state where two plugins
  publish the same tool name; the first owner keeps the routing.
- `_rebuild_tool_cache` gaining a return value is backward compatible: every
  production caller ignored the previous `None`.
- The e2e changes are inert unless `LOOMWG_E2E=1` on a Linux root host.
- Migration/e2e paths still cannot run locally, unchanged from before.

## Suggested commits

- `test(conftest): disambiguate plugin module keys and keep module identity`
- `fix(plugins): kill failed spawns, skip busy restarts, guard tool-name collisions`
- `test(plugins): harden manifest, e2e, and JSON-RPC contract tests`

Commits created: none
