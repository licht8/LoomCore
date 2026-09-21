# TZ_RU.md — core config, confirmations, contracts and audit hardening

> Content is English (RULES 0). Spec follows `docs/tasks/_template/TZ_RU.md`.

## 1. Goal

Close five review findings across four core modules:

1. `src/config.py` ships a usable `database_url` default with embedded
   credentials (`loomuser:secret_password`); a deployment that forgets to set
   `DATABASE_URL` silently uses well-known credentials.
2. `src/core/confirmations.py` loads phrases longer than
   `MAX_CONFIRMATION_WORDS` (3) that can never match a message (silent dead
   config), and `_load_cached` memoizes **failure** results, so a missing or
   corrupt file is never re-read for the process lifetime.
3. `src/core/contracts.py` reads `required` from the top-level schema instead of
   the failing subschema, producing `"<tool> is missing required argument(s): "`
   (empty list) for nested `required` violations.
4. `src/core/audit.py` `record_mutation` commits/rolls back the caller's session
   (the signature invites misuse), and `verify_chain` cannot detect truncation
   (including `verify_chain([]) is True`) despite the module docstring implying
   otherwise.

## 2. Non-goals

- No DB migration, no API/DTO change, no behavioural change to auth, plugins,
  websocket or the agent loop.
- No change to `record_mutation_isolated` call sites
  (`src/api/plugins.py:64`, `src/interfaces/websocket.py:165`).
- No new dependencies.

## 3. Approved decisions (locked)

1. **`database_url`**: default becomes the **credential-free placeholder**
   `postgresql+asyncpg://localhost:5432/loomcore`; expose it as module constant
   `DEFAULT_DATABASE_URL`. No hard fail-fast validator: `Settings()` is
   constructed at import (`src/config.py` `settings = get_settings()`) and
   throughout tests, so raising on the placeholder would force every caller to
   set `DATABASE_URL`. Compose already always sets `DATABASE_URL`
   (`docker-compose.yml:53`, from `POSTGRES_*`), so deployments are unaffected;
   the placeholder removes the embedded secret and surfaces a misconfiguration
   at connect time. Document the setting in `.env.example`.
2. **confirmations filtering**: `_phrase_set` drops any normalized phrase whose
   word count exceeds `MAX_CONFIRMATION_WORDS` and logs one `logger.warning`
   per list naming the dropped phrases (sorted).
3. **confirmations caching**: only **successful** loads are memoized. Failures
   raise a module-private `_PhrasesUnavailable`; `lru_cache` does not cache
   exceptions, so a missing/corrupt file is retried on the next call. Add
   `clear_phrase_cache()` (calls `_load_cached.cache_clear()`) for explicit
   invalidation after a config edit.
4. **contracts**: in the `error.validator == "required"` branch use
   `error.validator_value` (the failing subschema's own `required` list); if no
   usable names are extracted, raise the generic
   `f"{tool_name}: {error.message}"` instead of an empty list.
5. **audit ownership**: make the session-taking helper private —
   rename `record_mutation` → `_record_mutation` — and document that it
   **commits or rolls back the passed session** and must only be called with a
   session dedicated to the audit append. `record_mutation_isolated` stays the
   public API and keeps creating its own session.
6. **audit truncation**: `verify_chain` gains keyword-only anchors
   `expected_count: int | None = None` and `expected_head: str | None = None`.
   With neither, behaviour is unchanged. With `expected_count`, a list whose
   length differs returns `False`. With `expected_head`, an empty list or a
   last row whose `entry_hash` differs returns `False`. Update the module
   docstring to state that truncation is only detected when a caller supplies
   an independently known anchor.

## 4. Data model / interfaces

- `src/config.py`: new module constant `DEFAULT_DATABASE_URL: str`; field
  `database_url: str = DEFAULT_DATABASE_URL`.
- `src/core/confirmations.py`: new `class _PhrasesUnavailable(Exception)`;
  new `def clear_phrase_cache() -> None`; `_phrase_set(value, label)` gains a
  label argument.
- `src/core/contracts.py`: no signature change.
- `src/core/audit.py`: `record_mutation` → `_record_mutation`; `verify_chain`
  gains keyword-only `expected_count` / `expected_head`.
- `.env.example`: a commented, optional `DATABASE_URL` line.

## 5. Parts

1. **src/config.py** — add `DEFAULT_DATABASE_URL`, change the field default,
   and add a short comment noting it is a deliberate credential-free
   placeholder.
2. **.env.example** — add a commented optional `DATABASE_URL` line explaining
   compose derives it from `POSTGRES_*`.
3. **src/core/confirmations.py** — filter + warn in `_phrase_set`; convert
   load-time failures to `_PhrasesUnavailable` so `lru_cache` no longer
   caches them; add `clear_phrase_cache()`; refresh the module docstring.
4. **src/core/contracts.py** — use `error.validator_value` with the generic
   fallback.
5. **src/core/audit.py** — rename to `_record_mutation` + ownership docstring
   (update the `record_mutation_isolated` call); anchor-aware `verify_chain`
   and corrected module/function docstrings.
6. **tests** — see §6.

## 6. Tests

- `tests/test_config.py`
  - `test_database_url_default_has_no_embedded_credentials` — parse the default
    with `sqlalchemy.engine.make_url` and assert `.username is None` and
    `.password is None`.
  - `test_database_url_default_is_the_placeholder` — equals
    `DEFAULT_DATABASE_URL`.
- `tests/test_confirmations.py`
  - `test_long_phrases_are_dropped_with_a_warning` — a config with
    `"go ahead now please"` plus `"yes"`: the long phrase is not loaded and a
    warning naming it is logged (`caplog.at_level("WARNING")`).
  - `test_missing_config_is_not_cached` — a missing path returns empty sets,
    then writing the file and loading again yields the phrases.
  - `test_clear_phrase_cache_rereads_edited_file` — after editing the file, a
    second load is still the cached value until `clear_phrase_cache()`, then
    reflects the edit.
- `tests/test_contracts.py`
  - `test_validate_tool_args_nested_required_lists_names` — nested `required`
    yields `"missing required argument(s): <nested-name>"`.
  - `test_validate_tool_args_required_without_names_falls_back` — a fake error
    with `validator="required"` and a non-list `validator_value` raises
    `"<tool>: <error.message>"`.
- `tests/test_audit.py`
  - retarget the three `record_mutation(...)` unit tests to `_record_mutation`.
  - `test_verify_chain_detects_truncation_with_expected_count` and
    `test_verify_chain_detects_truncation_with_expected_head` — full list with a
    matching anchor is `True`; a truncated list is `False`.
  - `test_verify_chain_empty_is_true_without_anchor_and_false_with_expected_head`.

Gates:

```
uv run ruff check .
uv run pytest tests/test_config.py tests/test_confirmations.py tests/test_contracts.py tests/test_audit.py -q
uv run pytest -m "not integration and not e2e" -q
```

## 7. Acceptance criteria

- `src/config.py` no longer contains any literal credentials; the default URL
  parses with no username/password.
- A confirmations file longer-entry is filtered out and warned about; a file
  that appears after a failed load is picked up without a restart.
- A nested `required` violation reports the missing nested name (or the generic
  error message), never an empty trailing list.
- The session-taking audit helper is private and documents that it owns the
  commit; `verify_chain` detects a truncated list when given an anchor; all
  existing tests still pass.

## 8. Commits

Suggested messages (coordinator commits only after acceptance, RULES 0a):

1. `fix(config): drop embedded credentials from the database_url default`
2. `fix(core): filter unmatchable confirmation phrases and stop caching load failures`
3. `fix(core): report the failing subschema's required names`
4. `fix(core): make audit session ownership explicit and anchor-aware chain checks`

## 9. Risks

- Changing the confirmations cache to retry failures re-reads a broken path on
  every confirmation message (bounded: only while a plan is pending); the
  repeated warning is intentional operator feedback.
- `_record_mutation` is now private; the only production entry point remains
  `record_mutation_isolated`, and the two existing call sites are unchanged.
- `verify_chain` keeps its permissive default (no anchor ⇒ empty list is
  `True`); the module docstring is corrected so the limitation is explicit.
