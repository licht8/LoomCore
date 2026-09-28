# TZ_RU.md — docs / tests / CI hygiene

> Copy to `docs/tasks/<kebab-name>/TZ_RU.md` and fill in **before** starting
> (RULES 16). Content is English (RULES 0). Keep it specific and testable.

## 1. Goal

A fast, low-risk PR that closes the audit's documentation, frontend-testing and
CI-coverage gaps. No runtime behavior changes.

- **Docs drift**: several guides and rules reference removed tools/arguments and
  behavior that no longer matches the code.
- **Frontend tests**: there is no test runner and no test for the two most
  bug-prone pure modules (`frontend/lib/chat-ws.ts`, `frontend/lib/chat-hydrate.ts`).
- **CI gap**: the integration job runs only `tests/test_smoke_integration.py`, so
  the two `@pytest.mark.integration` rate-limit tests in `tests/test_rate_limit.py`
  never execute in CI.

## 2. Non-goals

- No backend code, API, DB, WS or plugin behavior change.
- No LoomOps e2e suite (deferred; separate future task).
- No React component/DOM tests — only the pure reducers/hydration helpers.
- No change to the existing fast gate (`pytest -m "not integration and not e2e"`).

## 3. Approved decisions (locked)

### D1 — documentation corrections (content only)

- `docs/setup/TESTING.md:112` — `wg_create_client` -> `wg_create_peer` (the tool
  actually shipped in `plugins/LoomWG/LoomWG/schemas.py`).
- `docs/setup/TESTING.md:113` — `/api/artifacts/<token>/{config,qr}` ->
  `/api/artifacts/<name>/{config,qr}`; the route is
  `@router.get("/artifacts/{name}/{kind}")` (`src/api/artifacts.py:48-55`).
- `docs/setup/VDS_RUNBOOK.md:268` — `wg_create_client` -> `wg_create_peer`.
- `docs/setup/VDS_RUNBOOK.md:269` — `/api/artifacts/<token>/...` -> `<name>`.
- `docs/setup/VDS_RUNBOOK.md:374` — `wg_create_client` -> `wg_create_peer`.
- `RULES.md:361` and `AGENTS.md:41` — replace "offered exactly **one** callable
  tool, `agent_plan`" with the accurate statement: the planner is offered
  `agent_plan` (to act) and `reply` (plain conversation) — matching
  `PLANNER_TOOLS = [AGENT_PLAN_SCHEMA, REPLY_SCHEMA]` (`src/core/agent.py:121`)
  and `docs/architecture/AGENT.md`.
- `docs/architecture/PLUGIN_MANIFEST.md:15-16` — "(see `manifest.py`)" must not
  imply every plugin ships one; rephrase to reference the runtime validator that
  exists: `plugins/LoomWG/LoomWG/manifest.py`.
- `plugins/LoomWG/LoomWG/manifest.py:5` — the docstring claims "The core does not
  read it"; the core **does** read and validate `manifest.json` during discovery
  (see `docs/architecture/PLUGIN_MANIFEST.md:12-14`). Reword: the core reads and
  validates it; the plugin re-validates it against its own runtime.
- `README.md:17-25` — the quickstart says log in on the **Token** tab, but the
  default `REQUIRE_TLS=true` (`.env.example:62`, `docker-compose.yml:91`) refuses
  plain-HTTP credentials. Reword: the recommended path is password + TOTP (JWT);
  the static `ADMIN_TOKEN` is accepted only over HTTPS (or behind a proxy that
  sets `X-Forwarded-Proto: https`); `REQUIRE_TLS=false` is for local/testing HTTP.

### D2 — CI runs the integration marker

- `.github/workflows/ci.yml` `integration` job: change the step from
  `uv run pytest -q tests/test_smoke_integration.py` to
  `uv run pytest -q -m integration`. This keeps the Postgres service and
  `LOOMCORE_INTEGRATION=1` and additionally runs the two
  `@pytest.mark.integration` tests in `tests/test_rate_limit.py:219,244`.

### D3 — frontend unit tests (Vitest, pure functions only)

- Add `vitest` (current 5.x) as a devDependency and a `"test": "vitest run"`
  script in `frontend/package.json`; regenerate `package-lock.json`.
- Add `frontend/vitest.config.ts`: `test.environment = "node"`,
  `test.include = ["lib/**/*.test.ts"]`, and a `@` -> repo-root alias so the
  modules' `@/lib/*` imports resolve.
- Add `frontend/lib/chat-ws.test.ts` covering `applyWsMessage`: `thinking` sets
  `isThinking`; `response` clears thinking and appends one assistant message;
  `delta` with a `message_id` creates/extends the matching assistant bubble
  (and `reset` replaces); `tool_call`/`plan_step` don't throw; `error` clears
  thinking.
- Add `frontend/lib/chat-hydrate.test.ts` covering `appendAssistantDelta`
  (append vs reset vs new-id), `finalizeAssistant`, `upsertPlanMessage`,
  `applyPlanStep`, and a `hydrateMessages` round-trip.
- `.github/workflows/ci.yml` `frontend` job: add a `- name: Test` step running
  `npm run test` (after `Install dependencies`, before/after type-check).

## 4. Data model / interfaces

- No backend interfaces change.
- Frontend gains a test runner (`npm run test`) and the `vitest` devDependency.
- CI gains one command in the integration job and one step in the frontend job.

## 5. Parts

1. Docs corrections — `docs/setup/TESTING.md`, `docs/setup/VDS_RUNBOOK.md`,
   `RULES.md`, `AGENTS.md`, `docs/architecture/PLUGIN_MANIFEST.md`,
   `plugins/LoomWG/LoomWG/manifest.py` (docstring), `README.md`.
2. CI integration marker — `.github/workflows/ci.yml`.
3. Frontend tests — `frontend/package.json`, `frontend/vitest.config.ts`,
   `frontend/lib/chat-ws.test.ts`, `frontend/lib/chat-hydrate.test.ts`,
   `.github/workflows/ci.yml`.

## 6. Tests

- `cd frontend; npm install` then `npm run test` -> new suites pass.
- `cd frontend; npx tsc --noEmit` and `npm run build` stay clean (test files are
  excluded from the Next build).
- Backend unaffected: `uv run ruff check .`; `uv run pytest -m "not integration
  and not e2e" -q` stay green.
- `uv run pytest -q -m integration` selects the smoke + rate-limit integration
  tests (document that it needs a DB).

## 7. Acceptance criteria

- No stale references remain: `rg "wg_create_client" docs/` is empty;
  `rg "artifacts/<token>" docs/` is empty.
- `RULES.md`/`AGENTS.md` describe the planner's two tools (matching
  `src/core/agent.py:121`), and no doc claims the core ignores `manifest.json`.
- `rg "tests/test_smoke_integration.py" .github/workflows/ci.yml` no longer
  selects a single file; `pytest -m integration` is what the job runs.
- `npm run test` runs and passes; CI frontend job runs it.
- `uv run ruff check .` clean; `uv run pytest -m "not integration and not e2e" -q`
  green; `npx tsc --noEmit` clean; `npm run build` succeeds.

## 8. Commits

1. `docs: fix stale tool names, artifact paths, planner-tool count and manifest claims`
2. `test(frontend): add Vitest and cover chat-ws/chat-hydrate reducers`
3. `ci: run integration-marked tests and the frontend test suite`

## 9. Risks

- `pytest -m integration` in CI runs more tests than before; they need the
  Postgres service that the job already provides, but any flakiness now surfaces
  in CI. Mitigation: the rate-limit tests are deterministic and DB-light.
- Adding `vitest` changes `package-lock.json` and pulls a new dev dependency
  tree; pin the major version and keep it dev-only so the production build is
  unaffected.
- Docstring edits in `plugins/LoomWG/LoomWG/manifest.py` touch a plugin file:
  keep the change comment-only and re-run `uv run ruff check .`.
