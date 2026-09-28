# TZ_RU.md — overview resilience and session metadata

> Copy to `docs/tasks/<kebab-name>/TZ_RU.md` and fill in **before** starting
> (RULES 16). Content is English (RULES 0). Keep it specific and testable.

## 1. Goal

Close the two remaining medium audit findings:

- **`/api/overview` can hang and churn plugin restarts.** `GET /api/overview`
  (`src/api/overview.py:41-67`) awaits five tools **sequentially**, and a plugin
  tool call goes through `execute_tool_by_name` -> `execute_tool`
  (`src/plugins/manager.py:1064-1123`), which **auto-restarts** a dead plugin
  (`_restart_plugin`, `manager.py:512`) with exponential backoff (base 1s, max
  30s — `manager.py:124-125`) *inside the request*. The overview page polls
  every 15s (`frontend/components/overview-view.tsx:61`), so one broken plugin
  makes the endpoint slow and restarts repeatedly.
- **`sessions` has no first-class metadata.** `Session` has only
  `id`/`user_id`/`status` (`src/db/models.py:38-53`); the session list is
  reconstructed from messages with timestamp-equality joins
  (`src/db/session.py:191-252`) that can drop rows when two user messages share a
  microsecond (`title`/`preview` collapse). There is no `created_at`,
  `updated_at` or `title` to sort/title a conversation.

## 2. Non-goals

- No frontend change: the `/api/overview` and `GET /api/sessions` response shapes
  are unchanged, so the 15s poll and the chats rail keep working.
- Do not drop `Session.status` / `User.role` (kept as reserved; a separate task).
- No new API endpoint, no per-user model (single-tenant stays).
- No change to WS/chat behavior.

## 3. Approved decisions (locked)

### D1 — `/api/overview` is bounded and restart-free

- `src/api/overview.py`:
  - Add `OVERVIEW_TOOL_TIMEOUT_SECONDS = 5.0`.
  - `_run_tool` wraps the dispatch in
    `await asyncio.wait_for(..., OVERVIEW_TOOL_TIMEOUT_SECONDS)` and returns
    `None` on `TimeoutError`/any exception (keep the warning log).
  - Before a plugin call, gate on health:
    `plugin = plugin_manager.plugin_for_tool(tool_name)`; if not `plugin` or
    `not plugin_manager.is_healthy(plugin)` -> return `None` **without
    dispatching**, so a poll never triggers `_restart_plugin` backoff (the
    background healthcheck loop owns restarts). `core_*` tools keep dispatching
    directly (`dispatch_core_tool`).
  - Run the five sections **concurrently**:
    `asyncio.gather(*(_run_tool(name) for name in _SECTION_TOOLS.values()))` and
    unpack in `_SECTION_TOOLS` order. The per-plugin reentrant lock
    (`manager.py:80-108`) keeps concurrent same-plugin calls correct (they
    serialize); concurrency mainly removes the LoomOps-vs-LoomWG serial wait.
- Net effect: worst-case latency for the endpoint drops from `sum(tools)` to
  `<= 5s`, and a dead plugin yields `null` instead of a restart storm. Response
  shape stays `{core, plugins, host:{disk,metrics,failed_units}, wireguard}`.

### D2 — first-class session metadata

- `src/db/models.py` `Session` gains:
  - `created_at: Mapped[str] = mapped_column(String, nullable=False)`
  - `updated_at: Mapped[str] = mapped_column(String, nullable=False)`
  - `title: Mapped[str | None] = mapped_column(String(120), nullable=True)`
  (UTC ISO strings, matching `Message.created_at` style.)
  Also fix the stale `Message.role` comment to include `'plan'` (`models.py:66`).
- `src/db/session.py`:
  - `ensure_session` creates the `Session` with `created_at`/`updated_at` = now.
  - `add_message` touches the owning session in the same transaction:
    always set `updated_at = now`; when `role == "user"` and the title is
    NULL/empty, set `title = _truncate(content)` — via
    `func.coalesce(func.nullif(Session.title, ""), _truncate(content))` so only
    the first user message wins. A missing session row is a no-op (UPDATE
    matches zero rows).
  - `clear_session_history` also bumps `updated_at`.
  - `list_sessions` is rewritten against the `sessions` table (no
    timestamp-equality joins):
    1. a `message_count` subquery (`role == "user"`, grouped by session);
    2. a `last_user` subquery using
       `func.row_number().over(partition_by=Message.session_id,
       order_by=(Message.created_at.desc(), Message.id.desc())) == 1` to pick one
       deterministic last user row per session;
    3. select `Session.id, Session.title, Session.updated_at`, join both
       subqueries, `order_by(Session.updated_at.desc())`, `limit(limit)`.
    Return the same keys: `session_id`, `title` (stored title, else the preview),
    `preview` (last user body, `_truncate` 120), `message_count`,
    `last_message_at`. Sessions with no user message stay excluded (inner join).
- Migration `alembic/versions/010_session_metadata.py` (`revision="010"`,
  `down_revision="009"`): add the three columns nullable, backfill from
  `messages` (`created_at = min(message.created_at)`, `updated_at =
  max(message.created_at)`, `title = left(first user content, 120)`), fall back
  to `now()` for message-less sessions, then `alter_column(..., nullable=False)`
  for `created_at`/`updated_at`; `downgrade` drops the three columns.

## 4. Data model / interfaces

- `sessions` gains `created_at` (not null), `updated_at` (not null), `title`
  (nullable, <= 120). `GET /api/sessions` and `GET /api/overview` payloads are
  unchanged.
- New module constant `OVERVIEW_TOOL_TIMEOUT_SECONDS` (no env knob in v1).

## 5. Parts

1. `src/api/overview.py` — bounded, restart-free, concurrent sections.
2. `src/db/models.py` — `Session` metadata + `Message.role` comment.
3. `src/db/session.py` — `ensure_session`, `add_message`, `clear_session_history`,
   `list_sessions`.
4. `alembic/versions/010_session_metadata.py` — schema + backfill.

## 6. Tests

- `tests/test_db_session.py`:
  - `ensure_session` sets `created_at`/`updated_at`.
  - the first user message sets `title`; a later user message does not overwrite
    it; `updated_at` advances on every message.
  - `list_sessions` returns stored `title`/`preview`/`message_count` ordered by
    `updated_at` desc; a session with only assistant messages is excluded; two
    user messages with the **same** `created_at` both survive (no equality join).
- `tests/test_api_overview.py`:
  - a slow tool (patched `execute_tool_by_name` sleeping > timeout) yields
    `null` and the endpoint still returns.
  - an unhealthy plugin (`plugin_manager.is_healthy` -> False) is not dispatched
    (`execute_tool_by_name` uncalled) and yields `null`.
  - keep the populated and never-raises cases green.
- `tests/test_db_models.py`: assert the three `sessions` columns exist.
- Gates: `uv run ruff check .`; `uv run pytest -m "not integration and not e2e" -q`.
  Frontend untouched (still `npx tsc --noEmit` + `npm run test` are green).

## 7. Acceptance criteria

- A tool that never returns cannot hold `/api/overview` longer than
  `OVERVIEW_TOOL_TIMEOUT_SECONDS`; its section is `null` and the request
  finishes.
- With an unhealthy plugin, `/api/overview` performs no plugin tool call for that
  section (no restart/backoff triggered by the poll).
- Two user messages sharing an exact `created_at` still both count and the
  newest one is the preview (no dropped session/title).
- `GET /api/sessions` still returns the documented fields; existing frontend
  tests/build pass unchanged.
- `uv run ruff check .` clean; `uv run pytest -m "not integration and not e2e" -q`
  green.

## 8. Commits

1. `perf(api): bound and parallelize the overview sections`
2. `feat(db): add session created_at/updated_at/title and rewrite the session list`
3. `chore(db): migration 010 backfills session metadata` (may be folded into 2)

## 9. Risks

- The session-list ordering changes from "last user message" to
  "last activity (`updated_at`, any role)". Accepted: it matches what a chats
  rail should show; recorded here.
- Concurrent overview dispatch relies on `execute_tool`'s per-plugin lock for
  correctness; the timeout cancels waiters without corrupting state.
- Migration 010 backfills from `messages`; a session whose messages were cleared
  gets `now()`-based timestamps and a NULL title (falls back to the preview at
  read time).
- `updated_at` writes add one UPDATE per appended message (a cheap indexed
  primary-key update) inside the existing transaction.
