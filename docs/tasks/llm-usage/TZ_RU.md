# TZ_RU.md — LLM usage tracking & dashboard

Task id: `llm-usage`. Follow RULES.md (English only, no comments unless existing
style has them, ruff clean, tests mandatory). Executor does NOT commit; the
coordinator commits after acceptance (RULES 0a). Core infrastructure, so it stays
in `src/` (not a plugin).

## 1. Goal

Record what the configured LLM providers actually consume and surface it in the
dashboard. Today `OpenAICompatClient` parses the provider `usage` block and logs
it (`src/llm/client.py:170-188`) but nothing is persisted; `src/llm/budget.py`
counts calls in memory only. After this task, every completed LLM call is a
durable row, and an **admin Usage view** shows calls, tokens, and latency per
model and per day.

Scope locked with the owner:

- **Usage tracking & dashboard** (not per-role model selection).
- **DB table + Alembic migration** (durable, group-by queries).
- **Tokens & latency only** — no cost/pricing.

## 2. Non-goals

- No cost estimation or price table.
- No change to the daily budget's in-memory semantics (the endpoint only
  *reports* `llm_budget_status()`; durability is a separate task).
- No per-session or per-plugin analytics, no charts library, no CSV export.
- No `audit_log` changes, no streaming, no provider-call behaviour change.
- No changes to the provider settings API/UI.

## 3. Approved decisions (locked)

### D1 — capture at the single choke point
`OpenAICompatClient.chat_completion` is the only place that knows the resolved
provider, the actual model (incl. the `summary_model` override), the HTTP
attempt count, and the latency. It calls a new fail-safe
`record_llm_usage(...)`. Recording must never affect the request: any DB/plumbing
error is caught and logged as a warning.

Add two keyword-only params:
```python
async def chat_completion(self, ..., purpose: str = "chat",
                          session_id: str | None = None) -> dict:
```
`router.route` passes `purpose="planner", session_id=session_id`;
`router.summarize_plan` passes `purpose="summary"` (its signature gains no
session id — summary rows have `session_id=None`).

### D2 — `llm_usage` table (migration 008)
`src/db/models.py` gains `LlmUsage` with String timestamps (repo convention) and
**no foreign keys** so rows survive session deletion (mirrors `audit_log`):

| column | type | notes |
|---|---|---|
| `id` | `String(36)` PK | uuid4 default |
| `created_at` | `String(32)` not null, index | UTC isoformat |
| `provider` | `String(64)` not null default `""` | preset id, may be empty (env/custom) |
| `base_url` | `String(256)` not null default `""` | disambiguates custom endpoints |
| `model` | `String(256)` not null default `""` | actual model used |
| `purpose` | `String(32)` not null default `"chat"` | `planner` / `summary` / `chat` |
| `session_id` | `String(64)` nullable | null for summaries |
| `prompt_tokens` | `Integer` nullable | null when the provider omits usage |
| `completion_tokens` | `Integer` nullable | |
| `total_tokens` | `Integer` nullable | |
| `latency_ms` | `Integer` nullable | wall-clock of the successful attempt |
| `attempts` | `Integer` not null default 1 | HTTP attempts incl. retries |
| `status` | `String(16)` not null default `"success"` | `success` / `error` |
| `error` | `Text` nullable | short reason on `status="error"` |

Indexes: `created_at`, and `model` (both used by the aggregations).
`alembic/versions/008_llm_usage.py`, `revision="008"`, `down_revision="007"`,
matching the existing migration style; `downgrade()` drops the table.

### D3 — record successes and terminal failures, skip budget blocks
One row per **successful completion** (with tokens/latency) and one per
**terminal failure** (`status="error"`, tokens null, `error` set) — i.e. the
non-retryable HTTP error and the exhausted-retry path in
`chat_completion`. A `LlmBudgetExceeded` makes no provider call, so nothing is
recorded. Tokens are `null` when the provider returns no `usage` block.

### D4 — aggregation API (admin)
`src/api/llm.py` gains:

`GET /api/llm/usage?days=7` — `@limiter.limit("60 per 60 seconds")`, admin via
the same `require_admin_token(extract_admin_token(...), request)` guard, plain
dict response. `days` is `int >= 1 and <= 90` (FastAPI `Query`), default 7.
Response (snake_case, mirroring existing routes):
```json
{
  "days": 7,
  "since": "2026-09-16T...",
  "until": "2026-09-22T...",
  "totals": {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
             "total_tokens": 0, "avg_latency_ms": null},
  "by_model": [{"provider": "openai", "model": "gpt-4o-mini", "calls": 1,
                "prompt_tokens": 10, "completion_tokens": 5,
                "total_tokens": 15, "avg_latency_ms": 420}],
  "by_day": [{"day": "2026-09-22", "calls": 1, "prompt_tokens": 10,
              "completion_tokens": 5, "total_tokens": 15}],
  "budget": {"day": "2026-09-22", "used": 1, "limit": 1000, "remaining": 999}
}
```
Empty-safe for a fresh DB (`totals` zeros, empty lists). `budget` comes from
`src.llm.budget.llm_budget_status()`.

### D5 — UI: a new `/usage` route
A dedicated dashboard page (not a Settings section), matching the plugins view:

- `frontend/lib/llm-usage-api.ts` — `fetchLlmUsage(days)` + types, using
  `getApiBase()` + `authHeaders()` + `credentials: "include"` and the shared
  `readError` (`@/lib/http-error`).
- `frontend/components/usage-view.tsx` — React Query
  (`useQuery(["llm-usage", days], ...)`, `refetchInterval` 60s), a range selector
  (1 / 7 / 30 days) using the existing `Select`, totals cards
  (calls / tokens / avg latency / today's budget), a per-model table, and a
  per-day list. Reuse `Card`, `Badge`, `Button`, `Select`; show `—` for null
  tokens; no new dependency or chart library.
- `frontend/app/(dashboard)/usage/page.tsx` — renders `<UsageView />` (same
  5-line pattern as `plugins/page.tsx`); `AdminGate` protects it automatically.
- `frontend/components/dashboard-sidebar.tsx` — add
  `{ label: "Usage", href: "/usage", icon: BarChart3 }` to `navItems` (the
  existing exact-match active style already fits `/usage`).

### D6 — test seams (no DB in the fast suite)
- New `tests/test_llm_usage.py`: unit-test the **pure** response-shaper in
  `src/llm/usage.py` (rows in -> payload out, incl. empty/None-token cases) and
  that `record_llm_usage` swallows errors (monkeypatched writer raising).
- `tests/test_llm_client.py`: add an **autouse** fixture that monkeypatches
  `client_module.record_llm_usage` with an `AsyncMock` (so no test touches the
  DB), then assert the success payload (model/provider/tokens/latency/purpose/
  session) and the error-path call. The existing 15 tests keep passing.
- `tests/test_router.py`: widen the `patched_router` fake signature
  (`fake_chat_completion(..., session_id=None, purpose=None)`) so the router's
  new kwargs don't raise; optionally assert it receives `purpose="planner"`.
- `tests/test_api_llm.py`: monkeypatch `llm_api.llm_usage_summary` and assert
  the endpoint's auth, `days` validation (0 and 91 -> 422), and shape.

## 4. Data model / interfaces

Covered by D2 (table) and D4 (endpoint). No request/response model changes for
existing routes; no wire/event changes.

## 5. Parts

1. `src/db/models.py` — `LlmUsage`.
2. `alembic/versions/008_llm_usage.py` — create/drop table + indexes.
3. `src/db/session.py` — `add_llm_usage(db, ...)` and read helpers
   (`llm_usage_totals`, `llm_usage_by_model`, `llm_usage_by_day`, each taking a
   `since_iso` string; day grouping via `func.substr(created_at, 1, 10)`).
4. `src/llm/usage.py` (new) — `record_llm_usage(...)` (own
   `async_session_factory`, try/except -> warning), `llm_usage_summary(days)`,
   and the pure shaper `shape_usage_summary(...)`.
5. `src/llm/client.py` — `purpose`/`session_id` kwargs; record on success and on
   terminal failure.
6. `src/llm/router.py` — pass `purpose`/`session_id` at both call sites.
7. `src/api/llm.py` — `GET /api/llm/usage`.
8. Frontend: `frontend/lib/llm-usage-api.ts`,
   `frontend/components/usage-view.tsx`,
   `frontend/app/(dashboard)/usage/page.tsx`,
   `frontend/components/dashboard-sidebar.tsx`.
9. Docs: `CHANGELOG.md` (short dated entry), `README.md` if it lists the API.

## 6. Tests

- `tests/test_llm_usage.py` (new): shaper pure cases; `record_llm_usage`
  fail-safe.
- `tests/test_llm_client.py` (edit): autouse no-DB fixture + success/error
  recording assertions.
- `tests/test_router.py` (edit): fake signature accepts the new kwargs.
- `tests/test_api_llm.py` (edit): `/api/llm/usage` auth, `days` bounds, shape.
- No fast-suite test requires Postgres; the table is exercised by
  `alembic upgrade head` + an integration check only.

Gates:
```
ruff check .
pytest -m "not integration and not e2e"
cd frontend; npx tsc --noEmit
```

## 7. Acceptance criteria

- A chat turn and a plan summary each leave a `llm_usage` row with the correct
  model, provider, tokens, latency, `purpose`, and `session_id`.
- A provider that omits `usage` still records a row with null tokens; a terminal
  LLM failure records a row with `status="error"`; a budget block records
  nothing.
- `GET /api/llm/usage?days=7` is admin-only, rejects `days` outside 1..90, and
  returns zeros/empty lists on a fresh DB.
- The `/usage` page renders totals, per-model, and per-day data, and is reachable
  from the sidebar.
- Any failure in usage recording never fails the chat request (logged warning).
- `ruff check .` clean; fast suite green; `npx tsc --noEmit` clean;
  `alembic upgrade head` applies 008 cleanly.

## 8. Commits (coordinator commits only after acceptance, RULES 0a)

1. `feat(llm): record per-call usage in a new llm_usage table`
2. `feat(api): expose the LLM usage aggregation endpoint`
3. `feat(frontend): add the Usage dashboard page`
4. `docs: document LLM usage tracking`

## 9. Risks

- **One extra DB write per LLM call.** Mitigated: its own short session, fully
  caught; the request path is never blocked on it.
- **`created_at` is a string.** Range filter and day grouping rely on ISO
  ordering/`substr`, which is exact here; documented in the shaper.
- **Migration ordering.** 008 must run before the hub starts; `deploy/install.sh`
  already migrates before `docker compose up -d --wait` (the DB-prepared-statement
  fix), so no stale-plan exposure.
- **Missing `usage` from some gateways.** Tokens are nullable and the UI shows
  `—`; calls/latency are always present.
- **Summary rows have no session id.** `session_id` is nullable by design.
- **Unbounded growth.** The table grows one row per call; the daily budget
  (default 1000) bounds it, and retention/pruning is out of scope for now.
