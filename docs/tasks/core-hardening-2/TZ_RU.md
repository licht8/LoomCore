# TZ_RU.md — core hardening (round 2)

Task id: `core-hardening-2`. Follow RULES.md (English only, no comments unless
existing style has them, ruff clean, tests mandatory). Executor does NOT commit;
the coordinator commits after acceptance (RULES 0a). Five ordered parts covering
ten review clusters. Do the parts in order; each is independently reviewable.

Line numbers below are from HEAD `a0abe69` — re-locate before editing.

## 1. Goal

Close ten review clusters: fail-open auth, SSRF, event-loop blocking,
log-injection/redaction gaps, unsynchronized state, malformed-input contracts,
swallowed errors, durability/permissions, unbounded growth, and inconsistent API
response shapes.

## 2. Non-goals

- No new auth factors, no change to `AUTH_MODE` semantics beyond the fixes below.
- No external dependency additions (stdlib `ipaddress`/`socket`/`fcntl` only).
- No DB schema change, no new migration.
- No frontend changes (response-shape unification is backend-only; the current
  frontend reads `detail`).
- No DNS-rebinding-complete SSRF solution (documented limitation, D2.1).

## 3. Approved decisions (locked)

### Part 1 — Auth fail-open (cluster 1)

**D1.1 — `verify_totp` must reject an empty/blank secret.**
`src/auth.py:171-184`: `base64.b32decode("")` returns `b""`, so a code computed
from the empty key verifies. Add an early guard:
```python
if not secret or not secret.strip():
    return False
```
Also reject secrets that fail `b32decode` (already caught) and codes that are not
6 digits. Add tests.

**D1.2 — `/api/auth/session` cookie branch must honor the active mode.**
`src/api/auth.py:125-127` calls `decode_jwt_token` directly, bypassing
`auth_mode()`. Replace the whole body with mode-aware validation so both
credentials go through one gate:
```python
cookie = request.cookies.get(SESSION_COOKIE)
if is_valid_admin_credential(cookie):
    return {"authenticated": True}
if authorization and authorization.lower().startswith("bearer "):
    require_admin_token(extract_admin_token(request, authorization), request)
    return {"authenticated": True}
raise HTTPException(status_code=401, detail="Not authenticated")
```
`is_valid_admin_credential` (`src/core/security.py:129-141`) is already
mode-gated, so a mode-disabled JWT now yields 401/403 consistently with every
other endpoint (docstring at `auth.py:116-123` becomes true).

**D1.3 — `extract_admin_token` must read the request header; Bearer is
case-insensitive.**
`src/api/deps.py:15-39`: the header is only read from the explicit argument
(default `None`), so callers that omit it silently lose Bearer auth. Change to
fall back to `request.headers.get("authorization")` when the argument is `None`,
and match the scheme case-insensitively:
```python
raw = authorization if authorization is not None else request.headers.get("authorization")
header = None
if raw and raw.lower().startswith("bearer "):
    header = raw.split(" ", 1)[1].strip()
```
Keep the existing candidate priority (valid cookie first, then header; raw
fallback `cookie or header`). Apply the same case-insensitive check in
`auth_session`.

**D1.4 — `/api/artifacts/{name}/{kind}` must pass the Authorization header.**
`src/api/artifacts.py:48-55`: add `authorization: str | None = Header(default=None)`
and call `require_admin_token_from_request(request, authorization)` (currently it
passes one arg, so static-token clients can never reach the endpoint). Add a test
that a valid `Authorization: Bearer <token>` is accepted.

**D1.5 — PBKDF2 off the event loop.**
`src/api/auth.py:69-72`: `verify_password` (310k rounds, up to 10M) runs
synchronously in `async def login`. Wrap it:
```python
password_ok = await asyncio.to_thread(
    verify_password, str(data.get("password", "")),
    settings.admin_password_hash or _DUMMY_PASSWORD_HASH,
)
```
Do the same for any other synchronous credential verification in an async
handler. `_DUMMY_PASSWORD_HASH` is still computed at import (intentional
constant-work) — leave it.

**D1.6 — warn on a JWT mode that can never authenticate.**
`AUTH_MODE=jwt`/`both` with `SESSION_SECRET` but no `admin_password_hash` +
`admin_totp_secret` passes config validation yet always 503s
(`src/core/security.py:58,97-105`). Add a `check_security_posture()` warning
(non-fatal) in `src/core/startup_checks.py` for that combination.

### Part 2 — SSRF + LLM input contracts (clusters 2, 6-llm, 5-llm)

**D2.1 — provider-URL guard (new `src/llm/security.py`).**
```python
def resolve_host(host: str) -> list[str]      # socket.getaddrinfo; test seam
def validate_provider_url(url: str, *, resolve: bool) -> None  # raises ValueError
```
Rules:
- scheme must be `http`/`https` and a host must be present;
- an IP-literal host must be global (reject `ipaddress.ip_address(h).is_private`
  / `is_loopback` / `is_link_local` / `is_reserved` / `is_multicast` /
  `is_unspecified`);
- a hostname equal to `localhost`, ending in `.local`/`.internal`, or in a
  metadata denylist (`metadata.google.internal`, `metadata.goog`) is rejected;
- when `resolve=True`, resolve via `resolve_host` and reject if **any** address is
  non-global.
Documented limitation: this blocks literal/known-internal targets and resolved
private ranges; it does not defeat DNS rebinding between check and connect.

Apply:
- `PUT /api/llm/settings` (`src/api/llm.py:92-99`): replace the scheme-only check
  with `validate_provider_url(base_url, resolve=False)` (no DNS at config time).
- `POST /api/llm/test` (`src/api/llm.py:221-233`): `validate_provider_url(..., resolve=True)`
  before the GET → 400 on rejection.
- `OpenAICompatClient` (`src/llm/client.py`, before `client.post`): validate the
  resolved `config.base_url` with `resolve=True`; raise `LlmError` on rejection.

**D2.2 — do not follow redirects; treat 3xx as not-OK in `/llm/test`.**
Both `httpx.AsyncClient(...)` constructions pass `follow_redirects=False`
explicitly (httpx default, but pin it). In `/llm/test`, a `300 <= status < 400`
returns `{"ok": False, "status": status, "error": "redirect not allowed"}`
instead of the current false `{"ok": True}`.

**D2.3 — client rejects a non-object JSON body.**
`src/llm/client.py:170-174`: after `response.json()`, require a dict:
```python
if not isinstance(data, dict):
    raise LlmError("LLM returned a non-object JSON body")
```
so `[]`/`"x"`/`123`/`null` become `LlmError` instead of `AttributeError` past the
`-> dict` promise. Add tests for list/str/int/null.

**D2.4 — reject NaN/Infinity/negative numerics.**
- `LlmSettingsUpdate` (`src/api/llm.py:34-48`): timeouts
  `Field(default=None, gt=0, allow_inf_nan=False)`, `max_retries: int | None =
  Field(default=None, ge=0)`.
- `src/llm/config.py::_as_float` (`:97-102`): drop non-finite values (`math.isfinite`)
  back to the default.
- Keep the existing `_merge` checks (defense in depth) and the
  `read_timeout: 0 -> 400` behavior.

**D2.5 — PUT returns the value it saved; serialize the read-modify-write.**
`src/api/llm.py:197-212`: return `_serialize(updated)` (not a second
`get_active()` after the awaited audit). Guard the whole read-modify-write with a
module-level `asyncio.Lock` so concurrent PUTs cannot lose updates. Audit
semantics are unchanged (`status="error"` still recorded before the 500).

**D2.6 — stop logging raw user text.**
`src/llm/router.py:215-219` logs `user_message[:100]`. Replace with counts only
(e.g. `history_len`, `tools_count`, `message_chars=len(user_message)`). No message
content at any level.

**D2.7 — synchronize the active-config globals.**
`src/llm/config.py:186-229`: wrap `get_active`/`set_active`/`clear_cache` bodies in
a module-level `threading.RLock` (mirroring `src/auth.py`) so the stat-then-load
and write-then-assign sequences cannot interleave. Keep the mtime/size signature
and existing test semantics.

### Part 3 — Async blocking + plugin-manager correctness (clusters 3, 5, 6, 7, 9)

**D3.1 — offload blocking startup work.**
`src/app.py:64,66`: run `check_security_posture()` and
`plugin_manager.discover_plugins(...)` via `await asyncio.to_thread(...)`.

**D3.2 — offload blocking process waits.**
`src/plugins/manager.py:544-551` (`terminate` + `wait(timeout=5)` + `kill`) and
`_kill_and_stop` (`:1047-1064`) block the loop. In async callers
(`_restart_plugin`, `_exchange`, `_write_request`, `disable_plugin`,
`reload_plugin`) invoke the wait via `asyncio.to_thread` (e.g. an
`await asyncio.to_thread(self._kill_and_stop, name)` wrapper and a
`_terminate_process(proc)` helper run in a thread). Keep the synchronous
`_kill_and_stop` method itself so existing monkeypatches still fire.

**D3.3 — serialize `_restart_plugin` under `plugin.lock`.**
`src/plugins/manager.py:502-581` mutates `plugin.proc`/`plugin.state` with no
lock. Wrap the body in `async with plugin.lock:` (the reentrant `_PluginLock`
already allows the `_write_request`-initiated path that holds it).

**D3.4 — skip non-JSON stdout lines.**
`src/plugins/manager.py:842-858`: `json.loads(line)` has no guard, so a plugin
log line on stdout raises out of the worker thread and is not caught. Wrap in
`try/except json.JSONDecodeError: continue` (optionally log at debug).

**D3.5 — `"result": null` is "unavailable", not `AttributeError`.**
`src/plugins/manager.py:884-886`: `response.get("result", {})` returns `None` for
an explicit null. Use:
```python
result = response.get("result")
if not isinstance(result, dict):
    raise RuntimeError(f"Plugin '{plugin_name}' returned no result")
return result.get("tools", [])
```

**D3.6 — reset `restart_attempt` on a successful restart.**
`src/plugins/manager.py:565-567`: add `plugin.restart_attempt = 0` before
`return True`, so a plugin that recovers is not permanently `STOPPED` after
`max_restarts` lifetime failures. **Update**
`tests/test_plugin_manager.py::test_restart_success` (currently asserts
`restart_attempt == 1`).

**D3.7 — health loop must not stall.**
`src/plugins/health.py:22-29`: wrap each `restart_plugin` in
`asyncio.wait_for(..., timeout=...)` (e.g. 60s) and catch
`asyncio.TimeoutError` per plugin, so one slow restart cannot block the others or
overrun the 30s cadence.

**D3.8 — thread-safe lazy plugin-manager singleton.**
`src/plugins/__init__.py`: `lru_cache` does not guarantee single execution and
pins resolved paths. Guard construction with a `threading.Lock` and keep
`get_plugin_manager()`/`__getattr__` behavior.

**D3.9 — `ensure_session` session branch must narrow the catch.**
`src/db/session.py:139`: the session branch uses bare `except Exception`, unlike
the user branch which catches `IntegrityError`. Catch `IntegrityError` only, roll
back, re-read, and re-raise the original when the row is absent — matching the
user branch (`:106-120`). Add a test that a non-`IntegrityError` failure on the
session insert propagates.

**D3.10 — do not silently drop audit failures.**
`src/core/audit.py:142-158`: escalate the "mutation not audited" `logger.warning`
to `logger.error` (include the tool name) while still returning `None`. Remove
the `contextlib.suppress(Exception)` wrappers around audit calls in
`src/interfaces/websocket.py:166` and `src/api/plugins.py:61-70` so a failure is
logged, not swallowed.

**D3.11 — do not forward raw exception text to WebSocket clients.**
`src/interfaces/websocket.py:446-450,261-262,770-777`: replace `str(exc)` /
`f"{type(exc).__name__}: {exc}"` with a small `_safe_error_message(exc)` that
returns a category only (`LlmError` → "The language model request failed.",
timeouts → "The language model timed out.", else "Internal error.") and logs the
real exception server-side. Update the affected tests.

### Part 4 — Logging hygiene + response shapes (clusters 4, 10)

**D4.1 — sanitize keys, not just values.**
`src/core/security_log.py::_render_field` (`:32-39`): neutralize CR/LF/`=` in the
**key** (same escaping as the event name), and redact recursively: when a value is
a `dict`, redact any nested key matching `_SENSITIVE_MARKERS` before `repr()`
(top-level key-only redaction currently leaks nested credentials). Keep `client`
visible and `"***"` present.

**D4.2 — sanitize timing stage name and keys.**
`src/core/timing.py::log_stage`/`span` (`:23-47`): apply the CR/LF→space
sanitization to the stage `name` and to each key, preserving the exact
`key=value` shape asserted by `tests/test_timing.py::test_log_stage_sanitizes_newlines`.

**D4.3 — validate `session_id` on the history routes.**
`src/api/sessions.py:28-53`: the path param is a bare `str` (unlike
`src/api/chat.py:26-30`). Constrain it (`max_length=64`,
`pattern=r"^[A-Za-z0-9_-]{1,64}$"`) so a percent-decoded newline can never reach
`logger.info` at `:52`. Keep the handlers callable directly with `request=None`
and `"sess"` valid.

**D4.4 — unify error bodies on `{"detail": ...}`.**
- `src/api/chat.py:48-55`: the `504`/`500` responses become
  `{"detail": ...}` (the `429` already is).
- `src/api/artifacts.py:57,76-79`: return `{"detail": ...}`.
- `src/api/plugins.py:85-86,102-103,119-120`: see D4.5.
Update the corresponding tests.

**D4.5 — distinguish policy denial from start failure in the plugin API.**
Add `src/plugins/errors.py::PluginPolicyDenied(Exception)`. `enable_plugin` raises
it on the allowlist-denial branch (`src/plugins/manager.py:402-414`) instead of
returning `False`; spawn/init failure still returns `False`. `src/api/plugins.py`:
- `enable`: `except PluginPolicyDenied` → `403 {"detail": "Plugin '<id>' is not permitted by policy"}`; `not enabled` → `502 {"detail": "Failed to start '<id>'"}`.
- `reload`: `not reloaded` → `409 {"detail": "Plugin '<id>' is disabled or failed to reload"}`.
- `disable`: keep the (unreachable) not-found guard as `404`.
Audit still records the outcome. **Update**
`tests/test_plugin_manager.py::test_enable_refuses_plugin_not_in_allowlist`
(expects `is False`).

**D4.6 — artifacts must tolerate a non-dict plugin result.**
`src/api/artifacts.py:58-74`: `execute_internal_tool` can return any JSON type.
Guard `isinstance(result, dict)` and `isinstance(data, dict)` before indexing, and
include the resulting error in the `except (KeyError, ValueError, OSError, RuntimeError, TypeError)` tuple so a malformed result is a 404, never an
unhandled 500.

### Part 5 — Durability / permissions / pruning (clusters 8, 9)

**D5.1 — `_atomic_write_text` must be durable, mode-preserving, and leak-free.**
`src/plugins/state.py:78-89`: (a) `handle.flush(); os.fsync(handle.fileno())`
before `os.replace`; (b) fsync the parent directory after replace; (c) close the
`mkstemp` fd if `os.fdopen` raises; (d) preserve an existing destination's mode
(`os.stat(path).st_mode` → `os.fchmod(fd, mode)`) and default to `0600` only when
creating. Keep serialization identical (tests assert exact bytes).

**D5.2 — a zero-byte revocation store must not read as corrupt.**
`src/auth.py::_read_revoked_file` (`:79-88`): treat empty/whitespace content as
`{}` (successful parse) so it latches the signature and stops the per-decode
warning. Additionally, `src/core/startup_checks.py:53-56` should write `{}` (not
leave 0 bytes) when creating the store, so `test_secure_posture_is_silent`
(`assert store.exists()`) still holds.

**D5.3 — prune expired revocations.**
`src/auth.py`: entries store only a revoke timestamp; nothing ever removes them.
Add a retention (`_REVOKE_RETENTION_SECONDS`, e.g. 86400 = 2× the 8 h token
lifetime) and prune `_revoked` in `_load_revoked`/`_save_revoked`:
`{jti: ts for jti, ts in _revoked.items() if now - ts <= retention}`. Keep failed
parses unlatched (`tests/test_auth.py::test_revocation_load_failure_is_retried`)
and keep `test_revocation_store_rereads_changed_file` green.

**D5.4 — cross-process revocation writes must not lose updates.**
`src/auth.py::_save_revoked` (`:115-152`) is read-merge-write with only a
thread lock. Wrap the merge+write in a best-effort OS file lock
(`fcntl.flock` on the store when available; no-op on platforms without `fcntl`)
using a `.lock` sibling, so two processes cannot clobber each other. Add a unit
test for the lock context manager's no-op path.

**D5.5 — evict idle WebSocket identity windows.**
`src/interfaces/websocket.py:83-98`: `_ws_message_times` keys are never removed.
Delete a key when its deque becomes empty, and add
`release_ws_identity(identity)` called from the endpoint's `finally` so a closed
socket's entry is dropped. Keep `reset_ws_message_limits` and the existing
per-identity window behavior.

## 4. Interfaces / behavior impact

- Auth: a mode-disabled credential now yields 401/403 from `/api/auth/session`
  (was 200); `Authorization` is honored by artifacts and read from the header
  when callers omit the argument; `verify_totp("", "")` is `False`.
- LLM: private/loopback/link-local provider URLs are rejected; redirects are not
  followed; non-object JSON and NaN/Inf are rejected; PUT echoes the saved config.
- Plugins: policy denial is `403`, start failure `502`, reload conflict `409`.
- Errors: all JSON error bodies use `detail`.
- Files: plugin state writes are fsynced and keep their mode; the revoked store is
  pruned and its empty file is not "corrupt".

## 5. Parts (ordered)

1. Auth fail-open — `src/auth.py`, `src/api/auth.py`, `src/api/deps.py`,
   `src/api/artifacts.py` (auth part), `src/core/startup_checks.py` (D1.6).
2. SSRF + LLM contracts — `src/llm/security.py` (new), `src/api/llm.py`,
   `src/llm/client.py`, `src/llm/config.py`, `src/llm/router.py`.
3. Async + plugin manager — `src/app.py`, `src/api/auth.py` (D1.5),
   `src/plugins/manager.py`, `src/plugins/health.py`, `src/plugins/__init__.py`,
   `src/plugins/errors.py` (new), `src/db/session.py`, `src/core/audit.py`,
   `src/interfaces/websocket.py`.
4. Logging + response shapes — `src/core/security_log.py`, `src/core/timing.py`,
   `src/api/sessions.py`, `src/api/chat.py`, `src/api/artifacts.py`,
   `src/api/plugins.py`.
5. Durability/pruning — `src/plugins/state.py`, `src/auth.py`,
   `src/core/startup_checks.py`, `src/interfaces/websocket.py`.

## 6. Tests

New/extended (fast suite only, no DB/WireGuard):

- `tests/test_auth.py`: empty/blank TOTP secret rejected; 6-digit enforcement;
  revocation pruning keeps fresh entries and drops old ones.
- `tests/test_api_auth.py`: `test_session_accepts_valid_cookie` must set
  password+TOTP (or assert 401 in token mode); add a mode-disabled-cookie → 401
  case; case-insensitive `bearer`.
- `tests/test_api_deps.py`: header read from `request.headers` when the argument
  is omitted; case-insensitive Bearer.
- `tests/test_artifacts.py`: valid `Authorization: Bearer` accepted; non-dict
  plugin result → 404 with `detail`.
- New `tests/test_llm_security.py`: `validate_provider_url` for
  loopback/link-local/private/metadata/IP-literal/hostname/`resolve` cases (stub
  `resolve_host`).
- `tests/test_api_llm.py`: private base_url → 400 on PUT and `/test`; 3xx →
  `ok: false`; NaN/Inf → 422; PUT returns the saved config; add an autouse
  `resolve_host` stub so no test does live DNS.
- `tests/test_llm_client.py`: non-object JSON → `LlmError`; autouse `resolve_host`
  stub; existing 15 tests stay green.
- `tests/test_llm_config.py`: NaN/Inf in the file are ignored; concurrent
  `set_active`/`get_active` do not corrupt state.
- `tests/test_plugin_manager.py`: non-JSON stdout skipped; `"result": null` →
  unavailable; restart resets `restart_attempt` (update `test_restart_success`);
  policy denial raises `PluginPolicyDenied` (update
  `test_enable_refuses_plugin_not_in_allowlist`); `_restart_plugin` takes the lock.
- `tests/test_plugins_health.py`: a hanging restart is timed out and other plugins
  still checked.
- `tests/test_db_session.py`: non-`IntegrityError` session insert propagates.
- `tests/test_audit.py`: audit failure logs at ERROR (still returns `None`).
- `tests/test_security_logging.py`: keys with CR/LF sanitized; nested secret
  redacted.
- `tests/test_timing.py`: stage name/key CR/LF sanitized, `tool=a b` preserved.
- `tests/test_history_api.py`: invalid `session_id` rejected (still accepts
  `"sess"` via direct call).
- `tests/test_chat_api.py`: 504/500 use `detail`.
- `tests/test_api_plugins.py`: policy denial → 403, start failure → 502, reload →
  409.
- `tests/test_websocket.py`: raw exception text not forwarded; identity window
  evicted on disconnect.
- `tests/test_plugin_state.py`: mode preserved, fsync path (monkeypatch), fd
  closed on `fdopen` failure.
- `tests/test_startup_checks.py`: store created as `{}` (not 0 bytes) and no
  warning; JWT-mode-without-credentials warns.

Gates:
```
ruff check .
pytest -m "not integration and not e2e"
cd frontend; npx tsc --noEmit      # only if frontend is touched (it should not be)
```

## 7. Acceptance criteria

- No fail-open auth path: an empty TOTP secret never verifies; a mode-disabled
  cookie is rejected by `/api/auth/session`; artifacts honor Bearer; the header
  is read even when the caller omits the argument; Bearer is case-insensitive.
- `/llm/test` and the client refuse private/loopback/link-local/metadata URLs and
  do not follow redirects; non-object JSON and NaN/Inf are rejected; PUT returns
  what it saved under concurrency.
- No blocking call remains on the event loop in the paths named in cluster 3;
  a healthy-but-previously-restarted plugin is not permanently `STOPPED`; a
  non-JSON plugin line or null result yields the intended error.
- Audit failures are logged at ERROR and not suppressed; WebSocket clients never
  receive raw internal exception text.
- Log keys/names are sanitized and nested secrets redacted; every JSON error body
  uses `detail`; policy denial is distinguishable from start failure.
- Plugin state writes are fsynced and mode-preserving with no fd leak; the
  revocation store is pruned, its empty file is not corrupt, and writes are
  cross-process safe (best effort).
- `ruff check .` clean; fast suite green.

## 8. Commits (coordinator commits only after acceptance, RULES 0a)

1. `fix(auth): close fail-open credential paths and offload PBKDF2`
2. `fix(llm): guard provider URLs and harden config/client input`
3. `fix(plugins): serialize lifecycle, surface audit failures, bound blocking calls`
4. `fix(api): sanitize logs and unify error response shapes`
5. `fix(persistence): make state writes durable and prune unbounded stores`

## 9. Risks

- **Behavior changes** in `/api/auth/session` and error bodies break existing
  tests; the spec lists each test to update.
- **SSRF resolution** can add latency and must be stubbed in tests to avoid live
  DNS; rebinding remains out of scope.
- **`plugin.lock` around `_restart_plugin`** must stay reentrant so the
  `_write_request` restart path does not deadlock — `_PluginLock` already tracks
  the owning task.
- **`asyncio.to_thread` on `_kill_and_stop`** keeps the monkeypatch seam but the
  call is now executed off-loop; tests that count calls still see one.
- **File locking** uses `fcntl` (POSIX); Windows dev/tests take the no-op path.
- **Pruning revocations** must retain entries younger than the retention window
  and keep failed parses unlatched.
