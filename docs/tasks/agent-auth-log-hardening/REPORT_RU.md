# REPORT_RU.md — agent / auth / security-log hardening

Task spec: `docs/tasks/agent-auth-log-hardening/TZ_RU.md`.

## Goal

Close eight correctness/security findings across three core modules:

1. `src/core/agent.py` silently truncated the step list at `max_steps` yet still
   marked the plan `"done"`, so dropped steps were never executed or surfaced.
2. `execute_plan` handed the live step dict to `emit` and mutated it afterwards,
   so a retained event no longer matched what was delivered.
3. `src/auth.py` latched `_revoked_loaded` before reading the store and never
   cleared it, so the store was read at most once per process and a transient
   failure disabled revocation probing for the process lifetime.
4. The revocation store had no synchronization: a deterministic temp name and a
   wholesale dict rebind could lose or corrupt concurrent updates.
5. `verify_password` let an absurd iteration count reach `pbkdf2_hmac`, whose
   `OverflowError` escaped the handler as a 500 instead of returning `False`.
6. `src/core/security_log.py` interpolated the event name raw via `%s`, allowing
   newline injection into forged log lines.
7. `value!r` ran outside the logging call, so a raising `__repr__` could turn an
   audit write into a 5xx.
8. The docstring promised credential-free events but nothing redacted
   known-sensitive keys.

## Delivered

- `src/core/agent.py` (`execute_plan`):
  - New guard: `all_steps = plan.get("steps", [])`; when
    `len(all_steps) > max_steps` the plan is set to `failed`, one
    `{"type": "plan_step", "phase": "failed", "step": dict(all_steps[0]),
    "error": f"plan has more than {max_steps} steps"}` event is emitted, and
    `{"plan": plan, "results": []}` is returned before any dispatch (the
    `[:max_steps]` slice is gone). The `step` key is required because
    `src/interfaces/websocket.py` reads `event["step"]` for every `plan_step`.
  - All four step-carrying emits (`started`, approval `failed`, dispatch
    `failed`, `done`) now pass `dict(step)`; `plan["steps"]` and the returned
    `results` keep the live dicts.
- `src/auth.py`:
  - New imports `tempfile`, `threading`, `contextlib`; new module state
    `_revoked_signature: tuple[int, int] | None = None`,
    `_revoked_lock = threading.RLock()`, `_MAX_PBKDF2_ROUNDS = 10_000_000`.
  - New helpers `_revoked_file_signature(path)`, `_parse_revoked(data)`,
    `_read_revoked_file(path)` (the latter returns `None` for
    missing/unreadable/corrupt). `_parse_revoked` builds its mapping with an
    explicit loop and skips any entry whose `float(timestamp)` raises
    `(TypeError, ValueError, OverflowError)`, so one tampered (huge) timestamp no
    longer aborts the whole load.
  - `_load_revoked` reloads when the store is unloaded **or** its
    `(st_mtime_ns, st_size)` signature changed; the signature is captured before
    the read and the state is latched only after a successful parse; the parsed
    data is merged in place (`_revoked.update(parsed)`, no rebind). It now takes
    `_revoked_lock`.
  - `_save_revoked` merges the on-disk file first, then writes `_revoked` to a
    unique `tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.",
    suffix=".tmp")` file and `os.replace`s it into place; `os.fdopen` is the first
    statement of the write `try`, a failed `os.fdopen` closes the raw descriptor,
    and a `finally` always unlinks the staging file (so a non-`OSError` escape
    such as `KeyboardInterrupt` cannot leave a temp file behind); write errors
    never escape. It takes `_revoked_lock`.
  - `revoke_jwt_token` holds `_revoked_lock` across load + mutate + save.
  - `verify_password` bounds `rounds` (`<= 0` or `> _MAX_PBKDF2_ROUNDS` -> False)
    and catches `OverflowError` alongside `(ValueError, TypeError)`.
- `src/core/security_log.py`:
  - New `_SENSITIVE_MARKERS` tuple and `_REDACTED = "***"`, plus a private
    `_render_field(key, value)` that redacts sensitive keys and guards `repr`
    (fallback `"<unrepresentable>"`).
  - `log_security_event` now renders the event as
    `str(event).replace("\r", "\\r").replace("\n", "\\n")`, builds the field
    list manually (skipping `None`, sorted), joins with spaces, and emits
    `logger.warning("%s %s", safe_event, " ".join(parts))` (or the event alone).
  - Docstring updated to state the newline-escape/redaction guarantee.

## Verification

- `uv run ruff check .` — clean (`All checks passed!`).
- `uv run pytest tests/test_agent.py tests/test_auth.py
  tests/test_security_logging.py -q` — 48 passed (log:
  `tmp/tests_agent_auth_log_targeted.log`).
- `uv run pytest -m "not integration and not e2e" -q` — 945 passed, 11 skipped,
  5 deselected (log: `tmp/tests_agent_auth_log_full.log`). Previous suite size
  was 934; the eleven new tests account for the delta (2 agent + 6 auth + 3 log).
- Integration tests not run locally (no Postgres).

## Changed files

- `src/core/agent.py` — `max_steps` fail-guard + immutable step snapshots.
- `src/auth.py` — signature-based reload, `RLock`, in-place merge, unique temp +
  `os.replace` with always-clean staging file, bounded/Overflow-safe
  `verify_password`.
- `src/core/security_log.py` — event sanitization, guarded `repr`, redaction.
- `tests/test_agent.py`, `tests/test_auth.py`, `tests/test_security_logging.py` —
  new/updated tests.
- `docs/tasks/agent-auth-log-hardening/REPORT_RU.md` — this report.

## Notes and follow-ups

- New tests: `test_execute_plan_fails_when_steps_exceed_max`,
  `test_execute_plan_emits_immutable_step_snapshots` (agent);
  `test_revocation_store_rereads_changed_file`,
  `test_revocation_load_failure_is_retried`,
  `test_verify_password_rejects_absurd_rounds`,
  `test_revocation_store_survives_huge_timestamp`,
  `test_concurrent_revocations_are_not_lost`,
  `test_save_revoked_cleans_temp_on_unexpected_error` (auth);
  `test_log_security_event_sanitizes_event_name`,
  `test_log_security_event_survives_bad_repr`,
  `test_log_security_event_redacts_sensitive_fields` (security logging).
- `tests/test_auth.py`'s autouse `_isolated_revocation` fixture now also resets
  `auth._revoked_signature` to `None`.
- Review fix round: the over-length `failed` event now carries
  `"step": dict(all_steps[0])` (the WS consumer reads `event["step"]`
  unconditionally), and `_parse_revoked` no longer aborts on an unconvertible
  timestamp.
- Review fix round 2: `_save_revoked` now cleans the staging file in a
  `finally` (covers non-`OSError` escapes) and closes the raw descriptor when
  `os.fdopen` itself fails, so no `.<name>.*.tmp` file or descriptor can leak.
- The `mkstemp` -> `os.fdopen` window is minimized but not a full leak guarantee
  (mirrors the earlier `validate_config_text` fix); a full guarantee would need
  `os.write`/`os.close` (documented in the spec risks).
- Redaction is key-based (substring of the lowercased key), so it applies to
  `password`, `token`, `secret`, `authorization`, `cookie`, `totp`, etc.,
  including nested-style names like `api_key`.
- No change to the plan schema, event payload shape, `MAX_PLAN_STEPS`,
  `validate_plan`, the password-hash cost, JWT encoding, settings/env, or DB
  schema; no new dependencies.
- Suggested commits (coordinator commits after acceptance — RULES 0a):
  1. `fix(agent): fail plans that exceed the step limit and emit step snapshots`
  2. `fix(auth): reload and lock the revocation store; bound pbkdf2 rounds`
  3. `fix(core): sanitize and redact security-event log fields`

Commits created: none
