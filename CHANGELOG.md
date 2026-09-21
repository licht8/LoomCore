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
- Fixed a file-descriptor leak in LoomWG `validate_config_text`: dropped the
  redundant `os.chmod` before wrapping the `mkstemp` descriptor, so a failing
  `chmod` can no longer leak the fd.
- Hardened the plugin surface: `/api/tools` now logs and returns a 500 instead
  of a 200 `{"error": ...}` body, and per-plugin enable/disable/reload are
  serialized under the existing per-plugin lock.
- Made the CORS allow-list a `cors_origins` setting (comma-separated
  `CORS_ORIGINS`, localhost defaults) and replaced the startup `print()` calls
  in `src/app.py` with `loomcore.app` INFO logs.
- Removed embedded credentials from the `database_url` default (now a
  credential-free placeholder; set `DATABASE_URL` explicitly).
- Confirmation phrases longer than 3 words are dropped with a warning, and a
  failed phrase-file load is no longer cached (retried on the next call;
  `clear_phrase_cache()` after an edit).
- `validate_tool_args` now reports the failing subschema's required names
  instead of re-reading the top-level schema.
- Audit: the session-taking append helper is private and documents that it
  commits the passed session; `verify_chain` accepts `expected_count` /
  `expected_head` anchors to detect truncation.
- Agent plans that exceed `max_steps` now fail loudly (status `failed`, a
  `plan_step` failure that carries its `step`) instead of silently truncating,
  and emitted step events are shallow copies so consumers never see later
  mutation.
- Revocation store reloads when its file signature changes, is guarded by a
  `threading.RLock` with in-place merge and unique temp-file writes (concurrent
  revocations no longer lost); `verify_password` bounds the PBKDF2 round count
  and catches `OverflowError`.
- Security events newline-escape the event name, guard field rendering against a
  hostile `__repr__`, and redact known-sensitive keys (password/token/secret/...).
