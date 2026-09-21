# REPORT_RU.md — core config, confirmations, contracts and audit hardening

Task spec: `docs/tasks/core-config-audit-hardening/TZ_RU.md`.

## Goal

Close five review findings across four core modules:

1. `src/config.py` shipped a `database_url` default with embedded credentials
   (`loomuser:secret_password`), so a deployment that forgot `DATABASE_URL`
   silently connected with well-known credentials.
2. `src/core/confirmations.py` loaded phrases longer than
   `MAX_CONFIRMATION_WORDS` (dead config) and memoized load **failures**, so a
   missing/corrupt file was never re-read for the process lifetime.
3. `src/core/contracts.py` read `required` from the top-level schema, producing
   `"<tool> is missing required argument(s): "` (empty) for nested violations.
4. `src/core/audit.py` exposed a session-taking helper that commits/rolls back
   the caller's session, and `verify_chain` could not detect truncation.
5. Document `DATABASE_URL` in `.env.example`.

## Delivered

- `src/config.py`:
  - New module constant
    `DEFAULT_DATABASE_URL = "postgresql+asyncpg://localhost:5432/loomcore"` with
    a comment noting it is a deliberate credential-free placeholder.
  - `database_url: str = DEFAULT_DATABASE_URL` (no fail-fast validator).
- `.env.example`: commented optional `DATABASE_URL=` line explaining compose
  derives it from `POSTGRES_*`.
- `src/core/confirmations.py`:
  - New module-private `class _PhrasesUnavailable(Exception)`.
  - `_load_cached` now raises `_PhrasesUnavailable` on a missing/corrupt file or
    a non-object payload instead of returning empty sets, so `lru_cache` no
    longer memoizes failures.
  - `load_phrases` catches `_PhrasesUnavailable`, logs one warning, and returns
    the empty sets (fail-safe, retried next call).
  - `_phrase_set(value, label)` drops normalized phrases longer than
    `MAX_CONFIRMATION_WORDS` and logs one warning per list naming the dropped
    phrases (sorted).
  - New `clear_phrase_cache()` -> `_load_cached.cache_clear()`.
  - Module docstring refreshed (word cap, dead-entry warning, no failure
    caching, explicit invalidation).
- `src/core/contracts.py`: the `error.validator == "required"` branch now reads
  `error.validator_value` (the failing subschema's own `required` list), builds
  `missing` only when it is a list, and falls back to
  `f"{tool_name}: {error.message}"` when no names are extracted.
- `src/core/audit.py`:
  - `record_mutation` -> `_record_mutation`, with a docstring stating it
    commits/rolls back the passed session and must only receive a dedicated
    session; `record_mutation_isolated` is the public entry point and now calls
    `_record_mutation`. The advisory-lock comment updated.
  - `verify_chain` gained keyword-only `expected_count` / `expected_head`:
    length mismatch, or (with `expected_head`) an empty list / last-hash
    mismatch, return `False`. Module + function docstrings corrected to state
    truncation is only detectable with an independent anchor.
- Tests (see below).

## Verification

- `uv run ruff check .` — clean (`All checks passed!`).
- `uv run pytest tests/test_config.py tests/test_confirmations.py
  tests/test_contracts.py tests/test_audit.py -q` — 58 passed (log:
  `tmp/tests_core_hardening_targeted.log`).
- `uv run pytest -m "not integration and not e2e" -q` — 934 passed, 11 skipped,
  5 deselected (log: `tmp/tests_core_hardening_full.log`). Previous suite size
  was 924; the ten new tests account for the delta (2 config + 3 confirmations +
  2 contracts + 3 audit).
- `rg "secret_password|loomuser" src/` — no matches (no literal credentials).
- `rg "\brecord_mutation\b" src/` — no matches (only
  `record_mutation_isolated` remains).
- Integration tests not run locally (no Postgres).

## Changed files

- `src/config.py` — `DEFAULT_DATABASE_URL` constant + credential-free default.
- `.env.example` — documented optional `DATABASE_URL`.
- `src/core/confirmations.py` — phrase filtering + warning, failure-retrying
  cache, `clear_phrase_cache()`, docstring.
- `src/core/contracts.py` — nested `required` names with generic fallback.
- `src/core/audit.py` — private `_record_mutation` + ownership docstring;
  anchored `verify_chain`.
- `tests/test_config.py`, `tests/test_confirmations.py`,
  `tests/test_contracts.py`, `tests/test_audit.py` — new/retargeted tests.
- `docs/tasks/core-config-audit-hardening/REPORT_RU.md` — this report.

## Notes and follow-ups

- New tests: `test_database_url_default_has_no_embedded_credentials` /
  `test_database_url_default_is_the_placeholder` (config);
  `test_long_phrases_are_dropped_with_a_warning`,
  `test_missing_config_is_not_cached`,
  `test_clear_phrase_cache_rereads_edited_file` (confirmations);
  `test_validate_tool_args_nested_required_lists_names`,
  `test_validate_tool_args_required_without_names_falls_back` (contracts);
  `test_verify_chain_detects_truncation_with_expected_count`,
  `test_verify_chain_detects_truncation_with_expected_head`,
  `test_verify_chain_empty_is_true_without_anchor_and_false_with_expected_head`
  (audit). The four `record_mutation(` unit tests were retargeted to
  `_record_mutation`.
- The shipped `src/core/confirmations.json` has no phrase longer than three
  words, so the new filter emits no warning for the default config.
- `_record_mutation` is now private; the only production entry point remains
  `record_mutation_isolated` and its two call sites (`src/api/plugins.py:64`,
  `src/interfaces/websocket.py:165`) are unchanged.
- `verify_chain` keeps the permissive default (empty list is `True` without an
  anchor); the module docstring now states the limitation explicitly.
- Changing the confirmations cache to retry failures re-reads a broken path on
  every confirmation message while a plan is pending; the repeated warning is
  intentional operator feedback.
- No DB migration, API/DTO change, auth/plugin/websocket/agent change, or new
  dependency.
- Suggested commits (coordinator commits after acceptance — RULES 0a):
  1. `fix(config): drop embedded credentials from the database_url default`
  2. `fix(core): filter unmatchable confirmation phrases and stop caching load failures`
  3. `fix(core): report the failing subschema's required names`
  4. `fix(core): make audit session ownership explicit and anchor-aware chain checks`

Commits created: none
