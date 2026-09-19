# Changelog

Short entries only: date + one or two lines. Details live in `docs/` or in the
task report (RULES 14).

## 2026-09-19

- Rewrote `RULES.md` for the current LoomCore architecture (thin core + MCP
  plugins + plan-and-execute agent).
- Added coordinator workspace (`Agents/README.md`), docs map (`docs/README.md`)
  and task templates (`docs/tasks/_template/`).
- Fixed admin credential precedence: a stale/invalid session cookie no longer
  shadows a valid `Authorization: Bearer` token (`extract_admin_token`,
  `/api/auth/session`).
- Hardened `/api/artifacts/{name}/{kind}`: sanitize the plugin-supplied download
  filename and media type, and stop returning the raw exception in the 404 body.
- Widened session id columns (`sessions.id`, `messages.session_id`,
  `audit_log.session_id`) to 64 chars (migration `006`) so the frontend's
  `sess_<hex>` ids persist instead of failing the INSERT.
- Hardened `/api/chat`: log LLM failures, return the budget 429 as
  `{"detail": ...}` (matching the global rate-limit handler), and bound
  `message` (1..8000) and `session_id` (<=64, `[A-Za-z0-9_-]`).
- Hardened admin auth: `login` no longer short-circuits at the username check
  (constant-work PBKDF2/TOTP), `decode_jwt_token` fails closed on an empty
  signing secret, the cookie `Secure` flag derives from `is_https()`, and
  `/api/auth/session` is rate-limited to 30/min.
