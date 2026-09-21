# Changelog

Short entries only: date + one or two lines. Details live in `docs/` or in the
task report (RULES 14).

## 2026-09-21

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
- `CORE_VERSION` now warns when package metadata is missing (source checkout),
  `get_db` is typed as an async generator, and timing fields have CR/LF
  neutralised.
- Startup probes the revocation store's writability (creating a missing file) and
  reports `cannot be persisted to`; `classify_tool_risk` hoists its sets to
  module-level frozensets and adds destructive/moderate stems.
- `core_list_tools` tolerates malformed plugin catalogs, and `dispatch_core_tool`
  refuses a dangerous core tool unless `approved=True` (threaded through the agent
  dispatcher and the websocket `_execute_tool`).
- User identity is scoped to `(provider, identifier)` (migration `007`), and
  `ensure_session` recovers a concurrent user-insert race by re-reading instead of
  surfacing `IntegrityError`.
- The LLM client rejects budget costs below 1, retries transient statuses
  (408/429/5xx) with `Retry-After`/backoff, and reuses one pooled `httpx.AsyncClient`
  that is closed on shutdown.
- Removed the ambiguous `result` field from `tool_response` events (an explicit
  `null` was indistinguishable from an absent key) and corrected the
  `exclude_none` docstring; `extract_text` tolerates malformed MCP content and
  `None`.
- The plugin health loop logs a warning when a restart fails instead of always
  reporting success, and the shared `PluginManager` is built lazily
  (`get_plugin_manager()`, PEP 562) instead of at import.
