# TZ_RU.md — Frontend gaps: sessions, tools catalog, audit viewer, host overview, approvals inbox

> Filename follows the `docs/tasks/<name>/TZ_RU.md` convention (RULES 16); the
> content is English per RULES 0. The spec is value-ordered: the parts that add
> the most visible capability first.

## 1. Goal

The backend already exposes everything an admin needs — session history,
the tool catalog, the hash-chained audit log, read-only host/WireGuard
observability, and approval-gated plans — but the Web UI only surfaces Chat,
Plugins, Usage and Settings. This task adds the five missing admin surfaces:

1. **Conversation list / session switcher** — there is no `GET /api/sessions`
   today and the app is locked to one localStorage session
   (`loomcore_session_id`). This is the most user-visible gap.
2. **Tools catalog page** — `GET /api/tools` returns name/risk/description but
   the UI only lists plugins.
3. **Audit-log viewer** — `audit_log` + `verify_chain` are fully built with no
   API or page.
4. **Host overview dashboard** — WireGuard interfaces, disk/metrics, failed
   units and core/plugin health exist as read-only tools but are only reachable
   through chat.
5. **Approvals inbox** — plans awaiting a decision are only visible inside the
   chat stream.

Every new backend surface is a thin **read-only** admin route (RULES 10); no
new plugin, no new tool, no schema change.

## 2. Non-goals

- No approve/reject (or any mutating action) over REST: the WebSocket/chat path
  stays the only way to approve, edit or reject a plan. The approvals inbox is
  **read-only list + deep link** (owner decision).
- No generic REST tool-invocation endpoint; the overview endpoint is a
  **curated read-only aggregate** (owner decision).
- No new plugin and no plugin logic change (RULES 1): the new backend files are
  read-only REST routes plus DB read helpers.
- No WebSocket protocol change: no new event types, no `WS_SCHEMA_VERSION` bump.
- No DB schema/migration: everything reads existing tables (`sessions`,
  `messages`, `audit_log`).
- No i18n / ru-en dictionaries (English-only app; the RULES 12 i18n gap stays a
  follow-up).
- No file deletions; the `frontend/AGENTS.md` Next block stays untouched.
- Direct, single-step dangerous confirmations are **not** listed in the
  approvals inbox — they are transient (per-connection, kept only in the
  in-memory `pending` dict) and never persisted — so the inbox lists only
  multi-step plans (see §9).

## 3. Approved decisions (locked)

- **D1 — Session list + switcher.** New admin `GET /api/sessions`. The
  conversation list is an **in-chat sessions rail** (a collapsible column inside
  the Chat page), not the global dashboard sidebar. Session selection lives in
  `useChat`; switching reconnects the WebSocket and re-hydrates history; switching
  and creating are blocked while a turn is in flight (`isThinking || isSending`).
- **D2 — Tools catalog.** Reuse `GET /api/tools` verbatim (no backend change);
  new `/tools` page + sidebar entry.
- **D3 — Audit viewer.** New admin `GET /api/audit` (paginated list) and
  `GET /api/audit/verify` (full-chain verification via `verify_chain`); new
  `/audit` page + sidebar entry.
- **D4 — Host overview.** New admin `GET /api/overview`, a curated read-only
  aggregate calling a fixed set of safe read tools; each section is `null` when
  its tool is missing or fails, so the endpoint never 500s; new `/overview` page
  + sidebar entry.
- **D5 — Approvals inbox.** New admin `GET /api/approvals` returning the newest
  `role="plan"` message per `plan_id` whose `plan.status == "proposed"`; new
  `/approvals` page + sidebar entry; each row deep-links to
  `/?session=<session_id>`. No approve/reject control.
- **D6 — Deep link.** The Chat page reads the `?session=` query parameter and
  passes it as the initial session, so an approvals "Open in chat" link lands on
  the right conversation. (Part 5 depends on Part 1.)
- **D7 — Auth + limits.** Every new route is admin-guarded
  (`require_admin_token` / `require_admin_token_from_request`) and rate-limited
  `60 per 60 seconds`, matching `src/api/sessions.py` and `src/api/plugins.py`.
  No new mutating route ⇒ no new audit writes.

## 4. Data model / interfaces

No DB schema change. New REST responses and new frontend types.

- `GET /api/sessions` (`limit` 1..200 default 100) →
  `{"sessions": [{session_id, title, preview, message_count, last_message_at}]}`
  ordered by `last_message_at` desc. `title` = first `role="user"` message
  (≤120 chars), `preview` = last `role="user"` message (≤120 chars),
  `message_count` = number of `role="user"` rows. Sessions with no user message
  are omitted.
- `GET /api/audit` (`limit` 1..200 default 50, `offset` ≥0 default 0) →
  `{"entries": [{id, created_at, actor, session_id, plan_id, revision, tool,
  risk, arguments, status, error}], "total": <int>}` ordered by `created_at`
  desc. Hashes (`prev_hash`/`entry_hash`) are intentionally not exposed in v1.
- `GET /api/audit/verify` → `{"valid": bool, "count": int, "head": str|null}`
  (full chain; `verify_chain` is called without `expected_count`/`expected_head`,
  so it proves tamper-evidence, not truncation — documented).
- `GET /api/overview` →
  `{"core": {...}|null, "plugins": [...], "host": {"disk":..., "metrics":...,
  "failed_units": [...]}|null, "wireguard": {"interfaces": [...]}|null}`.
  Each section is `null` when its backing tool is absent or fails; `plugins` is
  always present (from `plugin_manager.get_plugin_status`).
- `GET /api/approvals` →
  `{"approvals": [{plan_id, session_id, goal, revision, step_count, risks,
  created_at}]}` ordered by `created_at` desc; `risks` is the sorted set of the
  plan's step risks.
- New DB read helpers (RULES 10): `list_sessions(db, limit)` and
  `list_pending_plans(db, limit)` in `src/db/session.py`; `list_audit_entries(db,
  *, limit, offset)`, `count_audit_entries(db)`, `all_audit_entries(db)` in new
  `src/db/audit_store.py`.
- New frontend lib modules: `lib/sessions-api.ts`, `lib/tools-api.ts`,
  `lib/audit-api.ts`, `lib/overview-api.ts`, `lib/approvals-api.ts`; new types
  (`SessionSummary`, `ToolInfo`, `AuditEntry`, `Overview`, `PendingApproval`).
- `frontend/lib/chat-api.ts`: add `setSessionId(id: string)` and
  `newSessionId(): string` exports; keep `getSessionId()`.

## 5. Parts

### Part 1 — Session list + in-chat switcher (D1, D6)

Backend:

- `src/db/session.py`: add
  `async def list_sessions(db, limit: int = 100) -> list[dict]`. Group `Message`
  by `session_id` with `func.count()` / `func.max(Message.created_at)` filtered
  to `Message.role == "user"`, order by `max` desc, limit; then one follow-up
  query over the returned ids to fetch the first and last user message content
  for `title`/`preview` (truncate to 120 chars).
- `src/api/sessions.py`: add `GET /api/sessions` with the standard admin guard
  and `@limiter.limit("60 per 60 seconds")`; call `list_sessions` and serialize.

Frontend:

- `frontend/lib/chat-api.ts`: add `setSessionId(id)` (write
  `loomcore_session_id`) and `newSessionId()` (generate `sess_<32 hex>`, persist,
  return); keep `getSessionId()`.
- `frontend/lib/sessions-api.ts`: `SessionSummary` type + `fetchSessions()` over
  `GET /api/sessions` via `requestJson`.
- `frontend/lib/use-chat.ts`: `useChat(initialSessionId?: string)`. Replace the
  `sessionId` ref with `useState` initialized to
  `initialSessionId ?? getSessionId()`. The WS connect effect and the
  history-hydrate effect depend on `sessionId` and reset messages/plan state on
  change. Add `selectSession(id)` (persist via `setSessionId` + switch; no-op
  while `isThinking || isSending`), `createSession()` (new id + switch + empty
  feed), and refresh the session list when a turn ends and after
  `clearConversation`.
- `frontend/components/session-list.tsx` (client): the rail — a **New chat**
  button, rows (title, preview, relative time, active highlight), a per-row
  delete (confirm with `ui/alert-dialog`, then
  `DELETE /api/sessions/{id}/messages`), empty/loading states; hidden on small
  screens.
- `frontend/components/chat-room.tsx`: render `<SessionList/>` as a left column
  beside the existing feed + composer; chat behaviour otherwise unchanged.
- `frontend/app/(dashboard)/page.tsx`: accept `searchParams` (server) and pass
  `sessionId` (the `?session=` value) as `initialSessionId` to `ChatRoom`.

### Part 2 — Tools catalog page (D2)

- `frontend/lib/tools-api.ts`: `ToolInfo` (`name`, `description`, `inputSchema`,
  `risk?`, `report?`) + `fetchTools()` over the existing `GET /api/tools`.
- `frontend/components/tools-view.tsx` (client): title/count, Refresh, a search
  box (name/description), a risk filter (safe/moderate/dangerous), grouping by
  tool-name prefix (`core_` / `ops_` / `wg_`), each row expandable to a property
  table from `inputSchema`. Read-only — no invocation control (RULES 12).
- `frontend/app/(dashboard)/tools/page.tsx` →
  `<AdminGate><DashboardShell><ToolsView/></DashboardShell></AdminGate>` (mirror
  `plugins/page.tsx`).
- `frontend/components/dashboard-sidebar.tsx`: add
  `{label:"Tools", href:"/tools", icon: Wrench}`.

### Part 3 — Audit-log viewer (D3)

- `src/db/audit_store.py` (new): `list_audit_entries(db, *, limit, offset)`
  (desc), `count_audit_entries(db)`, `all_audit_entries(db)` (asc, for
  verification).
- `src/api/audit.py` (new): `GET /api/audit` (paged list + `total`) and
  `GET /api/audit/verify` (`verify_chain(all_audit_entries(...))` →
  `{valid, count, head}`); admin-guarded, `60 per 60 seconds`.
- `src/app.py`: register the new router in `create_app()`.
- `frontend/lib/audit-api.ts`: `AuditEntry` type + `fetchAudit({limit, offset})`
  + `verifyAudit()`.
- `frontend/components/audit-view.tsx` (client): a table (time, tool, risk badge,
  status badge, actor, session link, arguments preview), client-side
  tool/status/risk filters, Prev/Next paging via `offset`, a **Verify chain**
  button reporting valid/invalid + count, error/empty states.
- `frontend/app/(dashboard)/audit/page.tsx`; sidebar entry
  `{label:"Audit log", href:"/audit", icon: ScrollText}`.

### Part 4 — Host overview dashboard (D4)

- `src/api/overview.py` (new): `GET /api/overview`. A module-level curated map
  `_SECTION_TOOLS = {"core": "core_health", "disk": "ops_disk_usage",
  "metrics": "ops_system_metrics", "failed_units": "ops_failed_units",
  "wireguard": "wg_list_interfaces"}`. Dispatch `core_*` via
  `src.core.core_tools.dispatch_core_tool` and plugin tools via
  `plugin_manager.execute_tool_by_name`, each inside a per-section
  `try/except` → `null`; `plugins` always from `get_plugin_status`. Admin-guarded,
  `60 per 60 seconds`.
- `src/app.py`: register the new router.
- `frontend/lib/overview-api.ts`: `Overview` type + `fetchOverview()`.
- `frontend/components/overview-view.tsx` (client): a card grid (Core, Plugins,
  Disk, Metrics, WireGuard, Failed units); a `null` section renders as
  "unavailable"; react-query `refetchInterval: 15_000`; skeletons, error banner,
  Refresh.
- `frontend/app/(dashboard)/overview/page.tsx`; in the sidebar replace the
  `{/* System Status — hidden until functional */}` comment with
  `{label:"Overview", href:"/overview", icon: LayoutDashboard}`.

### Part 5 — Approvals inbox, read-only (D5, D6)

- `src/db/session.py`: add `async def list_pending_plans(db, limit: int = 100)`.
  Query `Message` where `role == "plan"` and the JSONB status is `"proposed"`
  (`Message.tool_calls[0]["status"].as_string() == "proposed"`), order
  `created_at` desc, limit; dedupe by `plan_id` keeping the newest; derive
  `{plan_id, session_id, goal (content), revision, step_count, risks, created_at}`.
- `src/api/approvals.py` (new): `GET /api/approvals` serializing that list;
  admin-guarded, `60 per 60 seconds`.
- `src/app.py`: register the new router.
- `frontend/lib/approvals-api.ts`: `PendingApproval` type + `fetchApprovals()`.
- `frontend/components/approvals-view.tsx` (client): rows (goal, session, age,
  step count, risk chips with a "dangerous" warning), an **Open in chat** link to
  `/?session=<session_id>`, an explicit "read-only — approve in chat" note, empty
  state, 20s poll. No approve/reject controls.
- `frontend/app/(dashboard)/approvals/page.tsx`; sidebar entry
  `{label:"Approvals", href:"/approvals", icon: Inbox}`.

## 6. Tests

Backend (pytest; follow `tests/test_history_api.py`):

- `tests/test_sessions_api.py`: `GET /api/sessions` returns the serialized
  helper output; `list_sessions` unit-tested over a fake session.
- `tests/test_audit_api.py`: `GET /api/audit` paging shape + `total`;
  `GET /api/audit/verify` returns `valid=True` for a valid in-memory chain and
  `valid=False` for a tampered one (reuse `tests/test_audit.py` builders).
- `tests/test_overview_api.py`: each section is populated when its tool succeeds
  and `null` when it raises; `plugins` always present; never 500.
- `tests/test_approvals_api.py`: `list_pending_plans` keeps only the newest
  `proposed` revision per `plan_id` and drops terminal plans; the route
  serializes it.
- `tests/test_db_session.py`: extend for `list_sessions` / `list_pending_plans`
  if the fake-session pattern is already used there.

Frontend: no test runner — verification is `npx tsc --noEmit` + `npm run build`.

Exact gates: `uv run ruff check .`; `uv run pytest -m "not integration and not
e2e" -q`; then in `frontend/`: `npx tsc --noEmit` and `npm run build`.

## 7. Acceptance criteria

1. `GET /api/sessions` lists sessions newest-first with title/preview/count/time;
   the in-chat rail renders them, switching reconnects the WS and re-hydrates
   history, **New chat** creates and selects a fresh session, and switching is
   blocked mid-turn.
2. `/tools` renders every tool from `GET /api/tools` with risk and a searchable
   description and an expandable schema; the sidebar entry navigates and
   reflects active state.
3. `/audit` lists entries with filters and paging and a **Verify chain** action
   that reports validity and count; both audit routes are admin-guarded and
   rate-limited.
4. `/overview` shows core/plugin/disk/metrics/WireGuard/failed-units cards; a
   disabled or missing plugin renders its card as unavailable and never 500s the
   endpoint.
5. `/approvals` lists only plans with status `proposed` (newest revision per
   plan), and **Open in chat** deep-links to the right session via `?session=`.
6. No new mutating REST route; no WS/protocol change; no DB migration; the
   `frontend/AGENTS.md` block is unchanged.
7. Gates green: `uv run ruff check .`; `uv run pytest -m "not integration and not
   e2e"`; `npx tsc --noEmit`; `npm run build`.
8. Manual smoke with plugins enabled: create two sessions and switch between
   them; open `/tools`, `/audit` (verify chain), `/overview`, and `/approvals`
   (the deep link lands in the right conversation).

## 8. Commits

Suggested, one per part so any can be reverted alone (the coordinator commits
after acceptance only, RULES 0a):

- `docs(tasks): add frontend gaps spec`
- `feat(api): add session listing and the in-chat session switcher`
- `feat(frontend): add the tools catalog page`
- `feat(api): add audit-log read and chain-verify endpoints with a viewer`
- `feat(api): add a read-only host overview endpoint and dashboard`
- `feat(api): add a read-only approvals inbox with a chat deep link`

Add a dated `CHANGELOG.md` entry (RULES 14).

## 9. Risks

- **Approvals inbox is partial.** Direct, single-step dangerous confirmations
  are transient (per-connection `pending`, not persisted) and therefore absent;
  only multi-step `plan` rows with status `proposed` appear. Mitigation:
  documented scope + a UI note; persisting direct confirmations is a future task.
- **Stale proposals.** A proposal the user never answers stays `proposed`
  forever, so the inbox can show old entries. Mitigation: show age; no
  auto-expiry in v1.
- **Switch mid-flight.** Switching sessions while a turn streams would orphan
  the stream. Mitigation: switch/new is disabled while `isThinking || isSending`.
- **JSONB query portability.** `list_pending_plans` uses Postgres JSONB
  operators (the project targets Postgres; `Message.tool_calls` is `JSONB`);
  DB-backed tests stay behind the integration marker.
- **Audit verify cost.** `GET /api/audit/verify` loads the whole chain (O(n)) on
  demand; it is admin-only, rate-limited, and not polled.
- **Truncation is not detected** by `verify_chain` without an anchor (documented
  in `src/core/audit.py`); the endpoint reports tamper-evidence only.
- **Overview hardcodes a curated tool list**; a renamed/removed tool turns its
  section `null` (graceful). Keep the list small and documented.
- **Rate limits.** New pages poll; keep intervals ≥15–30s to stay within the
  60/min route limits shared with the other admin pages.
- **`?session=` deep link.** An invalid or empty id must fall back to selecting
  or creating a session rather than leaving an empty stuck rail.
