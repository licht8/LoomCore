# REPORT_RU.md — plugin tools 5xx + lifecycle serialization

Task spec: `docs/tasks/plugin-tools-5xx-and-lifecycle-lock/TZ_RU.md`.

## Goal

Two defects on the plugin surface:

1. `GET /api/tools` (`src/api/plugins.py::list_plugins_tools`) wrapped
   `plugin_manager.get_all_tools()` in a broad `except Exception` and returned
   HTTP 200 with `{"error": str(exc)}` — leaking internals, logging nothing, and
   silently dropping the `tools` key (breaking consumers).
2. Per-plugin lifecycle mutations (`enable_plugin`, `disable_plugin`,
   `reload_plugin`) mutated shared state (`plugin.enabled`, `plugin.proc`, tool
   caches) with `await`s inside the operation but did not hold the per-plugin
   lock across the sequence, so concurrent admin requests could interleave
   (e.g. a `reload` killing a process an in-flight call was reading from).

## Delivered

- `src/api/plugins.py`:
  - Added `import logging` and module logger
    `logging.getLogger("loomcore.api.plugins")`.
  - `list_plugins_tools` now builds `tools = list(CORE_TOOLS_SCHEMA) +
    await plugin_manager.get_all_tools()`, and on failure calls
    `logger.exception("Failed to collect plugin tool schemas")` then raises
    `HTTPException(status_code=500, detail="Failed to collect tool schemas")
    from exc`. The mutable `result: dict` shape-switch was removed. Auth check
    and `@limiter.limit("60 per 60 seconds")` kept. Success shape unchanged.
- `src/plugins/manager.py`:
  - `disable_plugin`, `enable_plugin`, `reload_plugin`: kept the `plugin is
    None` guard before locking, then wrapped the whole mutating body in
    `async with plugin.lock:` and moved the enabled-state re-checks INSIDE the
    lock (no TOCTOU on the state the caller waited on). The reused lock is the
    existing per-plugin reentrant `_PluginLock` (field
    `ManagedPlugin.lock`); no new lock type was added. Exception-revert paths
    stay inside the lock. `_restart_plugin`/`restart_plugin` were NOT touched
    (out of scope: up to 30s backoff).
- `tests/test_api_plugins.py`: `import logging`; replaced
  `test_tools_endpoint_reports_error` with
  `test_tools_endpoint_returns_500_on_failure` (asserts 500,
  `{"detail": "Failed to collect tool schemas"}`, raw `"plugin down"` absent
  from the body, and the log record present).
- `tests/test_plugin_manager.py`: added
  `test_lifecycle_mutations_are_serialized` in the enable/disable/reload
  section (asserts a concurrent `disable` does not call `_kill_and_stop` until
  the in-flight `enable` finishes).

## Verification

- `uv run ruff check .` — clean (`All checks passed!`).
- `uv run pytest tests/test_api_plugins.py tests/test_plugin_manager.py -q` —
  74 passed (log: `tmp/tests_plugin_tools_lock.log`).
- `uv run pytest -m "not integration and not e2e" -q` — 919 passed, 11 skipped,
  5 deselected (log: `tmp/tests_plugin_tools_lock_full.log`). Previous suite
  size was 918; the one new test plus the reworked `tools` test account for the
  delta.
- Confirmed the new serialization test fails without the fix: with
  `src/plugins/manager.py` stashed it fails at `assert killed == []`
  (`['demo'] == []`), and passes once the lock is restored. `git stash` was
  popped cleanly; working tree retained all changes.
- Integration tests not run locally (no Postgres).

## Changed files

- `src/api/plugins.py` — log + 500 on tool-collection failure.
- `src/plugins/manager.py` — hold the plugin lock across lifecycle mutations.
- `tests/test_api_plugins.py` — 5xx failure-path test.
- `tests/test_plugin_manager.py` — lifecycle serialization test.
- `docs/tasks/plugin-tools-5xx-and-lifecycle-lock/REPORT_RU.md` — this report.

## Notes and follow-ups

- Reentrancy: `_PluginLock` is keyed on `asyncio.current_task()`, so the nested
  `_initialize_plugin`/`get_tools` → `_exchange` → `plugin.lock` acquisitions in
  the same task still work; `test_exchange_lock_is_reentrant_for_restart` stays
  green.
- Trade-off: a lifecycle op now holds the lock for its whole sequence, so a
  concurrent tool call/lifecycle request on the SAME plugin waits (bounded by
  the existing initialize 10s / tools-list 30s timeouts); other plugins are
  unaffected (per-plugin lock).
- `_restart_plugin` (auto-restart / background healthcheck) still does not take
  the lock, so a healthcheck restart can race a concurrent `disable` — left as
  a follow-up per TZ §9.
- `CHANGELOG.md` entry is the coordinator's per RULES 14 (not touched here).
- Suggested commits (coordinator commits after acceptance — RULES 0a):
  1. `fix(api): log and 5xx on plugin tool collection failure`
  2. `fix(plugins): serialize per-plugin lifecycle mutations with the plugin lock`

Commits created: none
