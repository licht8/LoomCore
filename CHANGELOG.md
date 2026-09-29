# Changelog

Short entries only: date + one or two lines. Details live in `docs/` or in the
task report (RULES 14).

## 2026-09-29

- Bounded post-tool summary: the summary call is now attempted once and capped by
  `LLM_SUMMARY_TIMEOUT` (default 15s), and a timeout/empty/mismatched summary
  yields a short deterministic digest instead of the raw tool dump — a stalled or
  "thinking" model can no longer turn a tool turn into a multi-minute wait that
  ends by copying the tool output.
- Faster post-tool replies: the summary now asks for at most two short
  sentences, its default cap dropped to `LLM_SUMMARY_MAX_TOKENS=256`, and with
  `LLM_THINKING_TEMPLATE=1` the summary always sends `enable_thinking=false`
  (the planner still honors `LLM_ENABLE_THINKING`) so a reasoning model no
  longer thinks before the short own-words answer. A fast `LLM_SUMMARY_MODEL` is
  documented as the main lever.
- The assistant always answers in its own words: report tools no longer skip the
  post-run summary (their cards just render as Markdown); the raw output is shown
  only when the user asks for it or for an allow-listed read-only command. If a
  configured `summary_model` fails or returns nothing, `summarize_plan` now retries
  once with the main model, so the assistant no longer silently echoes tool output.
- Tool cards are collapsed by default in the chat feed: the header keeps the tool
  name, status and download buttons while the output body expands on click (a
  failed tool auto-expands), so the assistant's message is what the user reads.

## 2026-09-28

- Deployment safety hardening: `.env.example` no longer ships usable credential
  placeholders (`ADMIN_PASSWORD_HASH`/`ADMIN_TOTP_SECRET`/`SESSION_SECRET` empty)
  and startup fails closed if a known placeholder is still set; compose now
  forwards `LOOMCORE_PLUGINS_ALLOWED`/`TRUSTED_PROXIES`/`LOOMCORE_REPORT_COMMANDS`
  and the plugin allow-list is read from Settings instead of raw `os.environ`;
  plugin subprocesses are terminated on shutdown (`PluginManager.terminate_all`).
- Read-path hardening: `GET /api/tools` is served from the indexed plugin cache
  (`get_tools_for_routing`) instead of a live JSON-RPC fan-out, and the WebSocket
  handshake validates `session_id` (`[A-Za-z0-9_-]{1,64}`) before accept,
  closing 1008 on a malformed or oversized id.
- Core risk denylist + write cap: `classify_tool_risk` now lets the core dangerous
  denylist win over a plugin-declared `risk`, so a plugin can no longer mark
  `ops_run_command`/`ops_write_file` safe to skip approval and audit;
  `ops_write_file` enforces `MAX_WRITE_BYTES` (256 KB), mirroring the read cap.
- Robustness hardening: `GET /api/audit/verify` streams the hash chain
  (`iter_audit_entries` keyset pagination + `latest_entry_hash` anchor, so
  truncation is detected too) instead of materialising the whole table; new
  migration `009` + ORM index `ix_messages_session_id_created_at`; the frontend
  stops reconnecting on WS close `1008`, surfaces an error when a send is dropped
  with no socket, and the dead "Attach file" button was removed; session history,
  artifact downloads and logout are now rate-limited.
- Docs/tests/CI hygiene: corrected stale docs (`wg_create_client` ->
  `wg_create_peer`, `/api/artifacts/<name>/...`, the planner's two callable
  tools, "core does not read the manifest") and the README `REQUIRE_TLS` login
  note; the frontend gained a Vitest runner with unit tests for the
  `chat-ws`/`chat-hydrate` reducers; CI now runs the `integration` marker and the
  frontend test suite.
- Overview + session metadata: `GET /api/overview` now runs its sections
  concurrently, bounds each tool with a timeout and never dispatches through an
  unhealthy plugin (no restart churn on the 15s poll); `sessions` gained
  `created_at`/`updated_at`/`title` (migration `010`, backfilled from messages)
  and `list_sessions` reads them directly instead of timestamp-equality joins,
  ordering by last activity.
- Planner argument grounding: the tool catalog now renders each property's
  `enum`/`default` constraints (e.g. `source [auto|journal|journald|files,
  default auto]`), so the planner no longer guesses invalid values; LoomOps
  `ops_log_search` accepts `journald` as an alias for `journal`.

## 2026-09-22

- Frontend gaps: added read-only admin surfaces backed by four thin API routes —
  `GET /api/sessions` (session list with title/preview/count served by the new
  in-chat conversations rail), `GET /api/audit` + `GET /api/audit/verify`
  (hash-chain browser and verify), `GET /api/overview` (curated host/plugin
  aggregate, each section null on failure), and `GET /api/approvals` (pending
  proposed plans, read-only). New `/tools`, `/audit`, `/overview`, `/approvals`
  pages, a `?session=` deep link, and a Tools catalog. No migration, no WS/schema
  change.
- Perceived-latency pass: the plan goal now streams as a transient
  `plan_progress` status line ("Planning: …") above the composer (never an
  assistant bubble; cleared when a tool card or the response arrives), report
  tools are declared by the plugins (`report` / `report_arg` in `TOOLS_SCHEMA`,
  read into the plugin manager — the core no longer hardcodes LoomWG/LoomOps
  names), and an allow-listed read-only `ops_run_command`
  (`LOOMCORE_REPORT_COMMANDS`, empty default) skips the summary LLM call.
- Report tools answer with their raw output once: an all-report plan (every step
  a listing/report tool, per the curated `_is_report_tool` set) skips the summary
  LLM call and renders its tool cards compact, so the Markdown output appears only
  in the assistant bubble; command/action tools still get an own-words summary
  with the raw output on the card. Tool cards now carry `format` (markdown/text)
  and `compact` (persisted in `tool_calls`), rendered by the frontend.
- Markdown output and parallel plan steps: tool `text` (core tools, LoomWG
  peers/interfaces/stats, LoomOps failed units) and the summary/planner prompts
  now emit Markdown bullet lists instead of comma-joined prose (`data` payloads
  unchanged); consecutive read-only `parallel: true` plan steps run concurrently
  via `asyncio.gather` (non-safe steps stay serial and approval-gated); and
  `LLM_PLANNER_MODEL` is documented as the primary planning-latency lever.
- Meta-listing fast-path and planner recovery: short tool/plugin listing
  requests (`show me tools for LoomOps`, `покажи инструменты для LoomCore`) are
  now answered deterministically without an LLM call, `core_list_tools` accepts
  an optional `plugin` filter (served from the indexed cache), a `reply` step
  inside `agent_plan` is answered as text, and a bare known tool name becomes a
  direct plan (or a specific re-ask) instead of the confused fallback. Added a
  configurable `LLM_PLANNER_MODEL` and stream the plan goal for action-turn
  feedback.
- Streaming replies and token caps: the planner's `reply` text and the plan
  summary now stream to the client as `delta` WebSocket events (reconciled by a
  shared `message_id` on the final `response`), with `LLM_PLANNER_MAX_TOKENS` /
  `LLM_SUMMARY_MAX_TOKENS` (default 1024/512) bounding the two per-turn LLM
  calls. The planner prompt catalog is trimmed (tool descriptions capped at 160
  chars; user/history char limits lowered) to cut prefill latency.
- Assistant latency quick wins: the planner tool catalog is served from the
  indexed cache (`PluginManager.get_tools_for_routing`, skipping plugins that are
  disabled, unhealthy or advertise malformed tools) instead of a per-turn
  `tools/list` round trip, the summary LLM call is skipped for direct plans,
  `LLM_SUMMARY_MODEL` bootstraps a cheaper summary model, and the default LLM
  `read_timeout`/`max_retries` drop to 30s/1 (compose defaults and DEPLOY.md
  aligned).
- Hardened the DB layer against `InvalidCachedStatementError`: the async engine
  now disables both asyncpg and SQLAlchemy prepared-statement caches
  (`statement_cache_size` / `prepared_statement_cache_size` = 0), so DDL applied
  by Alembic (e.g. migration `006`) can no longer invalidate a live pooled plan.
- `deploy/install.sh` now applies migrations before the hub starts (build, bring
  up `db`, `docker compose run --rm hub alembic upgrade head`, then start the
  stack), so no long-running hub is connected during the migration.
- Added LLM usage tracking: every completed provider call is recorded (tokens,
  latency, purpose, session, attempts, status) in the new `llm_usage` table
  (migration `008`), exposed read-only to admins at `GET /api/llm/usage` and in
  the new `/usage` dashboard page. Recording is best-effort and never fails a
  chat turn.

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
- `load_soul` falls back to the default for a blank or non-UTF-8 `SOUL.md` and
  memoises reads; `_render_plan` no longer mis-keys id-less or duplicate steps and
  truncates oversized tool results, the request, history and the summary body.
- The router only recovers a textual `name {json}` tool call when the name is
  known and the body parses, and no longer appends an empty assistant turn when
  re-asking.
- WebSocket plan execution no longer aborts remaining steps when persisting a
  tool message fails: the terminal event is still sent, the plan completes, and
  the failure is logged.
- WebSocket inbound frames are shape-validated (non-string `message` or non-dict
  frames are ignored) and malformed JSON frames are reported and skipped instead
  of closing the connection; bad frames still consume the per-identity throttle.
- Test plugin loaders are disambiguated: LoomOps logical names are namespaced
  (`loomops.*`), each loader asserts the resolved module's root package, and the
  package is purged once per test (modules are cached for module identity).
- The plugin manager kills a spawned child when enable/reload/restart fails,
  never auto-restarts a `BUSY` plugin from `get_all_tools`, rejects a duplicate
  tool name with a warning, and drops tools a plugin stops advertising.
- Hardened plugin tests: manifest lookups guard `None`, LoomWG capability
  masks derive from `_CAPABILITIES`, JSON-RPC responses correlate by `id`, and
  the e2e teardown picks a unique interface/free port and reports failures.
- **Breaking:** removed the hardcoded Hetzner provider and all `HETZNER_*`
  settings. The LLM provider (OpenAI, OpenRouter, Groq, DeepSeek, or a custom
  OpenAI-compatible endpoint) is now chosen at runtime in the new **Settings**
  tab, persisted to `/data/llm.json` (`LLM_SETTINGS_FILE`) with the API key
  write-only (mode 0600, never returned/logged/audited). Generic `LLM_*` env
  vars only pre-seed the first start.
- Added the admin LLM settings API (`GET /api/llm/providers`,
  `GET`/`PUT /api/llm/settings`, `POST /api/llm/test`) and an unconfigured-state
  banner; an unconfigured deployment still boots and chat replies with a setup
  hint instead of failing.
