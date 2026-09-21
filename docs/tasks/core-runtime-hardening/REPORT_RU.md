# REPORT_RU.md — core runtime hardening (version / db typing / timing / startup / risk / core tools)

Task spec: `docs/tasks/core-runtime-hardening/TZ_RU.md`.

## Goal

Close six review findings across core runtime modules:

1. `src/core/version.py` had a silent `PackageNotFoundError` fallback to a valid
   PEP 440 value (`0.0.1`), so `check_compatibility` gated plugins against a
   fabricated version with no signal.
2. `src/db/database.py`'s `get_db` is an async generator but was annotated
   `-> AsyncSession`.
3. `src/core/timing.py` interpolated caller-supplied values verbatim into one
   `[timing]` line, so a newline could forge/split log records.
4. `src/core/startup_checks.py` only proved the revocation store's parent
   directory existed, never that the store file was writable.
5. `src/core/security.py`'s risk heuristic missed obvious destructive stems and
   rebuilt its literal sets on every call.
6. `src/core/core_tools.py` aborted the whole listing on a malformed plugin
   catalog and did not enforce the approval requirement documented in the module.

## Delivered

- `src/core/version.py`: `logging` + `logger = logging.getLogger("loomcore.version")`
  and a `_resolve_version()` helper that returns
  `importlib.metadata.version("loomcore")` on success and, on
  `PackageNotFoundError`, logs the fallback warning and returns
  `_FALLBACK_VERSION = "0.0.1"`. `CORE_VERSION = _resolve_version()`.
- `src/db/database.py`: `from collections.abc import AsyncGenerator`;
  `async def get_db() -> AsyncGenerator[AsyncSession, None]:` (body unchanged).
- `src/core/timing.py`: new `_render_field(key, value)` that replaces CR/LF with
  spaces; `log_stage` builds `extra` from it. The stage name is a code literal
  and is not sanitised.
- `src/core/startup_checks.py`: `_check_revocation_store` now opens the store in
  append mode (`with path.open("a", encoding="utf-8"): pass`) after creating the
  parent, so a missing file is created silently and an unwritable store warns
  with the new wording `cannot be persisted to`.
- `src/core/security.py`: hoisted the four literal sets to module-level
  `frozenset`s `_EXPLICIT_DANGEROUS`, `_EXPLICIT_SAFE`, `_DANGEROUS_TOKENS`,
  `_MODERATE_TOKENS`; extended `_DANGEROUS_TOKENS` with `remove`, `kill`,
  `stop`, `wipe`, `purge`, `uninstall` and `_MODERATE_TOKENS` with `install`,
  `disable`, `enable`. Whole-token matching, the explicit-set precedence, the
  declared-risk override and the `safe` default are unchanged.
- `src/core/core_tools.py`:
  - `_core_list_tools` keeps `get_tools` inside its `try` and raises
    `ValueError(f"plugin '{name}' returned a non-list tool catalog")` for a
    non-list catalog, so it is reported as `<name> (unavailable)`. `names` is
    built defensively from dict entries with a string `name`.
  - `dispatch_core_tool(name, arguments=None, *, approved=False)` now raises
    `PermissionError(f"Core tool '{name}' requires approval")` when
    `resolve_tool_risk(name) == "dangerous"` and not approved, before argument
    validation/dispatch.
- `src/core/agent.py`: `_SafeDispatch` is now a `typing.Protocol` whose
  `__call__(self, tool, args, *, approved=False) -> dict` documents the gate;
  `execute_plan` calls `dispatch(step["tool"], step["args"], approved=approved)`.
- `src/interfaces/websocket.py`: `_execute_tool(tool_name, arguments=None, *,
  approved=False)` forwards `approved=` to `dispatch_core_tool`; the plugin
  branch ignores it.

## Verification

- `uv run ruff check .` — clean (`All checks passed!`).
- `uv run pytest tests/test_version.py tests/test_db_database.py
  tests/test_timing.py tests/test_startup_checks.py tests/test_security.py
  tests/test_core_tools.py tests/test_agent.py tests/test_websocket.py -q` —
  135 passed (log: `tmp/tests_core_runtime_targeted.log`).
- `uv run pytest -m "not integration and not e2e" -q` — 961 passed, 11 skipped,
  5 deselected (log: `tmp/tests_core_runtime_full.log`). Previous suite size was
  945; the sixteen new tests account for the delta (2 version + 1 db + 1 timing +
  9 security parametrizations + 2 core_tools + 1 websocket).
- Integration tests not run locally (no Postgres).

## Changed files

- `src/core/version.py` — `_resolve_version()` + fallback warning.
- `src/db/database.py` — async-generator return annotation.
- `src/core/timing.py` — `_render_field` newline sanitisation.
- `src/core/startup_checks.py` — store writability probe + reworded warning.
- `src/core/security.py` — module-level risk frozensets + extended stems.
- `src/core/core_tools.py` — defensive listing + dispatch approval guard.
- `src/core/agent.py` — `_SafeDispatch` protocol + threading `approved`.
- `src/interfaces/websocket.py` — `_execute_tool` forwards `approved`.
- `tests/test_version.py`, `tests/test_db_database.py`, `tests/test_timing.py` —
  new.
- `tests/test_startup_checks.py`, `tests/test_security.py`,
  `tests/test_core_tools.py`, `tests/test_agent.py`, `tests/test_websocket.py` —
  new/updated.
- `docs/tasks/core-runtime-hardening/REPORT_RU.md` — this report.

## Notes and follow-ups

- New tests: `test_resolve_version_uses_metadata`,
  `test_resolve_version_warns_on_fallback`; `test_get_db_is_annotated_async_generator`;
  `test_log_stage_sanitizes_newlines`; `test_destructive_stems_are_dangerous`
  (6 params), `test_install_enable_disable_are_moderate` (3 params);
  `test_dispatch_requires_approval_for_dangerous_core_tool`,
  `test_core_list_tools_ignores_malformed_plugin_tools`;
  `test_execute_tool_forwards_approval`. `tests/test_startup_checks.py`'s
  `test_unwritable_revocation_store_warns` now points the store at a directory
  and asserts the new wording, and `test_secure_posture_is_silent` asserts the
  store file is created.
- The `approved` keyword is threaded through `_SafeDispatch`, `execute_plan` and
  the single production dispatcher `_execute_tool` in the same change; every test
  dispatcher/fake gained `*, approved=False`, so a missed one fails loudly with
  `TypeError` rather than silently skipping the gate.
- Opening the store at startup now creates an empty file when it is missing; this
  is intended (it is the configured persistence path).
- The risk-stem extension matches whole underscore/dash tokens only and the
  explicit sets still win, so known tools (`ops_address_lookup`, the LoomWG and
  LoomOps read-only names) are unaffected; default stays `safe`.
- No change to the plan schema, event payload shape, approval UX, DB schema,
  `CORE_VERSION`'s value, or plugin protocol; no new dependencies.
- Suggested commits (coordinator commits after acceptance — RULES 0a):
  1. `fix(core): warn on version fallback, type get_db as a generator, sanitize timing fields`
  2. `fix(core): verify revocation store writability and hoist risk token sets`
  3. `fix(core): harden core-tool listing and enforce approval at dispatch`

Commits created: none
