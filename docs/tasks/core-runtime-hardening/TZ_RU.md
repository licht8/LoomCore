# TZ_RU.md — core runtime hardening (version / db typing / timing / startup / risk / core tools)

> Coordinator spec (RULES 16). Content is English (RULES 0).

## 1. Goal

Close six review findings across core runtime modules:

1. `src/core/version.py` — the `PackageNotFoundError` fallback is silent and its
   value (`"0.0.1"`) is a valid PEP 440 release, so `check_compatibility` gates
   plugins against a fabricated version with no signal. Emit a warning.
2. `src/db/database.py` — `get_db` is an async generator but annotated
   `-> AsyncSession`. Annotate it as an async generator.
3. `src/core/timing.py` — caller-supplied field values are interpolated verbatim
   into a single `[timing]` line, so a newline can forge/split log records.
4. `src/core/startup_checks.py` — the revocation-store check only proves the
   parent directory exists, never that the store file is writable, so a
   read-only store passes startup silently.
5. `src/core/security.py` — the risk heuristic misses obvious destructive stems
   (`remove`, `kill`, `stop`, `uninstall`, `purge`, `wipe`) and rebuilds its
   literal sets on every call.
6. `src/core/core_tools.py` — (a) `_core_list_tools` extracts `tool["name"]`
   outside its `try`, so a malformed plugin catalog aborts the whole "safe"
   listing; (b) `dispatch_core_tool` does not enforce the approval requirement
   documented at the top of the module.

## 2. Non-goals

- No change to the approval UX, agent planner, or the plan persistence format.
- No change to `check_compatibility` semantics, `CORE_VERSION`'s value, or the
  plugin protocol. The fallback stays `"0.0.1"`; it only gains a warning.
- No default-risk change: `classify_tool_risk` still returns `safe` for an
  unrecognised name (explicit read-only names remain the allowlist). Adding
  stems is the chosen remedy; "unknown -> moderate" is rejected (it would gate
  legitimately safe tools that lack an `explicit_safe` entry, e.g.
  `ops_address_lookup`).
- No redaction/format change for `[timing]` lines beyond newline sanitisation.

## 3. Approved decisions (locked)

- **D1 (version).** Keep `_FALLBACK_VERSION = "0.0.1"`. Replace the module-level
  `try` with a helper `_resolve_version() -> str` that returns
  `importlib.metadata.version("loomcore")` on success and, on
  `PackageNotFoundError`, logs
  `logger.warning("Core package metadata not found (source checkout or broken install); using fallback version %s. Plugin compatibility checks use this value.", _FALLBACK_VERSION)`
  and returns `_FALLBACK_VERSION`. `CORE_VERSION = _resolve_version()`.
  Logger name: `loomcore.version`. No new public constant/flag.
- **D2 (db typing).** `from collections.abc import AsyncGenerator`; annotate
  `async def get_db() -> AsyncGenerator[AsyncSession, None]:`. Body unchanged.
- **D3 (timing).** Add `_render_field(key, value)` that returns
  `f"{key}={str(value).replace(chr(13), ' ').replace(chr(10), ' ')}"` and build
  `extra = " ".join(_render_field(k, v) for k, v in fields.items())`. CR and LF
  in a value become a single space; the stage name is a code literal and is not
  sanitised.
- **D4 (startup).** In `_check_revocation_store`, after
  `path.parent.mkdir(parents=True, exist_ok=True)` open the store to prove the
  file itself is writable: `with path.open("a", encoding="utf-8"): pass`. Reword
  the warning to
  `"Revocation store '%s' cannot be persisted to (%s); falling back to in-memory and logout will not survive a restart."`.
  The absent-file case is silent and creates an empty store file.
- **D5 (risk).** Hoist the four literal sets to module-level `frozenset`s
  (`_EXPLICIT_DANGEROUS`, `_EXPLICIT_SAFE`, `_DANGEROUS_TOKENS`,
  `_MODERATE_TOKENS`). Extend `_DANGEROUS_TOKENS` with
  `remove`, `kill`, `stop`, `wipe`, `purge`, `uninstall` (keep the existing
  `restart`, `delete`, `drop`, `reboot`, `rm`, `chmod`, `flush`). Extend
  `_MODERATE_TOKENS` with `install`, `disable`, `enable` (keep `create`, `add`,
  `set`, `config`, `deploy`). Keep the whole-token split and the
  declared-risk override. Default stays `safe`.
- **D6a (core tool listing).** In `_core_list_tools`, keep `get_tools` inside the
  `try`; treat a non-list return as a failure by raising
  `ValueError(f"plugin '{name}' returned a non-list tool catalog")` inside the
  `try`. Build `names` defensively:
  `[tool["name"] for tool in tools if isinstance(tool, dict) and isinstance(tool.get("name"), str)]`.
- **D6b (approval enforcement).** `dispatch_core_tool(name, arguments=None, *, approved: bool = False)`
  raises `PermissionError(f"Core tool '{name}' requires approval")` when
  `resolve_tool_risk(name) == "dangerous"` and not `approved`, before argument
  validation/dispatch. Thread the plan's approval to it:
  - `src/core/agent.py`: `_SafeDispatch` becomes a `typing.Protocol` whose
    `__call__(self, tool, args, *, approved: bool = False) -> dict`;
    `execute_plan` calls `dispatch(step["tool"], step["args"], approved=approved)`.
  - `src/interfaces/websocket.py`: `_execute_tool(tool_name, arguments=None, *, approved=False)`
    forwards `approved=` to `dispatch_core_tool` (the plugin branch ignores it).
  - Existing test dispatchers/fakes gain the `*, approved=False` keyword.

## 4. Data model / interfaces

- `dispatch_core_tool` gains a keyword-only `approved: bool = False` parameter.
- `_execute_tool` gains a keyword-only `approved: bool = False` parameter.
- `_SafeDispatch` (agent.py) changes from a `Callable` alias to a `Protocol`
  documenting `approved`.
- No DB, API, manifest, or event-schema changes.

## 5. Parts

1. `src/core/version.py` — add `logging` + `logger`; add `_resolve_version()`
   (D1); `CORE_VERSION = _resolve_version()`.
2. `src/db/database.py` — import `AsyncGenerator`; fix the return annotation (D2).
3. `src/core/timing.py` — add `_render_field`; use it in `log_stage` (D3).
4. `src/core/startup_checks.py` — open the store in append mode; reword the
   warning (D4).
5. `src/core/security.py` — hoist the four sets to module-level frozensets;
   extend the token sets (D5).
6. `src/core/core_tools.py` — defensive listing (D6a); approval guard in
   `dispatch_core_tool` (D6b).
7. `src/core/agent.py` + `src/interfaces/websocket.py` — thread `approved`
   through `_SafeDispatch`/`_execute_tool` (D6b).

## 6. Tests

- New `tests/test_version.py`: `test_resolve_version_uses_metadata` (monkeypatch
  `version_module.version` -> `"1.2.3"`; `_resolve_version() == "1.2.3"`);
  `test_resolve_version_warns_on_fallback` (monkeypatch `version` to raise
  `PackageNotFoundError`; `caplog.at_level(logging.WARNING, logger="loomcore.version")`;
  result `== "0.0.1"` and a warning mentioning `fallback`).
- New `tests/test_db_database.py`:
  `test_get_db_is_annotated_async_generator` asserting
  `database.get_db.__annotations__["return"] == AsyncGenerator[AsyncSession, None]`.
- New `tests/test_timing.py`: `test_log_stage_sanitizes_newlines` (monkeypatch
  `settings.timing_enabled = True`; `caplog.at_level(logging.INFO, logger="loomcore.timing")`;
  `log_stage("s", 1.0, tool="a\nb\r")`; the record message contains `tool=a b`
  and no `"\n"`).
- `tests/test_startup_checks.py`: update `test_unwritable_revocation_store_warns`
  to point `loomcore_revoked_file` at a path that is an existing **directory**
  (`tmp_path / "store"` created as a dir) and assert `"cannot be persisted to"`
  in the warning; extend `test_secure_posture_is_silent` to assert the store file
  now exists.
- `tests/test_security.py`: add `test_destructive_stems_are_dangerous`
  (`x_remove_user`, `x_kill_worker`, `x_stop_service`, `x_uninstall`,
  `x_purge`, `x_wipe` -> `dangerous`) and `test_install_enable_disable_are_moderate`
  (`x_install_tool`, `x_disable_feature`, `x_enable_feature` -> `moderate`).
- `tests/test_core_tools.py`: update the lifecycle calls to pass `approved=True`;
  add `test_dispatch_requires_approval_for_dangerous_core_tool`
  (`PermissionError`, manager not called) and
  `test_core_list_tools_ignores_malformed_plugin_tools` (fake `get_tools` returns
  `[{"name": "ok"}, "junk", {"no_name": 1}, {"name": 2}]` ->
  `plugin_tools == [{"name": "ok", "plugin": "LoomOps"}]`; a non-list return ->
  `"LoomOps (unavailable)"`).
- `tests/test_agent.py`: update the six dispatcher fakes to accept
  `*, approved=False`; in `test_execute_plan_runs_approved_risky_step` record the
  received `approved` and assert it is `True`.
- `tests/test_websocket.py`: update `fake_dispatch` to accept
  `*, approved=False`; add `test_execute_tool_forwards_approval` asserting
  `_execute_tool("core_health", {}, approved=True)` forwards `approved=True`.

Gates:

```
uv run ruff check .
uv run pytest tests/test_version.py tests/test_db_database.py tests/test_timing.py tests/test_startup_checks.py tests/test_security.py tests/test_core_tools.py tests/test_agent.py tests/test_websocket.py -q
uv run pytest -m "not integration and not e2e" -q
```

## 7. Acceptance criteria

- `_resolve_version()` logs one `loomcore.version` warning and returns `"0.0.1"`
  when package metadata is missing; returns the real version otherwise.
- `get_db`'s return annotation is `AsyncGenerator[AsyncSession, None]`.
- A `\n`/`\r` in a `log_stage` field cannot split the `[timing]` record.
- A revocation store whose file cannot be opened is reported at startup; a
  missing store file is created silently.
- The new destructive/moderate stems classify as documented; existing
  classifications/tests are unchanged.
- `_core_list_tools` survives a malformed/non-list plugin catalog and still
  returns the core tools.
- `dispatch_core_tool("core_enable_plugin", ...)` raises `PermissionError`
  without `approved=True`; an approved call reaches the handler.
- All gates pass.

## 8. Commits

1. `fix(core): warn on version fallback, type get_db as a generator, sanitize timing fields`
2. `fix(core): verify revocation store writability and hoist risk token sets`
3. `fix(core): harden core-tool listing and enforce approval at dispatch`

## 9. Risks

- `execute_plan`'s dispatcher contract gains a keyword; every test dispatcher and
  the single production dispatcher are updated in the same change, so a missed
  one fails loudly (`TypeError`) rather than silently skipping the gate.
- Opening the revocation store at startup creates an empty file as a side
  effect; intended (it is the configured persistence path).
- The risk-stem extension can reclassify a plugin tool not covered by the
  explicit sets; only whole underscore/dash tokens match and the explicit sets
  continue to win, so the known tools are unaffected.
