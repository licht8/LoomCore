# TZ_RU.md — core risk denylist and LoomOps write cap

> Copy to `docs/tasks/<kebab-name>/TZ_RU.md` and fill in **before** starting
> (RULES 16). Content is English (RULES 0). Keep it specific and testable.

## 1. Goal

Close two security/logic gaps found in the audit:

- **A plugin-declared `risk` currently overrides the core's explicit dangerous
  list.** `classify_tool_risk` returns a valid `declared` value *before*
  consulting `_EXPLICIT_DANGEROUS` (`src/core/security.py:224-225`). A plugin
  that advertises a denylisted tool (e.g. `ops_run_command`, `ops_write_file`)
  as `safe` would have it treated as read-only: no approval
  (`plan_requires_approval` -> False) and no audit row (`_run_plan.audit_step`
  returns early for `risk == "safe"`, `src/interfaces/websocket.py:199-201`).
  The core denylist must always win.
- **LoomOps `ops_write_file` has no size cap.** `read_file` is capped at
  `MAX_READ_BYTES = 256_000` (`plugins/LoomOps/LoomOps/ops/files.py:11,63-68`)
  but `write_file` encodes and writes an arbitrarily large `content`
  (`:77-89`), so an approved write can fill the host/container disk.

## 2. Non-goals

- No manifest schema/signing change: a plugin-declared risk still applies to
  tools that are **not** on the core denylist.
- No change to the token heuristics, `_EXPLICIT_SAFE`, the plan executor, or the
  approval flow.
- No new configuration knob (the write cap mirrors the existing read cap).

## 3. Approved decisions (locked)

### D1 — the explicit dangerous list wins over a declared risk

- `src/core/security.py`, `classify_tool_risk(tool_name, declared=None)`:
  evaluate in this order:
  1. `tool_lower in _EXPLICIT_DANGEROUS` -> `"dangerous"` (denylist can never be
     downgraded);
  2. a valid `declared` (`str`, stripped/lowercased in `VALID_TOOL_RISKS`) ->
     return it (a plugin may still mark non-denylisted tools safe/moderate);
  3. `tool_lower in _EXPLICIT_SAFE` -> `"safe"`;
  4. the `_DANGEROUS_TOKENS` / `_MODERATE_TOKENS` heuristics.
- Update the docstring: "The core dangerous denylist always wins; otherwise a
  declared risk takes precedence over the name heuristics."
- Effect: for a denylisted tool `resolve_tool_risk` (`src/core/core_tools.py:137-140`)
  returns `"dangerous"` regardless of the plugin declaration, so
  `plan_requires_approval` is True and `audit_step` records the mutation.
- Note: `declared == "dangerous"` on an `_EXPLICIT_SAFE` name stays `"dangerous"`
  (step 2 runs before step 3), preserving today's conservative behavior.

### D2 — cap the LoomOps write payload

- `plugins/LoomOps/LoomOps/ops/files.py`: add
  `MAX_WRITE_BYTES = 256_000` next to `MAX_READ_BYTES`. In `write_file`, after
  `data = content.encode("utf-8")` and before `confine.write_atomic`, raise
  `ValueError(f"content exceeds {MAX_WRITE_BYTES} bytes")` when
  `len(data) > MAX_WRITE_BYTES`.
- `plugins/LoomOps/LoomOps/schemas.py`: extend the `ops_write_file` description
  with "Content is capped at 256 KB." (no schema or risk change).

## 4. Data model / interfaces

- No API, DB, WS or schema change. `classify_tool_risk` keeps its signature and
  `VALID_TOOL_RISKS`.
- `MAX_WRITE_BYTES` is module-level in `files.py` (mirrors `MAX_READ_BYTES`,
  referenced by tests).

## 5. Parts

1. `src/core/security.py` — reorder `classify_tool_risk`.
2. `plugins/LoomOps/LoomOps/ops/files.py` + `plugins/LoomOps/LoomOps/schemas.py`
   — write cap.

## 6. Tests

- `tests/test_security.py`: update `test_declared_risk_overrides_heuristics`
  (line ~94): `classify_tool_risk("ops_run_command", "safe")` must now be
  `"dangerous"`. Rename it `test_explicit_dangerous_overrides_declared_risk` and
  add a positive case that a declared risk still wins for a non-denylisted tool
  (e.g. `classify_tool_risk("wg_deploy_config", "safe") == "safe"`). Re-check the
  remaining cases (lines 13-126) are unaffected: denylisted names still
  `"dangerous"`, `_EXPLICIT_SAFE` names still `"safe"`, and
  `classify_tool_risk("core_health", "dangerous") == "dangerous"` still holds.
- `tests/test_LoomOps_tools.py`: add `test_write_file_rejects_oversized_content`
  (content of `files.MAX_WRITE_BYTES + 1` bytes -> `pytest.raises(ValueError)`,
  and the target file is not created). Keep the existing `test_write_file_*`
  cases green.
- Gates: `uv run ruff check .`; `uv run pytest -m "not integration and not e2e" -q`.

## 7. Acceptance criteria

- For every name in `_EXPLICIT_DANGEROUS`, `classify_tool_risk(name, "safe")`
  returns `"dangerous"`, and a declared risk still wins for non-denylisted names.
- An `ops_write_file` call with more than 256 KB of content is refused with a
  `ValueError` and writes nothing; a payload at or under the cap is written as
  before.
- `uv run ruff check .` clean; `uv run pytest -m "not integration and not e2e" -q`
  green.

## 8. Commits

1. `fix(core): force the dangerous denylist over plugin-declared risk`
2. `fix(LoomOps): cap the ops_write_file payload size`

## 9. Risks

- A plugin that legitimately declares a denylisted name as safe is now clamped to
  dangerous; that is the intent (the denylist is the trust boundary). It only
  adds an approval + audit, never removes protection.
- The 256 KB write cap may reject legitimately large writes; operators can raise
  `MAX_WRITE_BYTES` in code if a real need appears (mirrors `MAX_READ_BYTES`).
