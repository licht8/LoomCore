# REPORT_RU.md — LLM usage tracking and dashboard

Task spec: `docs/tasks/llm-usage/TZ_RU.md`.

## Goal

Persist every completed LLM call (success or terminal failure) with tokens and
latency, and expose an admin-only Usage dashboard. No cost estimation, no change
to provider-call behaviour, no change to the in-memory daily budget semantics.

## Delivered

### D1 — capture at the single choke point (`src/llm/client.py`)

- `OpenAICompatClient.chat_completion` gained keyword-only
  `purpose: str = "chat"` and `session_id: str | None = None`.
- On a successful completion it records one row (model/provider/base_url,
  purpose, session, prompt/completion/total tokens from `usage`, wall-clock
  `latency_ms`, `attempts`, `status="success"`).
- On a terminal failure it records one row with `status="error"`, null tokens and
  null latency: the non-retryable HTTP error path (`LLM API error <status>`) and
  the exhausted-retry path after the loop (`LLM request failed` / timeout).
- A budget rejection (`LlmBudgetExceeded`) makes no provider call and records
  nothing.
- `src/llm/router.py`: `route()` passes `purpose="planner", session_id=session_id`;
  `summarize_plan()` passes `purpose="summary"` (no session id -> null).

### D2 — `llm_usage` table + migration `008`

- `src/db/models.py` adds `LlmUsage` with String timestamps (repo convention) and
  **no foreign keys** so rows survive session deletion (mirrors `audit_log`).
  Columns: `id`, `created_at` (indexed), `provider`, `base_url`, `model`
  (indexed), `purpose`, `session_id`, `prompt_tokens`, `completion_tokens`,
  `total_tokens`, `latency_ms`, `attempts`, `status`, `error` (all token/latency
  columns nullable).
- `alembic/versions/008_llm_usage.py` (`revision="008"`, `down_revision="007"`)
  creates the table and both indexes; `downgrade()` drops them. `008` is now the
  Alembic head.

### D3 — recording helpers

- `src/db/session.py`: `add_llm_usage(db, ...)` writes one row; read helpers
  `llm_usage_totals`, `llm_usage_by_model`, `llm_usage_by_day` (each takes
  `since_iso`; day grouping via `func.substr(created_at, 1, 10)`).
- `src/llm/usage.py` (new): `record_llm_usage(...)` opens its own short
  `async_session_factory` session and swallows every failure with a
  `loomcore.llm.usage` warning, so recording can never fail the request it
  measures.

### D4 — `GET /api/llm/usage`

- `src/api/llm.py`: admin-only `GET /api/llm/usage?days=7`
  (`@limiter.limit("60 per 60 seconds")`, `days` bounded `1..90` via
  `Query`). Returns the snake_case shape `{days, since, until, totals,
  by_model, by_day, budget}`; `budget` comes from `src.llm.budget.llm_budget_status()`.
- `src/llm/usage.py`: pure `shape_usage_summary(...)` assembles the response.
  Token sums are `0` for an empty window and `None` when the window has calls but
  no usage samples (so the UI can show `—`); `avg_latency_ms` is whole ms.

### D5 — frontend `/usage`

- `frontend/lib/llm-usage-api.ts` — `fetchLlmUsage(days)` + types, reusing the
  shared `requestJson` (auth headers, `credentials: "include"`, `readError`).
- `frontend/components/usage-view.tsx` — React Query
  (`["llm-usage", days]`, 60s refetch), 1/7/30-day range `Select`, refresh
  `Button`, totals `Card`s (calls / tokens / avg latency / today's budget),
  per-model table (`Badge` for the provider), per-day list; `—` for null values.
- `frontend/app/(dashboard)/usage/page.tsx` renders `<UsageView />`.
- `frontend/components/dashboard-sidebar.tsx` adds the `Usage` nav item
  (`BarChart3`).

### D6 — tests

- New `tests/test_llm_usage.py`: shaper empty window (totals zeros, empty lists),
  shaper with data incl. null tokens + avg rounding, and `record_llm_usage`
  swallowing a writer error (warns, does not raise).
- `tests/test_llm_client.py`: autouse fixture stubs `client_module.record_llm_usage`
  (keeps the suite DB-free); new tests assert the success payload
  (purpose/session/model/provider/status/attempts) and the error-path call.
- `tests/test_router.py`: widened the `patched_router` fake signature with
  `purpose`/`session_id`; new test asserts `route()` passes
  `purpose="planner"` and the session id.
- `tests/test_api_llm.py`: usage endpoint auth (401), `days` bounds (0/91 -> 422)
  and response shape (monkeypatched `llm_api.llm_usage_summary`).

### Docs

- `CHANGELOG.md`: a dated 2026-09-22 entry for LLM usage tracking. `README.md`
  has no API listing, so it was left unchanged (per the task spec).

## Verification

- `uv run ruff check .` — clean.
- `uv run pytest -m "not integration and not e2e" -q` — 1053 passed, 12 skipped,
  5 deselected (`tmp/tests_llm_usage_full.log`).
- `cd frontend; npx tsc --noEmit` — clean.
- `npm run build` — succeeded; the `/usage` route is in the build output
  (`tmp/frontend_build_llm_usage.log`).
- `uv run alembic heads` -> `008 (head)`; `uv run alembic history` shows
  `007 -> 008 (head), llm usage`.

## Changed files

- `src/db/models.py`, `src/db/session.py`
- `alembic/versions/008_llm_usage.py` (new)
- `src/llm/usage.py` (new), `src/llm/client.py`, `src/llm/router.py`
- `src/api/llm.py`
- `frontend/lib/api-client.ts` (new), `frontend/lib/llm-settings-api.ts`
- `frontend/lib/llm-usage-api.ts` (new), `frontend/components/usage-view.tsx` (new),
  `frontend/app/(dashboard)/usage/page.tsx` (new),
  `frontend/components/dashboard-sidebar.tsx`
- `tests/test_llm_usage.py` (new), `tests/test_llm_client.py`,
  `tests/test_router.py`, `tests/test_api_llm.py`
- `CHANGELOG.md`

## Commits

None (executor does not commit; the coordinator commits after acceptance).

## Notes and follow-ups

- `alembic upgrade head` against a live Postgres was not run in this environment
  (integration/e2e needs a database and is out of scope for the fast suite); the
  revision chain and head were validated with `alembic heads`/`history`.
- Migration `008` must run before the hub starts; `deploy/install.sh` already
  migrates before `docker compose up -d --wait` (foundation laid by the
  `db-prepared-statement-hardening` task).
- One extra short DB write per LLM call, fully caught; retention is out of scope
  and growth is bounded by the daily call budget.
- Summary rows have `session_id = NULL` by design; `created_at` is an ISO string,
  so day grouping is an exact `substr`.

## Review fix round 1 (frontend)

Findings from the frontend review, all applied:

1. **Duplicated request plumbing** — extracted the shared `requestHeaders` +
   `requestJson` (auth header merge, `credentials: "include"`, empty-body and
   invalid-JSON handling) into a new `frontend/lib/api-client.ts`; both
   `frontend/lib/llm-settings-api.ts` and `frontend/lib/llm-usage-api.ts` now
   import it instead of carrying their own copy.
2. **Dead `value === undefined` checks** — `formatCount`/`formatLatency` in
   `frontend/components/usage-view.tsx` now check only `value === null` (the
   declared parameter is `number | null`).
3. **Select `onValueChange` guard** — kept a live guard (`if (value) ...`) rather
   than dropping it: Base UI types the value as `string | null`, so an
   unconditional `Number(value)` would turn a null selection into `0`. (The
   finding assumed Radix's `(value: string) => void` signature.)
4. **Missing `items` on the Base UI `Select`** — added `items={RANGES}` so
   `<Select.Value />` renders the label (`Last 7 days`) instead of the raw value
   (`7`), matching the convention in `settings-view.tsx`.

Verification after the fixes: `npx tsc --noEmit` clean; `npm run build`
succeeded with the `/usage` route (`tmp/frontend_build_llm_usage_review1.log`).
No Python changed in this round.
