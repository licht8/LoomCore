# TZ_RU.md — agent / auth / security-log hardening

> Task spec. Content is English (RULES 0). Scope is a review-finding batch.

## 1. Goal

Close eight correctness/security findings across three core modules:

- `src/core/agent.py`
  1. `execute_plan` silently truncates the step list at `max_steps`
     (`plan.get("steps", [])[:max_steps]`) yet still marks the plan `"done"` and
     returns success, so dropped steps are never executed or surfaced.
  2. The live step dict is handed to `emit` by reference and mutated afterwards
     (`status` -> running/done/failed), so a consumer that retains an event sees
     a snapshot that no longer matches what was delivered.
- `src/auth.py`
  3. `_revoked_loaded` is set before the store is read and never cleared, so the
     store is read at most once per process and a transient read failure disables
     revocation probing for the whole process lifetime.
  4. The revocation store has no synchronization: a deterministic temp name
     (`.{name}.tmp`) and a wholesale dict rebind on load can lose or corrupt
     concurrent updates.
  5. `verify_password` lets an absurd iteration count through `int(rounds_text)`;
     `pbkdf2_hmac` then raises `OverflowError`, escaping the narrow handler as a
     500 instead of returning `False`.
- `src/core/security_log.py`
  6. The event name is interpolated raw via `%s` (unlike `!r`-escaped fields), so
     a caller-supplied event string can inject forged log lines.
  7. `value!r` runs outside the logging call, so a raising/nondeterministic
     `__repr__` can turn an audit write into a 5xx.
  8. The docstring promises "Events never contain credential material" but
     nothing redacts known-sensitive keys.

## 2. Non-goals

- No change to the plan schema, event payload shape, WS payloads, `MAX_PLAN_STEPS`
  value, or `validate_plan` behavior (`validate_plan` already rejects plans with
  more than `MAX_PLAN_STEPS`; Part 1 is defense-in-depth for the `max_steps`
  override and any persisted/approved plan).
- No cross-process file locking (`fcntl`/`flock`); the store stays single-writer
  best-effort with merge-on-write (the deployment runs one `hub` process).
- No change to the password-hash cost (still 310_000 rounds) or JWT encoding.
- No new settings / env vars / DB migrations.
- No frontend changes.

## 3. Approved decisions (locked)

- **D1 — fail instead of truncate.** In `execute_plan`, read
  `all_steps = plan.get("steps", [])`; if `len(all_steps) > max_steps` set
  `plan["status"] = "failed"`, `await emit({"type": "plan_step", "phase":
  "failed", "step": dict(all_steps[0]), "error": f"plan has more than
  {max_steps} steps"})`, and return `{"plan": plan, "results": []}` **before**
  dispatching anything. Otherwise use `all_steps` (no slice needed once the guard
  passes). The failure event **must** carry `step`: the WS consumer
  (`src/interfaces/websocket.py:179`) reads `event["step"]` for every
  `plan_step`, so omitting it raises `KeyError` and turns the graceful failure
  into a "plan execution crashed" 500.
- **D2 — immutable events.** Every `emit(...)` in `execute_plan` that carries a
  step passes `dict(step)` (shallow copy). `plan["steps"]` keeps the live dicts
  and the returned `results` keep live references (only the emitted event is a
  snapshot).
- **D3 — reload on change.** Track the store signature as `(st_mtime_ns,
  st_size)`. Reload when `_revoked_loaded` is False **or** the signature differs
  from the last successfully loaded one. Set `_revoked_loaded` and
  `_revoked_signature` only **after** a successful parse. Missing/corrupt/
  unreadable files never latch, so they are retried on the next call. Capture the
  signature **before** reading so a mid-read change forces a later re-read.
- **D4 — lock + in-place merge + unique temp.** Add a module-level
  `threading.RLock`. `_load_revoked` must `_revoked.update(parsed)` (in place, no
  rebind). `_save_revoked` must first merge the on-disk file into `_revoked`,
  then serialize `_revoked` to a unique `tempfile.mkstemp` file in
  `path.parent` and `os.replace` it into place; clean up the temp file on
  failure; never raise to the caller on a write error.
- **D5 — atomic revoke sequence.** `revoke_jwt_token` holds the lock across
  load+mutate+save; the helpers keep acquiring the same `RLock` (re-entrant).
- **D6 — bounded rounds.** `rounds = int(rounds_text)`; return `False` when
  `rounds <= 0 or rounds > _MAX_PBKDF2_ROUNDS`, and catch `OverflowError` in
  addition to `(ValueError, TypeError)`. `_MAX_PBKDF2_ROUNDS = 10_000_000`
  (well above the 310_000 used by `generate_password_hash`).
- **D7 — sanitize + redact.** `log_security_event` renders the event as
  `str(event).replace("\r", "\\r").replace("\n", "\\n")`; renders each field with
  a guarded `repr` (fallback `"<unrepresentable>"`); and replaces the value of
  any field whose lowercased key contains a sensitive marker with `"***"`.
- **D8 — keep the guarantee.** The docstring claim stays; D7 makes it true.

## 4. Data model / interfaces

- `src/auth.py` new module state: `_revoked_signature: tuple[int, int] | None =
  None`, `_revoked_lock = threading.RLock()`, `_MAX_PBKDF2_ROUNDS = 10_000_000`.
- `src/auth.py` internal helpers (names are suggestions, behavior is locked):
  `_revoked_file_signature(path) -> tuple[int, int] | None` (stat; `None` on
  `OSError`), `_parse_revoked(data: object) -> dict[str, float]` (same filter as
  today: `str` keys, numeric non-bool values; a timestamp that cannot be
  converted to `float` — e.g. an absurdly large int raising `OverflowError` — is
  skipped entry-by-entry rather than aborting the whole parse), `_read_revoked_file(path) ->
  dict[str, float] | None` (`None` = missing/unreadable/corrupt).
- `src/core/security_log.py` new constants:
  `_SENSITIVE_MARKERS = ("password", "passwd", "secret", "token",
  "authorization", "api_key", "apikey", "cookie", "totp")` and `_REDACTED =
  "***"`.
- No API/DB/settings/event-shape changes.

## 5. Parts

1. `src/core/agent.py` — D1, D2.
   - Guard at the top of `execute_plan` (before the loop and before `approved`
     is used); include `"type": "plan_step"` in the failure event for contract
     consistency.
   - Change all four step-carrying `emit` calls (`started`, approval `failed`,
     dispatch `failed`, `done`) to pass `dict(step)`.
2. `src/auth.py` — D3, D4, D5, D6.
   - Add `import tempfile`, `import threading`, `import contextlib`.
   - Rework `_load_revoked` to the signature/reload design; never rebind
      `_revoked`.
   - Guard `_parse_revoked`'s `float(timestamp)` conversion per entry against
      `(TypeError, ValueError, OverflowError)` so a tampered store cannot abort
      loading (`OverflowError` is an `ArithmeticError`, not caught by the
      existing `(OSError, ValueError)`).
   - Rework `_save_revoked` to merge-on-disk + `mkstemp` + `os.replace`; wrap
     the whole body in `with _revoked_lock:`, and make `os.fdopen` the first
     statement inside the write `try` so a failure cannot leak the descriptor.
   - Wrap `revoke_jwt_token`'s mutation in `with _revoked_lock:`.
   - Update `_load_revoked`'s docstring (now "reload when the store changes").
3. `src/core/security_log.py` — D7, D8.
   - Build `parts` list manually; skip `None`; redact sensitive keys; guard
     `repr`; join with spaces; emit `logger.warning("%s %s", safe_event,
     rendered)` when non-empty else `logger.warning("%s", safe_event)`.

## 6. Tests

- `tests/test_agent.py`
  - `test_execute_plan_fails_when_steps_exceed_max`: a 2-step approved plan with
    `max_steps=1` -> `dispatch == []`, `result["results"] == []`,
    `result["plan"]["status"] == "failed"`, and a final `failed` event that
    contains a `step` key (`step["tool"] == "core_health"`; every `plan_step`
    event carries a step) and whose `error` contains `"more than 1 steps"`.
  - `test_execute_plan_emits_immutable_step_snapshots`: after a successful
    2-step run, the first `started` event's `step["status"]` is still `"running"`
    and `is not plan["steps"][0]`, while `plan["steps"][0]["status"] == "done"`.
- `tests/test_auth.py`
  - Extend the autouse `_isolated_revocation` fixture to also reset
    `auth._revoked_signature` to `None`.
  - `test_revocation_store_rereads_changed_file`: load a store with `{"a": 1}`,
    confirm token `a` rejected; rewrite the store with a different-length payload
    that also revokes `b`; confirm token `b` is now rejected (signature changed,
    so the store was re-read).
  - `test_revocation_load_failure_is_retried`: write invalid JSON to the store,
    confirm a token is still accepted (load failed, nothing latched); overwrite
    the store with valid JSON revoking that token's `jti`; confirm it is now
    rejected.
  - `test_revocation_store_survives_huge_timestamp`: a store whose JSON contains
    an absurdly large numeric timestamp (e.g. `{"a": 10**400}`) must not raise
    `OverflowError`; `_load_revoked`/`decode_jwt_token` for an unrelated token
    still work (the bad entry is skipped, not fatal).
  - `test_verify_password_rejects_absurd_rounds`: `rounds=0`, a huge rounds value
    (e.g. `10**20`), and a monkeypatched `hashlib.pbkdf2_hmac` that raises
    `OverflowError` all return `False`.
  - `test_concurrent_revocations_are_not_lost`: run several
    `revoke_jwt_token(...)` calls for distinct ids concurrently (`threading`)
    against a temp store; assert the persisted JSON contains every id and no
    `*.tmp` leftovers remain in the directory.
- `tests/test_security_logging.py`
  - `test_log_security_event_sanitizes_event_name`: event `"bad\ninjected\r"`
    yields a single-line message containing `"bad\\ninjected\\r"` and no raw
    `"\n"`/`"\r"`.
  - `test_log_security_event_survives_bad_repr`: a value whose `__repr__` raises
    logs `"<unrepresentable>"` and does not raise.
  - `test_log_security_event_redacts_sensitive_fields`: `password`, `token`,
    `secret`, `authorization` values are replaced with `"***"`, while `client`
    stays visible.

Gates (must pass verbatim):

```
uv run ruff check .
uv run pytest tests/test_agent.py tests/test_auth.py tests/test_security_logging.py -q
uv run pytest -m "not integration and not e2e" -q
```

## 7. Acceptance criteria

- An over-length plan fails loudly (status `failed`, one `failed` event with a
  `step` key, no dispatch) instead of reporting `done` with dropped steps, and the
  WS consumer never raises `KeyError` on it.
- Retaining an emitted `started` event no longer reflects later mutations, and
  the emitted step is a different object from the live plan step.
- Editing the revocation store on disk is picked up by a running process; a
  corrupt/missing store is retried rather than latched; concurrent revocations
  persist without losing entries or leaving temp files.
- A tampered store containing an unconvertible timestamp does not raise or abort
  JWT verification; the bad entry is skipped and the good entries still apply.
- `verify_password` returns `False` (never raises) for `rounds <= 0`, absurdly
  large rounds, and a `pbkdf2_hmac` `OverflowError`.
- A crafted event name cannot add newlines, a hostile `__repr__` cannot break the
  caller, and sensitive field values never appear in the rendered log line.
- All three gate commands pass.

## 8. Commits

Suggested (coordinator commits only after acceptance, RULES 0a):

```
fix(agent): fail plans that exceed the step limit and emit step snapshots
fix(auth): reload and lock the revocation store; bound pbkdf2 rounds
fix(core): sanitize and redact security-event log fields
```

## 9. Risks

- The reload check adds one `stat()` per JWT decode; acceptable for the auth
  path, and it is skipped entirely when no store is configured.
- `threading.RLock` serializes store I/O inside the event loop (already blocking
  file I/O today); writes are rare.
- mtime granularity is coarse on some filesystems; folding in `st_size` and
  capturing the signature before the read keeps changes detectable and, at
  worst, causes an extra re-read.
- The `mkstemp` -> `os.fdopen` window is not a fully leak-proof pattern; keeping
  `fdopen` first inside the `try` minimizes it (mirrors the recently fixed
  `validate_config_text`). A full guarantee would need `os.write`/`os.close`.
