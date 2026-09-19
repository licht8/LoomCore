# TZ_RU.md — auth credential precedence (stale cookie must not shadow a valid Bearer)

## 1. Goal

`extract_admin_token()` in `src/api/deps.py` returns the session cookie whenever
it is non-empty, **without validating it**. A stale, expired, malformed or
attacker-supplied cookie therefore shadows a perfectly valid `Authorization:
Bearer` header. This produces two defects:

1. A valid Bearer token is silently ignored, yielding confusing `401`s on every
   protected endpoint (`/api/chat`, `/api/plugins/*`, `/api/sessions`,
   `/api/artifacts`, `/api/auth/session`).
2. Because `chat_key_func` derives the rate-limit bucket from the extracted
   token, an authenticated user can attach a junk cookie and silently move their
   own requests from the per-token bucket to the shared per-IP bucket (or, from
   an attacker's view, force collisions on another user's IP bucket).

Fix: validate each credential candidate in order and return the first one that
is a genuine credential for the active auth mode; only fall back to the old
"cookie first" raw value when neither candidate validates, so the existing
error-code paths (401/403/503) keep working.

## 2. Non-goals

- No change to `require_admin_token`, `is_valid_admin_credential`, `auth_mode`,
  JWT decoding, TOTP, TLS enforcement or audit logging.
- No change to the WebSocket auth path (`src/api/websocket.py`) — it already
  evaluates cookie and subprotocol token as alternatives (no shadowing).
- No change to the query-string token ban.
- No dependency changes.

## 3. Approved decisions (locked)

- Credential candidates are evaluated in the fixed order: session cookie, then
  `Authorization: Bearer` header. This preserves the existing preference for the
  cookie *when it is valid*, which is the desired UX for a logged-in browser.
- "Valid" means `src.core.security.is_valid_admin_credential(token)` (the
  non-raising check that already honours `AUTH_MODE`).
- If no candidate validates, `extract_admin_token` returns the cookie when
  present, otherwise the parsed Bearer value. This keeps `require_admin_token`
  able to report the precise failure (e.g. `503 not_configured`,
  `403 static_token_disabled`) instead of a generic 401.
- `/api/auth/session` must use the same fall-through: a valid Bearer may rescue a
  stale cookie, matching the behaviour of every other protected route.

## 4. Data model / interfaces

- `extract_admin_token(request, authorization=None) -> str | None` — signature
  unchanged; only the selection semantics change.
- `/api/auth/session` (GET) — response contract unchanged
  (`{"authenticated": true}` / 401).
- No schema, migration or config changes.

## 5. Parts

1. `src/api/deps.py` — rewrite `extract_admin_token` to:
   - parse the cookie and (when present) the `Bearer` value;
   - return the first candidate for which `is_valid_admin_credential(...)` is
     true;
   - otherwise return `cookie or header` (old fallback);
   - update the docstring to state the validated-order rule.
   `require_admin_token_from_request` and `chat_key_func` need no signature
   change; `chat_key_func` already re-checks `is_valid_admin_credential`, so it
   now keys by the valid token even when a junk cookie is present.

2. `src/api/auth.py` — `/api/auth/session`: only short-circuit to
   `{"authenticated": True}` when the cookie is a decodable JWT; otherwise, when
   a `Bearer` header is present, evaluate it via
   `require_admin_token(extract_admin_token(request, authorization), request)`
   and return authenticated; only raise 401 when no candidate succeeds. Keep the
   existing JWT-disabled/`require_admin_token` error semantics.

3. `tests/test_api_deps.py` — add/update unit tests (see §6).

4. `tests/test_api_auth.py` — add a `/api/auth/session` fall-through test.

## 6. Tests

Add to `tests/test_api_deps.py`:

- `test_extract_admin_token_prefers_valid_cookie_over_header` — configure
  `auth_mode="both"`, `admin_token="static"`, JWT enabled
  (`session_secret` + `admin_password_hash` + `admin_totp_secret`); valid JWT
  cookie + `Bearer static` -> returns the cookie. (Rename/replace the existing
  `test_extract_admin_token_prefers_cookie_over_header`, which currently passes
  only because *no* auth is configured and the fallback keeps the cookie.)
- `test_extract_admin_token_falls_through_to_header_when_cookie_invalid` —
  `auth_mode="token"`, `admin_token="good"`; junk cookie + `Bearer good` ->
  returns `"good"`.
- `test_extract_admin_token_returns_cookie_when_nothing_validates` — no auth
  configured; junk cookie + `Bearer junk` -> returns the cookie (fallback
  preserved).
- `test_chat_key_func_ignores_stale_cookie_when_header_valid` —
  `auth_mode="token"`, `admin_token="real"`; junk cookie + `Bearer real` ->
  `"token:" + sha256("real")`, not `"ip:..."`.

Add to `tests/test_api_auth.py`:

- `test_session_falls_through_to_bearer_when_cookie_is_stale` —
  `auth_mode="token"`, `admin_token="static-token"`; set a bogus
  `SESSION_COOKIE` and send `Authorization: Bearer static-token` -> 200
  `{"authenticated": True}`.

Existing tests must keep passing unchanged:
`test_extract_admin_token_ignores_query`,
`test_extract_admin_token_reads_bearer_header`,
`test_extract_admin_token_ignores_non_bearer_header`,
`test_chat_key_func_*`, `test_session_*`, `test_protected_endpoint_*`.

## 7. Acceptance criteria

- With `AUTH_MODE=token` (or `both`) and a configured `ADMIN_TOKEN`, a request
  carrying a junk/expired `SESSION_COOKIE` **and** a valid `Authorization:
  Bearer <ADMIN_TOKEN>` is accepted (200) on a protected endpoint.
- `chat_key_func` returns the `token:<sha256>` bucket for that same request, not
  an `ip:` bucket.
- A valid JWT cookie still takes precedence over a valid Bearer header.
- When no credential validates, error codes are unchanged from today (401 vs
  403 vs 503).
- `ruff check .` is clean.
- `pytest -m "not integration and not e2e"` passes.

## 8. Commits

- `fix(api): validate cookie before shadowing a valid Bearer token`

## 9. Risks

- Error-reason drift in the rare case "junk cookie + genuine-but-mode-disabled
  static Bearer" (returns 401 instead of the old 403 for the header): both deny
  the request, so it is safe; note it in the report.
- `is_valid_admin_credential` now runs during extraction, so a JWT decode may
  happen twice per request (once in extraction, once in `require_admin_token`).
  Acceptable; do not add caching in this task.
- `/api/auth/session` currently treats a decodable JWT as authenticated even
  under `AUTH_MODE=token`; do not change that here (pre-existing, out of scope).
