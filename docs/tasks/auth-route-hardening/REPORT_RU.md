# REPORT_RU.md — admin auth route hardening

Task spec: `docs/tasks/auth-route-hardening/TZ_RU.md`.

## Goal

Close four hardening findings in the admin auth surface plus make slowapi state
deterministic in tests:

1. `login`'s `and` chain short-circuited at the username check, so the
   PBKDF2/TOTP work never ran for a wrong username — a login timing oracle.
2. `/session` and `/logout` decoded the session JWT with no empty-secret guard.
3. `login` derived the cookie `Secure` flag from a raw `X-Forwarded-Proto`
   comparison instead of `is_https()`.
4. `/session` verified the static token with no rate limit (unthrottled oracle).

## Delivered

- `src/auth.py` — `decode_jwt_token` early return is now
  `if not token or not secret: return None` (fail closed; covers `/session`
  and `/logout`). Docstring updated.
- `src/api/auth.py`
  - Imported `generate_password_hash`; added module constant
    `_DUMMY_PASSWORD_HASH = generate_password_hash(secrets.token_urlsafe(32))`.
  - `login` now evaluates the credential parts separately and unconditionally:
    `username_ok` via `secrets.compare_digest` on UTF-8 bytes,
    `password_ok = verify_password(pw, settings.admin_password_hash or
    _DUMMY_PASSWORD_HASH)`, `totp_ok = verify_totp(...)`, then
    `valid = username_ok and admin_jwt_mode_enabled() and password_ok and totp_ok`.
    Response bodies/status codes are unchanged.
  - Cookie now uses `secure=settings.secure_cookies or is_https(request)`.
  - `auth_session` decorated with `@limiter.limit("30 per 60 seconds")` directly
    under `@router.get("/session")`; docstring drops "non-rate-limited" and
    states the 30/min cap.
- `tests/conftest.py` — added autouse `_reset_rate_limiter` fixture calling
  `limiter.reset()` before each test (no IP-bucket arithmetic).
- `tests/test_auth.py` — added `test_decode_jwt_rejects_empty_secret`.
- `tests/test_api_auth.py` — added
  `test_login_verifies_password_and_totp_for_wrong_username` (spies),
  `test_login_uses_dummy_hash_when_unconfigured` (asserts the stubbed hash is
  `pbkdf2_sha256$...`), `test_login_sets_secure_cookie_for_forwarded_proto_list`
  (`"https, http"`), `test_login_sets_secure_cookie_for_uppercase_https`
  (`"HTTPS"`), and `test_session_rate_limited` (30×401 then 429).
- `tests/test_rate_limit.py` — added non-integration
  `test_session_limit_registered_as_30_per_minute` asserting
  `limiter._route_limits["src.api.auth.auth_session"]` is 30 per 60 seconds.

## Verification

- `uv run ruff check .` — clean (`All checks passed!`).
- `uv run pytest tests/test_auth.py tests/test_api_auth.py tests/test_rate_limit.py -q`
  — 51 passed (log: `tmp/tests_auth_route_hardening.log`).
- `uv run pytest -m "not integration and not e2e" -q` — 917 passed, 11 skipped,
  5 deselected (log: `tmp/tests_auth_route_hardening_full.log`). Previous suite
  size was 910; the seven new tests account for the delta.
- Integration tests not run locally (no Postgres).

## Changed files

- `src/auth.py` — empty-secret fail-closed in `decode_jwt_token`.
- `src/api/auth.py` — constant-work login, dummy hash, `is_https` Secure flag,
  `/session` 30/60 limit + docstring.
- `tests/conftest.py` — autouse limiter reset.
- `tests/test_auth.py` — empty-secret test.
- `tests/test_api_auth.py` — oracle/dummy-hash/Secure/session-limit tests.
- `tests/test_rate_limit.py` — `/session` registry test.
- `docs/tasks/auth-route-hardening/REPORT_RU.md` — this report.

## Notes and follow-ups

- `_DUMMY_PASSWORD_HASH` is computed at import (one PBKDF2, ~100-300 ms per
  process start) and is not a literal, per TZ §9.
- A login attempt while no admin hash is configured now costs a full PBKDF2;
  intended anti-oracle behavior, not a regression.
- The `frontend/components/admin-gate.tsx` "not rate-limited" comment is now
  stale; tracked as a follow-up, not changed here (out of scope).
- The stale-cookie/Bearer precedence and all auth-mode/TLS/WS/DB behavior are
  untouched.
- Suggested commits (coordinator commits after acceptance — RULES 0a):
  1. `fix(auth): fail closed on missing session secret and remove login timing oracle`
  2. `fix(api): derive Secure cookie from is_https and rate-limit /session`
