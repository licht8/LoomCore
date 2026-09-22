# REPORT_RU.md — Core hardening (round 2)

Task spec: `docs/tasks/core-hardening-2/TZ_RU.md` (task id `core-hardening-2`).
Executor does not commit; the coordinator commits after acceptance (RULES 0a).

## Goal

Close ten review clusters across the core: fail-open auth, SSRF, event-loop
blocking, log-injection/redaction gaps, unsynchronized state, malformed-input
contracts, swallowed errors, durability/permissions, unbounded growth, and
inconsistent API response shapes.

No new dependencies (stdlib `ipaddress`/`socket`/`fcntl` only), no DB schema or
migration, no frontend changes.

## Delivered

### Part 1 — Auth fail-open (D1.1-D1.6)

- **D1.1** `src/auth.py::verify_totp` rejects an empty/blank secret and any code
  that is not exactly six digits (after stripping whitespace) before decoding.
- **D1.2** `src/api/auth.py::auth_session` now validates through
  `is_valid_admin_credential` (mode-gated) for the cookie branch and
  `require_admin_token` for the Bearer branch, so a mode-disabled JWT cookie is
  rejected with 401/403 instead of authenticating.
- **D1.3** `src/api/deps.py::extract_admin_token` falls back to
  `request.headers.get("authorization")` when the argument is absent, and matches
  the `Bearer` scheme case-insensitively. `auth_session` uses the same
  case-insensitive check.
- **D1.4** `src/api/artifacts.py::download_artifact` accepts
  `authorization: str | None = Header(default=None)` and passes it to
  `require_admin_token_from_request`, so static-token clients reach it.
- **D1.5** `src/api/auth.py::login` runs the 310k–10M-round PBKDF2 check via
  `await asyncio.to_thread(verify_password, ...)`; `_DUMMY_PASSWORD_HASH` is still
  computed at import.
- **D1.6** `src/core/startup_checks.py` adds `_check_jwt_mode_credentials()`,
  warning (non-fatal) when `AUTH_MODE` is `jwt`/`both` with `SESSION_SECRET` set
  but no password hash + TOTP secret — a configuration that can never
  authenticate.

### Part 2 — SSRF + LLM input contracts (D2.1-D2.7)

- **D2.1** new `src/llm/security.py`: `resolve_host(host)` (test seam over
  `socket.getaddrinfo`) and `validate_provider_url(url, *, resolve)` rejecting
  non-http(s) schemes, missing hosts, non-global IP literals
  (private/loopback/link-local/reserved/multicast/unspecified), `localhost`,
  `*.local`/`*.internal` hosts and the metadata denylist; with `resolve=True`
  every resolved address must be global. The module docstring documents the
  DNS-rebinding limitation (out of scope).
  Applied at `PUT /api/llm/settings` (`resolve=False`), `POST /api/llm/test`
  (`resolve=True`) and `OpenAICompatClient.chat_completion` (raises `LlmError`).
- **D2.2** both `httpx.AsyncClient(...)` constructions pin
  `follow_redirects=False`; `/llm/test` returns
  `{"ok": False, "status": <3xx>, "error": "redirect not allowed"}` for `3xx`
  instead of a false `ok: True`.
- **D2.3** `src/llm/client.py` raises `LlmError("LLM returned a non-object JSON
  body")` for `[]`/`"x"`/`123`/`null`.
- **D2.4** `LlmSettingsUpdate` timeouts use `Field(default=None,
  allow_inf_nan=False)` and `max_retries` uses `Field(default=None, ge=0)`;
  `src/llm/config.py::_as_float` drops non-finite values back to the default.
- **D2.5** `PUT /api/llm/settings` returns `_serialize(updated)` (the value it
  saved) and the read-modify-write runs under a serializing lock.
- **D2.6** `src/llm/router.py` logs counts only (`history_len`, `tools_count`,
  `message_chars`) — never the user message text.
- **D2.7** `src/llm/config.py` guards `clear_cache`/`get_active`/`set_active`
  with a module-level `threading.RLock` (mtime/size signature unchanged).

### Part 3 — Async blocking + plugin manager (D3.1-D3.11)

- **D3.1** `src/app.py` runs `check_security_posture()` and
  `discover_plugins(...)` via `asyncio.to_thread`.
- **D3.2** blocking process waits (`terminate`/`wait`/`kill`) moved off the loop
  via `asyncio.to_thread` in `disable_plugin`, `enable_plugin`, `reload_plugin`,
  `_exchange`, `_restart_plugin`; a new sync helper `_terminate_process` keeps the
  synchronous `_kill_and_stop` (monkeypatch seam) intact.
- **D3.3** `_restart_plugin` mutates the plugin under `async with plugin.lock:`
  (reentrant `_PluginLock`, so the `_write_request` restart path cannot deadlock).
- **D3.4** `_read_response` skips non-JSON stdout lines
  (`json.JSONDecodeError`) and non-dict JSON, logging at debug.
- **D3.5** `get_tools` treats an explicit `"result": null` as unavailable
  (`RuntimeError("... returned no result")`) instead of raising `AttributeError`.
- **D3.6** `_restart_plugin` resets `plugin.restart_attempt = 0` on success, so a
  recovered plugin is not permanently `STOPPED`.
- **D3.7** `src/plugins/health.py` wraps each restart in
  `asyncio.wait_for(..., timeout=RESTART_TIMEOUT_SECONDS)` (60 s) with a
  per-plugin `TimeoutError` catch.
- **D3.8** `src/plugins/__init__.py` uses an explicit `threading.Lock`-guarded
  singleton instead of `lru_cache`; `cache_clear` is preserved as an attribute.
- **D3.9** `src/db/session.py::ensure_session` session branch catches only
  `IntegrityError` (matching the user branch) and re-raises other failures.
- **D3.10** `src/core/audit.py` logs audit failures at `logger.error` (tool name
  included, still returning `None`); the `contextlib.suppress` wrappers around
  audit calls in `src/interfaces/websocket.py` and `src/api/plugins.py` are gone.
- **D3.11** `src/interfaces/websocket.py` adds `_safe_error_message(exc)` and
  sends only a category to clients (LLM timeout / LLM failure / internal error),
  logging the real exception server-side.

### Part 4 — Logging hygiene + response shapes (D4.1-D4.6)

- **D4.1** `src/core/security_log.py` sanitizes keys (CR/LF/`=`) and redacts
  nested secrets recursively (`_redact_nested`), keeping `client` visible.
- **D4.2** `src/core/timing.py` sanitizes the stage name and every key.
- **D4.3** `src/api/sessions.py` constrains `session_id`
  (`^[A-Za-z0-9_-]{1,64}$`, `max_length=64`) via `Annotated[..., Path(...)]`, so
  direct calls with `request=None` still work.
- **D4.4** `src/api/chat.py` (504/500) and `src/api/artifacts.py` error bodies use
  `{"detail": ...}`; `src/api/plugins.py` was unified in D4.5.
- **D4.5** new `src/plugins/errors.py::PluginPolicyDenied`; `enable_plugin` raises
  it on an allowlist denial. The REST API maps it to `403`, start failure to
  `502`, reload failure to `409`; the core tool raises a policy `RuntimeError`.
  Audit still records the outcome.
- **D4.6** `src/api/artifacts.py` guards non-dict plugin results/payloads
  (`TypeError` in the caught tuple) so a malformed result is a 404, not a 500.

### Part 5 — Durability / permissions / pruning (D5.1-D5.5)

- **D5.1** `src/plugins/state.py::_atomic_write_text` fsyncs the staged file and
  the parent directory, closes the `mkstemp` fd if `os.fdopen` fails, preserves an
  existing destination's mode, and defaults to `0600` only when creating. Helper
  `_fsync_directory` is best-effort (no-op where unsupported).
- **D5.2** `src/auth.py::_read_revoked_file` treats an empty/whitespace file as a
  valid empty store (latches the signature, no per-decode warning);
  `src/core/startup_checks.py` seeds `{}` instead of leaving a 0-byte file.
- **D5.3** `_REVOKE_RETENTION_SECONDS = 86400`; `_prune_revoked` drops entries
  older than the retention window when saving.
- **D5.4** `_save_revoked` runs under a best-effort OS file lock
  (`fcntl.flock` on a `.lock` sibling where available; no-op on Windows), so two
  processes cannot clobber each other.
- **D5.5** `src/interfaces/websocket.py` evicts idle identity windows:
  `release_ws_identity(identity)` is called from the endpoint's `finally`, and a
  drained deque key is deleted.

## Deviations and notes

- **D2.4** — omitted `gt=0` on the timeout fields (kept only
  `allow_inf_nan=False`): a positive constraint returns 422 and would break the
  existing `read_timeout: 0 -> 400` contract; the `_merge` guard still rejects it
  with 400.
- **D2.5** — used a loop-aware module-level `asyncio.Lock`
  (`_settings_lock_for_loop`) instead of a plain module-level lock, so
  pytest-asyncio tests that run across event loops stay green.
- **D3.7** — catch `TimeoutError` (ruff UP041; `asyncio.TimeoutError` is an alias).
- **D4.5** — implemented together with Part 3 because it shares
  `src/api/plugins.py`.
- **D5.3** — pruning runs only in `_save_revoked`, not on load:
  `tests/test_auth.py::test_revocation_store_rereads_changed_file` stores a 1970
  timestamp and expects the token to stay revoked, so pruning on load would break
  it.
- **D5.4 / D5.1** — the file lock and directory fsync are POSIX-only; on Windows
  they take a guarded no-op path. `_atomic_write_closes_fd` closes the descriptor
  for real in the test so the Windows `os.unlink` of the staged file succeeds.
- **SSRF** — literal/metadata/known-internal targets and resolved private ranges
  are blocked; DNS rebinding between check and connect is explicitly out of scope
  (documented in `src/llm/security.py`).

## Verification

- `uv run ruff check .` — clean.
- `uv run pytest -m "not integration and not e2e" -q` — **1123 passed, 12
  skipped, 5 deselected** (`tmp/tests_core_hardening2_full.log`).
- Per-part targeted runs, all green:
  - Part 1 `tests/test_auth.py tests/test_api_auth.py tests/test_api_deps.py
    tests/test_artifacts.py tests/test_startup_checks.py` — 93 passed.
  - Part 2 `tests/test_llm_security.py tests/test_llm_config.py
    tests/test_api_llm.py tests/test_llm_client.py tests/test_llm_usage.py
    tests/test_router.py`.
  - Part 3 `tests/test_plugin_manager.py tests/test_plugins_health.py
    tests/test_core_tools.py tests/test_api_plugins.py tests/test_db_session.py
    tests/test_audit.py tests/test_websocket.py tests/test_plugins_init.py` —
    184 passed.
  - Part 4 `tests/test_security_logging.py tests/test_timing.py
    tests/test_history_api.py tests/test_chat_api.py tests/test_artifacts.py` —
    50 passed.
  - Part 5 `tests/test_plugin_state.py tests/test_auth.py
    tests/test_startup_checks.py` — 52 passed.
- No frontend was touched, so `npx tsc --noEmit` was not required.

## Changed files

- Auth: `src/auth.py`, `src/api/auth.py`, `src/api/deps.py`, `src/api/artifacts.py`
- LLM: `src/llm/security.py` (new), `src/llm/config.py`, `src/llm/client.py`,
  `src/llm/router.py`, `src/api/llm.py`
- Plugins/async: `src/app.py`, `src/plugins/manager.py`, `src/plugins/health.py`,
  `src/plugins/__init__.py`, `src/plugins/errors.py` (new), `src/plugins/state.py`,
  `src/core/audit.py`, `src/core/core_tools.py`, `src/core/startup_checks.py`,
  `src/db/session.py`, `src/interfaces/websocket.py`
- Logging/response shapes: `src/core/security_log.py`, `src/core/timing.py`,
  `src/api/sessions.py`, `src/api/chat.py`, `src/api/plugins.py`
- Tests: `tests/test_auth.py`, `tests/test_api_auth.py`, `tests/test_api_deps.py`,
  `tests/test_artifacts.py`, `tests/test_startup_checks.py`,
  `tests/test_llm_security.py` (new), `tests/test_llm_config.py`,
  `tests/test_api_llm.py`, `tests/test_llm_client.py`,
  `tests/test_plugin_manager.py`, `tests/test_plugins_health.py`,
  `tests/test_db_session.py`, `tests/test_audit.py`, `tests/test_websocket.py`,
  `tests/test_core_tools.py`, `tests/test_api_plugins.py`,
  `tests/test_security_logging.py`, `tests/test_timing.py`,
  `tests/test_history_api.py`, `tests/test_chat_api.py`,
  `tests/test_plugin_state.py`

## Commits

Commits created: none

## Review fix round 1

Five post-review findings applied (all in `src/`; tests added):

1. **DNS failures now raise `ValueError`** (`src/llm/security.py::resolve_host`):
   `socket.getaddrinfo` failures (`socket.gaierror`, an `OSError`) are converted
   to `ValueError("base_url host '<host>' could not be resolved")`, so both
   `resolve=True` call sites (`src/api/llm.py` `/llm/test` -> 400 and
   `src/llm/client.py` `chat_completion` -> `LlmError`) keep their documented
   contract instead of surfacing a 500 / raw `OSError`.
2. **Provider validation cached per base URL** (`src/llm/client.py`): the client
   remembers `_validated_base_url` and skips the DNS round-trip when
   `config.base_url` is unchanged, so the chat/planner/summary hot paths no longer
   resolve on every turn; a base-URL change re-validates.
3. **`100.64.0.0/10` (carrier-grade NAT) rejected** (`src/llm/security.py`):
   `_is_global_address` now also rejects `not address.is_global`, closing the
   shared-address-space gap (used by cloud NAT / overlay networks) while leaving
   genuinely public addresses accepted.
4. **Misleading no-op eviction removed** (`src/interfaces/websocket.py`):
   `_allow_ws_message` no longer deletes then immediately recreates the same key;
   its comment now states the key persists until `release_ws_identity` runs on
   socket close.
5. **`_run_plan` crash handler sanitized** (`src/interfaces/websocket.py:286`):
   the client-facing crash message now goes through `_safe_error_message(exc)`
   instead of `str(exc)`, matching every other client-facing error path.

Tests: `tests/test_llm_security.py` (+`100.64.0.1` case, DNS-failure ->
`ValueError` for both `resolve_host` and `validate_provider_url(resolve=True)`);
`tests/test_llm_client.py` (+validation cached across calls, +re-runs on base-URL
change); `tests/test_websocket.py` (+crash does not leak exception text,
+`release_ws_identity` drops the window).

Verification: `uv run ruff check .` clean; `uv run pytest -m "not integration and
not e2e" -q` -> **1130 passed, 12 skipped, 5 deselected**
(`tmp/tests_core_hardening2_review1_full.log`).

## Review fix round 2

Nine findings applied:

1. **Retention comment matches the value** (`src/auth.py`): the
   `_REVOKE_RETENTION_SECONDS = 86400` comment now says "three token lifetimes
   (24 h)" instead of "two" (8 h token), so the rationale matches the number.
2. **JWT-mode warning no longer over-claims** (`src/core/startup_checks.py`):
   `_check_jwt_mode_credentials` distinguishes `AUTH_MODE=both` **with** a static
   `ADMIN_TOKEN` (warns that only the static token works) from the case where
   admin access really would 503.
3. **`max_retries` follows the 400 contract** (`src/api/llm.py`): dropped `ge=0`
   from `LlmSettingsUpdate.max_retries` so the `_merge` `max_retries < 0` -> 400
   check stays live and consistent with the timeout fields (which keep
   `allow_inf_nan=False` and their live `<= 0` check). No more hidden 422.
4. **SSRF address check simplified + denylist** (`src/llm/security.py`):
   `_is_global_address` is now `not address.is_global` plus an explicit
   `_BLOCKED_NETWORKS` CIDR denylist covering blocks `is_global` mislabels
   (`128.0.0.0/16`, `192.0.0.0/24`, …); the redundant property disjunction is
   gone. The duplicate `metadata.google.internal` entry was removed (the
   `.internal` suffix already rejects it); `metadata.goog` is kept.
5. **No DNS on the request path** (`src/llm/client.py`): `chat_completion` uses
   `validate_provider_url(..., resolve=False)` (literal/hostname guard only) and
   the indefinite `_validated_base_url` memoization was removed.
6. **Resolve once at config-write time** (`src/api/llm.py`):
   `update_llm_settings` runs `validate_provider_url(supplied_base_url,
   resolve=True)` via `asyncio.to_thread` before merging, so an unresolvable or
   privately-resolving host is rejected with 400 once, not on every turn. The
   `/llm/test` probe keeps its resolving check.
7. **Non-string `base_url` guarded** (`src/llm/security.py`):
   `validate_provider_url` raises `ValueError("base_url must be a string")`, so a
   corrupt config surfaces as `LlmError`/400 instead of `AttributeError`/500.
8. **Non-blocking revocation lock** (`src/auth.py`): `_file_lock` uses
   `fcntl.flock(..., LOCK_NB)`; if the cross-process lock is busy it logs a
   warning and proceeds unlocked (the already-accepted best-effort path) instead
   of stalling the event loop.
9. Tests updated.

Tests: `tests/test_llm_security.py` (+`128.0.0.1`/`192.0.0.1` rejected,
+non-string URL -> `ValueError`); `tests/test_llm_client.py` (replaced the
round-1 caching tests with "request path does not resolve DNS" and "request path
still rejects a literal internal URL"); `tests/test_api_llm.py`
(+PUT rejects a privately-resolving host, +PUT rejects an unresolvable host,
+negative `max_retries` -> 400); `tests/test_startup_checks.py`
(+`both`+static token names only the JWT path, +`both` without token warns about
503); `tests/test_auth.py` (+busy lock degrades with a warning).

Verification: `uv run ruff check .` clean; `uv run pytest -m "not integration and
not e2e" -q` -> **1139 passed, 12 skipped, 5 deselected**
(`tmp/tests_core_hardening2_review2_full.log`).

## Review fix round 3

Four findings applied:

1. **Revocation store is create-only** (`src/core/startup_checks.py`): the startup
   check no longer truncates an existing store. It seeds `{}` via `path.open("x")`
   (exclusive create) and, on `FileExistsError`, only proves writability with
   `path.open("a")` — so a concurrent writer's revocations cannot be erased by
   the `exists()`/`stat()` -> `write_text()` TOCTOU.
2. **`_file_lock` tolerates an unnamable store** (`src/auth.py`): `path.with_name`
   moved inside the guarded block and `ValueError` (e.g. `LOOMCORE_REVOKED_FILE=.`
   or `/`) is treated like the other "cannot lock" cases, so logout degrades to
   the in-memory store instead of 500ing.
3. **IPv4-mapped IPv6 normalized** (`src/llm/security.py`): `_normalize_address`
   unwraps `::ffff:a.b.c.d` (`ipv4_mapped`) and 6to4 `2002::/16` (`sixtofour`) to
   the embedded IPv4 before the global/denylist checks, so
   `[::ffff:127.0.0.1]`/`[::ffff:169.254.169.254]`/`[2002:7f00:0001::]` are
   rejected on every Python version (not only >= 3.13).
4. **IPv4-embedding transition prefixes denied** (`src/llm/security.py`):
   `_BLOCKED_NETWORKS` gained `::ffff:0:0/96` and `64:ff9b::/96` (NAT64), closing
   the gap for forms without an `ipaddress` unwrap helper.

Tests: `tests/test_llm_security.py` (+`[::ffff:127.0.0.1]`,
+`[::ffff:169.254.169.254]`, +`[2002:7f00:0001::]`, +`[64:ff9b::7f00:1]` rejected;
+public `[::ffff:93.184.216.34]` accepted); `tests/test_startup_checks.py`
(+existing non-empty store untouched, +existing empty store untouched);
`tests/test_auth.py` (+unnamable store does not raise).

Verification: `uv run ruff check .` clean; `uv run pytest -m "not integration and
not e2e" -q` -> **1146 passed, 12 skipped, 5 deselected**
(`tmp/tests_core_hardening2_review3_full.log`).

## Review fix round 4

One finding applied:

1. **Out-of-range numeric settings no longer escape** (`src/llm/config.py::_as_float`):
   the `float(value)` conversion is wrapped in `try/except OverflowError`. `json.loads`
   keeps a huge integer literal as an arbitrary-precision `int`, and
   `float(10**400)` raises `OverflowError` before `math.isfinite` could reject it; the
   guard makes `load_config` fall back to the default instead of propagating out of
   `get_active()` (preserving the "reads never fail hard" contract).

Tests: `tests/test_llm_config.py` (+`test_load_ignores_out_of_range_integers` — a
400-digit `read_timeout`/`connect_timeout` falls back to the defaults while a valid
`pool_timeout` is kept).

Verification: `uv run ruff check .` clean; `uv run pytest -m "not integration and
not e2e" -q` -> **1147 passed, 12 skipped, 5 deselected**
(`tmp/tests_core_hardening2_review4_full.log`).

## Review fix round 5

Three findings applied:

1. **Shared WS rate-limit window is not dropped on disconnect**
   (`src/interfaces/websocket.py`): D5.5's `release_ws_identity` call was reverted.
   The only caller passes no `identity`, so every socket shares the single
   `"admin"` window; popping it on disconnect let any socket reset the 60 msg/min
   limit for all sockets (and freed no memory). The window now persists for the
   process lifetime, preserving the documented per-identity invariant. The
   `release_ws_identity` helper was removed.
2. **Lazy logging in the plugin reader** (`src/plugins/manager.py`): the
   non-JSON-stdout `logger.debug` now passes `plugin_name` as an argument instead
   of eagerly interpolating an f-string.
3. **Settings I/O off the event loop** (`src/api/llm.py`): the
   `_merge(get_active(), data)` + `set_active(...)` read-modify-write now runs via
   `await asyncio.to_thread(_apply)` while holding `_settings_lock`, so a slow
   volume or the config module's `RLock` cannot stall concurrent requests.

Tests: `tests/test_websocket.py` (replaced
`test_release_ws_identity_drops_window` with
`test_disconnect_keeps_the_shared_window` — after a socket disconnect the used
limiter slot still counts).

Verification: `uv run ruff check .` clean; `uv run pytest -m "not integration and
not e2e" -q` -> **1147 passed, 12 skipped, 5 deselected**
(`tmp/tests_core_hardening2_review5_full.log`).

## Review fix round 6

Three findings applied (all in the SSRF guard except the last):

1. **Resolver-only numeric IPv4 forms are now treated as literals**
   (`src/llm/security.py`): `_parse_host_address` falls back to
   `ipaddress.ip_address(socket.inet_aton(host))` when `ipaddress.ip_address`
   rejects the host, so `2130706433`, `0x7f000001`, `0177.0.0.1` and `127.1`
   (all of which `getaddrinfo` reads as `127.0.0.1`) are rejected on the
   literal-only request path instead of slipping through as host names.
   `_is_global_address` now delegates to the new `_address_is_global` helper so
   the parsed address is checked directly.
2. **Trailing-dot hostnames are normalized** (`src/llm/security.py`): the host
   is `rstrip(".")`-ed before both the numeric parse and the name checks, so
   `localhost.` / `foo.internal.` / `metadata.goog.` / `127.0.0.1.` can no
   longer bypass the localhost/suffix/metadata rules; `.localhost` (RFC 6761
   loopback) joined `_INTERNAL_SUFFIXES`, and an empty normalized host is
   rejected.
3. **`asyncio.TimeoutError` alias dropped** (`src/interfaces/websocket.py`):
   `_safe_error_message` now tests `(LlmTimeoutError, TimeoutError)` — the alias
   is redundant since Python 3.11 and tripped ruff `UP041`.

Tests: `tests/test_llm_security.py` (+`2130706433`, `0x7f000001`, `0177.0.0.1`,
`127.1`, `127.0.0.1.`, `localhost.`, `foo.localhost`, `foo.internal.`,
`metadata.goog.` rejected; +numeric encoding of a public address accepted).

Verification: `uv run ruff check .` clean; `uv run pytest -m "not integration and
not e2e" -q` -> **1156 passed, 12 skipped, 5 deselected**
(`tmp/tests_core_hardening2_review6_full.log`).






