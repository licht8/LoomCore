# TZ_RU.md — tests / plugin-manager hardening

Task id: `tests-manager-hardening`. Follow RULES.md (English only, no comments
unless existing style has them, ruff clean, tests mandatory). Executor does NOT
commit; the coordinator commits after acceptance (RULES 0a).

## 1. Goal

Fix six review findings — two test-infrastructure hazards, one plugin-manager
hazard, and three test-robustness bugs:

1. `tests/conftest.py` — several logical names are defined in **both**
   `_MODULE_PATHS` and `_LOOMOPS_MODULE_PATHS` but resolve to different packages,
   so calling the wrong loader still imports successfully and silently exercises
   the other plugin. Additionally, `_load` purges the whole package on **every**
   call, splitting module identity inside a single test.
2. `src/plugins/manager.py` — failure paths of the `_do_register_inplace`
   callers leave an already-spawned subprocess (and its pipes) running.
3. `src/plugins/manager.py` — `get_all_tools` auto-restarts a **busy** plugin,
   terminating a legitimate in-flight call.
4. `src/plugins/manager.py` — tool names published by two plugins silently
   overwrite routing/schema/risk, and `get_all_tools` never drops stale tools.
5. `tests/test_LoomOps_manifest.py` — only the first test guards `None` from
   `load_manifest`; the rest raise an opaque `TypeError`.
6. `tests/test_LoomWG_e2e.py`, `tests/test_LoomWG_plugin.py`,
   `tests/test_LoomWG_manifest.py` — silent teardown failure, hardcoded
   interface/port, positional JSON-RPC response indexing, and hard-coded
   capability bitmasks.

## 2. Non-goals

- No runtime behaviour change to plugins, migrations, API, or frontend.
- No production module renames.
- No reformatting of unrelated lines; no edits outside the listed files.

## 3. Approved decisions (locked)

### D1 — disjoint logical-name keys (`tests/conftest.py`)
Verified collisions between the two maps: `files`, `schemas`, `tools`,
`protocol`, `server`, `results`. Namespace **every** LoomOps key with the plugin
id and assert the resolved module's root package:

```python
_LOOMOPS_MODULE_PATHS = {
    "loomops.ops": "LoomOps.ops",
    "loomops.commands": "LoomOps.ops.commands",
    "loomops.files": "LoomOps.ops.files",
    "loomops.processes": "LoomOps.ops.processes",
    "loomops.services": "LoomOps.ops.services",
    "loomops.system_info": "LoomOps.ops.system_info",
    "loomops.monitoring": "LoomOps.ops.monitoring",
    "loomops.logs": "LoomOps.ops.logs",
    "loomops.diagnostics": "LoomOps.ops.diagnostics",
    "loomops.tools": "LoomOps.tools",
    "loomops.schemas": "LoomOps.schemas",
    "loomops.protocol": "LoomOps.protocol",
    "loomops.server": "LoomOps.server",
    "loomops.results": "LoomOps.results",
}
```

In the shared loader, after import:

```python
root = module.__name__.split(".", 1)[0]
assert root == package, (
    f"logical name {name!r} resolved to {module.__name__!r}, "
    f"which does not belong to the {package} loader"
)
```

Update all `load_loomops(...)` call sites in `tests/test_LoomOps_tools.py`
(264 calls over 12 distinct names; each is the exact form
`load_loomops("X")["X"]`) to the namespaced key `["loomops.X"]`. A mechanical
`replaceAll` per name is acceptable.

### D2 — purge once + cache modules (`tests/conftest.py`)
Purge the package **once per fixture setup** (before the first load) and cache
imported modules for the fixture lifetime; purge again on teardown. Share one
helper between both fixtures:

```python
loaded: dict = {}

def _load(*names: str) -> dict:
    for name in names:
        if name not in loaded:
            loaded[name] = importlib.import_module(paths[name])
    return {name: loaded[name] for name in names}
```

Freshness is preserved by the function-scoped fixture; identity is preserved
within a test.

### D3 — kill the spawned child on failed (re)start (`src/plugins/manager.py`)
In the `except` blocks of `enable_plugin`, `reload_plugin`, and
`_restart_plugin`, call `self._kill_and_stop(plugin_name)` before returning
(reuse it; it kills and marks `STOPPED`). Currently a spawned child keeps
running and the 30s healthcheck can accumulate orphans.

### D4 — never auto-restart a busy plugin (`src/plugins/manager.py`)
In the `get_all_tools` `failed_plugins` loop, skip the restart when the process
is merely busy:

```python
plugin = self._plugins.get(plugin_name)
if plugin is not None and plugin.state == PluginState.BUSY:
    logger.info(
        f"Plugin '{plugin_name}' is busy; skipping auto-restart from get_all_tools"
    )
    continue
```

Only dead/unavailable plugins may be restarted there.

### D5 — tool-name collision + stale cache (`src/plugins/manager.py`)
In `_index_tools`, look up the tool's current owner in `_tool_plugins` or
`_internal_tools`; if an owner exists and is a **different** plugin, log a
warning and `continue` without writing any of `_tool_plugins`,
`_internal_tools`, `_tool_schemas`, `_tool_risk`.

In `get_all_tools`, replace `self._index_tools(...)` with
`self._rebuild_tool_cache(...)` at both extend sites so tools a plugin stopped
advertising are dropped. Make `_rebuild_tool_cache` return the external-tools
list from `_index_tools` (existing callers ignore the return value).

### D6 — test guards
- `tests/test_LoomOps_manifest.py`: add `assert data is not None` after each
  `load_manifest(LOOMOPS_DIR)` in the three tests that index into `data`.
- `tests/test_LoomWG_e2e.py`: capture the `wg-quick down` result and print a
  warning to stderr on non-zero returncode; catch `subprocess.TimeoutExpired`
  so a hung teardown cannot mask the original error; derive a unique interface
  name per run (≤15 chars, e.g. from `os.getpid()`), pick a free port, and/or
  assert the interface is not already active before bringing it up.
- `tests/test_LoomWG_plugin.py`: correlate responses by `id` in all three tests
  (`by_id = {r["id"]: r for r in responses}`), assert the expected id set is
  present (fail loudly with the raw responses), then assert via `by_id[1]` /
  `by_id[2]`.
- `tests/test_LoomWG_manifest.py`: derive every capability bit from
  `manifest_module._CAPABILITIES[...]` (three lambdas, ~L88/L113/L134); remove
  all literal capability numbers from the file.

## 4. Data model / interfaces

None. No schema, API, event, or wire changes.

## 5. Parts

1. `tests/conftest.py` — D1 + D2.
2. `tests/test_LoomOps_tools.py` — namespaced call sites (D1).
3. `src/plugins/manager.py` — D3 + D4 + D5.
4. `tests/test_LoomOps_manifest.py` — D6.
5. `tests/test_LoomWG_e2e.py` — D6.
6. `tests/test_LoomWG_plugin.py` — D6.
7. `tests/test_LoomWG_manifest.py` — D6.
8. `CHANGELOG.md` — one short dated entry (RULES 14).

## 6. Tests

- `tests/test_plugin_manager.py` — existing suites must stay green. Add
  regression tests:
  - a twice-published (different-owner) tool name is ignored with a warning and
    routing/schema stay with the first owner;
  - `get_all_tools` drops a tool the plugin no longer advertises;
  - `get_all_tools` does not restart a `BUSY` plugin;
  - a failed `enable_plugin` / `reload_plugin` / `_restart_plugin` kills the
    spawned child (`_kill_and_stop` called).
- Existing loader tests must pass with the new namespaced keys.

Gate commands:

```
ruff check .
pytest -m "not integration and not e2e"
pytest tests/test_plugin_manager.py tests/test_LoomOps_tools.py \
       tests/test_LoomOps_manifest.py tests/test_LoomWG_manifest.py \
       tests/test_LoomWG_plugin.py
```

Optional (Linux + root only): `sudo LOOMWG_E2E=1 pytest -m e2e`.

## 7. Acceptance criteria

- `ruff check .` clean; fast unit suite green.
- No logical-name key is shared between `_MODULE_PATHS` and
  `_LOOMOPS_MODULE_PATHS`; each loader asserts the module's root package.
- A loader called twice inside one test returns the same module objects.
- Failed (re)start never leaves a spawned child; a `BUSY` plugin is never
  auto-restarted from `get_all_tools`; a duplicated tool name is rejected with a
  warning; stale tools are dropped.
- No test relies on positional JSON-RPC response order.
- No literal capability bit remains in `test_LoomWG_manifest.py`.
- Working tree contains no edits outside the files in §5.

## 8. Commits (coordinator commits only after acceptance, RULES 0a)

1. `test(conftest): disambiguate plugin module keys and keep module identity`
2. `fix(plugins): kill failed spawns, skip busy restarts, guard tool-name collisions`
3. `test(plugins): harden manifest, e2e, and JSON-RPC contract tests`

## 9. Risks

- Renaming LoomOps logical keys touches 264 call sites in one test file; a
  mechanical replaceAll plus the full suite run catches mistakes.
- `_rebuild_tool_cache` gaining a return value is backward compatible (all
  current callers ignore it).
- Collision rejection changes behaviour only when two plugins publish the same
  tool name, which is already an error state.
- The e2e changes are inert unless `LOOMWG_E2E=1` on a Linux root host.
