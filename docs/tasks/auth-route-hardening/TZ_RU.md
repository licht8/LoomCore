# TZ_RU.md — Admin auth route hardening

## 1. Goal

Close four hardening findings in the admin auth surface (`src/api/auth.py`,
`src/auth.py`), plus make slowapi state deterministic in tests:

1. **Login timing oracle.** `login` builds `valid` as an `and` chain that
   short-circuits at the username check, so `verify_password` (PBKDF2, 310k
   rounds) and `verify_totp` never run for a wrong username — login latency
   discloses whether the submitted username is `ADMIN_USERNAME`.
2. **Empty session secret.** `/session` and `/logout` call
   `decode_jwt_token(cookie, settings.session_secret)` without the empty-secret
   guard `/login` applies; an HMAC keyed with `""` is not a key.
3. **`Secure` cookie downgrade.** `login` derives `Secure` from a raw
   `X-Forwarded-Proto == "https"` comparison instead of the existing
   `is_https()` helper, so `"https, http"` (proxy chain) or `"HTTPS"` silently
   drops `Secure` on an HTTPS deployment.
4. **Unthrottled `/session` oracle.** `/session` verifies the static admin token
   with no rate limit, giving an attacker an unthrottled `ADMIN_TOKEN` guessing
   oracle.

## 2. Non-goals

- The stale-cookie/Bearer precedence in `/session` is **already fixed**
  (`src/api/auth.py:117-119`); leave that logic unchanged.
- No frontend changes. The comment at `frontend/components/admin-gate.tsx:22`
  ("not rate-limited") becomes stale; tracked as a follow-up, not fixed here.
- No changes to `require_admin_token`, auth modes, TLS enforcement, or WS auth.
- No DB/schema changes.

## 3. Approved decisions (locked)

- **Login:** evaluate the username separately (bytes `secrets.compare_digest`)
  and **always** run `verify_password` and `verify_totp`, regardless of the
  username result. When `settings.admin_password_hash` is falsy, pass a
  module-level dummy hash so the PBKDF2 work still happens.
- **Empty secret:** fail closed in `decode_jwt_token`
  (`if not token or not secret: return None`). This covers `/session` and
  `/logout`; no separate 503 branch is added there (fail-closed is sufficient,
  and `/login` already returns 503).
- **Secure flag:** `secure=settings.secure_cookies or is_https(request)`.
- **`/session` rate limit:** `30 per 60 seconds` (owner choice; the SPA calls
  this endpoint on admin-gate mount and on status-menu popover open).
- **Test isolation:** add an autouse fixture that calls `limiter.reset()` before
  each test; do not depend on IP-bucket arithmetic.

## 4. Data model / interfaces

None (no schema/DB/API payload changes). Observable HTTP behavior changes:

- `/api/auth/session` returns `429` after 30 requests/min from one client IP.
- `decode_jwt_token(..., secret="")` returns `None` instead of decoding.
- Login response bodies/status codes are unchanged; only timing behavior
  changes.

## 5. Parts

1. **`src/auth.py` — `decode_jwt_token`:** change the early return to
   `if not token or not secret: return None`.
2. **`src/api/auth.py` — constant-work login:** import `generate_password_hash`
   from `src.auth`; add module constant
   `_DUMMY_PASSWORD_HASH = generate_password_hash(secrets.token_urlsafe(32))`.
   Replace the `valid = (...)` chain (currently lines ~60-65) with:
   - `username_ok = secrets.compare_digest(str(data.get("username") or "").encode("utf-8"), (settings.admin_username or "").encode("utf-8"))`
   - `password_ok = verify_password(str(data.get("password", "")), settings.admin_password_hash or _DUMMY_PASSWORD_HASH)`
   - `totp_ok = verify_totp(str(data.get("totp", "")), settings.admin_totp_secret)`
   - `valid = username_ok and admin_jwt_mode_enabled() and password_ok and totp_ok`
3. **`src/api/auth.py` — `login` `Secure` flag:** use
   `secure=settings.secure_cookies or is_https(request),` (`is_https` is already
   imported).
4. **`src/api/auth.py` — `auth_session`:** add
   `@limiter.limit("30 per 60 seconds")` directly under
   `@router.get("/session")` (same decorator order as `/login`); update the
   docstring, dropping "non-rate-limited" and stating the 30/min cap.
5. **`tests/conftest.py`:** add an autouse fixture:

   ```python
   @pytest.fixture(autouse=True)
   def _reset_rate_limiter():
       from src.core.rate_limit import limiter
       limiter.reset()
       yield
   ```

6. **Tests** — see §6.

## 6. Tests

- `tests/test_auth.py`: add `test_decode_jwt_rejects_empty_secret`
  (`assert decode_jwt_token(token, "") is None`).
- `tests/test_api_auth.py`:
  - `test_login_verifies_password_and_totp_for_wrong_username`: monkeypatch
    `src.api.auth.verify_password` / `src.api.auth.verify_totp` with counting
    wrappers around the `src.auth` originals; POST a wrong username; assert
    `401` and both spies were called exactly once.
  - `test_login_uses_dummy_hash_when_unconfigured`: set
    `admin_password_hash=""` (TOTP/username/session secret configured); spy on
    `verify_password`; assert `401` and the `encoded` argument is non-empty and
    starts with `pbkdf2_sha256$`.
  - `test_login_sets_secure_cookie_for_forwarded_proto_list`:
    `secure_cookies=False`, header `X-Forwarded-Proto: "https, http"` →
    `set-cookie` contains `secure`.
  - `test_login_sets_secure_cookie_for_uppercase_https`: header `"HTTPS"` →
    `set-cookie` contains `secure`.
  - `test_session_rate_limited`: 30 `GET /api/auth/session` from one unique
    `X-Forwarded-For` → the 31st response is `429`.
- `tests/test_rate_limit.py`: add a non-integration registry test asserting
  `limiter._route_limits["src.api.auth.auth_session"]` is `30` per `60` seconds
  (mirror the helper used by `test_real_mutation_endpoints_stay_strict`).

Gate commands:

```
uv run ruff check .
uv run pytest tests/test_auth.py tests/test_api_auth.py tests/test_rate_limit.py -q
uv run pytest -m "not integration and not e2e" -q
```

## 7. Acceptance criteria

- A wrong username still performs PBKDF2 and TOTP (spies confirm both calls).
- `decode_jwt_token(token, "")` is `None`; `/session` and `/logout` never decode
  with an empty secret.
- `Secure` is set for `X-Forwarded-Proto` values `https`, `HTTPS` and
  `"https, http"`.
- `/session` returns `429` after 30 requests/min and its limit is registered as
  30/60.
- The limiter is reset between tests; the full fast suite passes.
- `ruff check .` is clean.

## 8. Commits

1. `fix(auth): fail closed on missing session secret and remove login timing oracle`
2. `fix(api): derive Secure cookie from is_https and rate-limit /session`

(The coordinator commits after acceptance — RULES 0a.)

## 9. Risks

- `_DUMMY_PASSWORD_HASH` is computed at import, adding one PBKDF2
  (~100-300 ms) per process start; acceptable. It must not be a literal hash.
- A login attempt while the hash is unconfigured now costs a full PBKDF2 — this
  is intended (anti-oracle), not a regression.
- `30/60` on `/session` can still 429 a pathological SPA burst; this was chosen
  deliberately over `5/60`.
- `limiter.reset()` requires a resettable storage; `slowapi.Limiter.reset()`
  exists and the default `MemoryStorage` supports it.
