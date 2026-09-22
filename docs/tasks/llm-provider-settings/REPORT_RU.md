# REPORT_RU.md — runtime LLM provider settings

Spec: docs/tasks/llm-provider-settings/TZ_RU.md

## Goal

Remove the hardcoded Hetzner provider. LoomCore now boots with **no** LLM
provider configured, and an admin picks one from a new **Settings** tab. The
choice applies to the next turn without a restart and is persisted on the
loomdata volume.

## Delivered

### Config store (no DB table)

- `src/llm/providers.py` — `ProviderPreset` and `PROVIDER_PRESETS` for OpenAI,
  OpenRouter, Groq, DeepSeek and Custom (OpenAI-compatible). Hetzner is gone.
- `src/llm/config.py` — frozen `LlmConfig` (provider, base_url, model, api_key,
  summary_model, enable_thinking, thinking_template, four timeouts, max_retries)
  with a `configured` property (`base_url` and `model`), plus `load_config`,
  `save_config` (atomic temp + `os.replace`, `chmod 0600`), `default_config`
  (maps `LLM_*` env), `resolve_config_path`, `mask_api_key`, and the cached
  `get_active` / `set_active`. Missing, corrupt or unreadable files fall back to
  the env-derived default with a warning and never fail startup.
- `src/llm/exceptions.py` — added `LlmNotConfigured`.

### Client and router

- `src/llm/client.py` — `HetznerClient` became `OpenAICompatClient`. It no
  longer holds a class-level model or reads `settings.hetzner_*`; every
  `chat_completion` resolves the active `LlmConfig` (hot switch) for the model,
  base URL, key, timeouts and retries, raises `LlmNotConfigured` when
  unconfigured, accepts a per-call `model=` override, and sends
  `chat_template_kwargs` only when `thinking_template` is enabled.
- `src/llm/router.py` — uses `OpenAICompatClient`; `route` and `summarize_plan`
  short-circuit with the not-configured message; `summarize_plan` uses
  `summary_model` when set.
- `src/core/startup_checks.py` — `check_security_posture` logs a warning (not
  fatal) when no provider is configured.

### Admin API

- `src/api/llm.py` (registered in `src/app.py`): `GET /api/llm/providers`,
  `GET`/`PUT /api/llm/settings`, `POST /api/llm/test`. All admin-gated and rate
  limited; plain-dict responses. The API key is **write-only** — responses carry
  only `{"configured", "preview"}`; audit records provider/model/base_url only.
  Changing provider without a key clears the stored key. Invalid input returns
  400.

### Frontend

- `frontend/lib/llm-settings-api.ts` — typed client for the four endpoints.
- `frontend/components/ui/select.tsx` — minimal base-ui Select wrapper.
- `frontend/components/settings-view.tsx` — provider picker (fills preset
  defaults), base URL, model, write-only API key (preview placeholder, blank =
  unchanged, explicit Clear), summary model, enable-thinking and Advanced
  (thinking template, timeouts, retries), Test connection, Save with a confirm
  dialog.
- `frontend/components/llm-setup-banner.tsx` — banner while unconfigured, links
  to `/settings`; rendered from `dashboard-shell.tsx`.
- `frontend/app/(dashboard)/settings/page.tsx`; the dead Settings button in
  `dashboard-sidebar.tsx` is now a working link.

### Docs / compose

- `README.md`, `docs/setup/DEPLOY.md`, `docs/setup/VDS_RUNBOOK.md`,
  `deploy/install.sh`, `RULES.md` updated.
- `.env.example` and `docker-compose.yml` drop every `HETZNER_*` variable for an
  optional commented `LLM_*` bootstrap; the provider is chosen at runtime.

## Review fix round

Seventeen review findings were addressed:

- `src/api/llm.py` — the audit entry now records the real save outcome
  (`status="error"` when `set_active` fails, before the 500); explicit JSON
  `null` for any field is treated as unchanged via `_unchanged` instead of
  clearing it; the previously dead module `logger` now warns when the audit
  write itself fails (`contextlib` import removed).
- `src/llm/config.py` / `src/config.py` — `default_config()` now maps
  `thinking_template` from a new `LLM_THINKING_TEMPLATE` setting so the env
  bootstrap can actually send the thinking template.
- `src/llm/client.py` — the `Authorization` header is only sent when a key is
  present (keyless endpoints no longer receive an empty `Bearer`).
- `docker-compose.yml` / `.env.example` / `docs/setup/DEPLOY.md` — added the
  `LLM_THINKING_TEMPLATE` and `LLM_DAILY_CALL_LIMIT` passthrough (and documented
  them) so `.env` values are no longer ignored.
- `frontend/lib/llm-settings-api.ts` — `testLlmConnection` reports errors through
  the shared `readError` (a 422 array no longer renders as `[object Object]`).
- `frontend/components/error-banner.tsx` (new) — shared `ErrorBanner`, now also
  used by `plugins-view.tsx` instead of a duplicated copy.
- `frontend/components/llm-setup-banner.tsx` — dedicated `["llm-settings",
  "banner"]` query key with a 5-minute interval, and an in-flow spacer so the
  absolute banner no longer overlaps page content.
- `frontend/components/dashboard-sidebar.tsx` — the footer Settings link reuses
  `NavLink` instead of duplicating its active-state logic.
- `frontend/components/settings-view.tsx` — resets the test/saved messages on any
  edit, rejects an empty `max_retries`, surfaces the providers-query error with a
  retry, associates labels with their controls, extracts `formatTestResult`, and
  warns that changing provider clears the stored key unless a new one is entered
  (plus a hint that Test probes the saved configuration).

## Review fix round 2

Eleven further findings were addressed:

- `src/api/llm.py` — the provider guard now validates only the client-supplied
  value (a non-preset provider inherited from `LLM_PROVIDER` no longer makes
  every `PUT` fail); `POST /api/llm/test` strips a trailing slash from
  `base_url` and catches `httpx.HTTPError`/`httpx.InvalidURL` so a malformed
  value keeps the structured `{ok: false}` contract.
- `frontend/lib/http-error.ts` (new) — shared `readError`, now imported by both
  `plugins-api.ts` and `llm-settings-api.ts` instead of duplicated.
- `frontend/components/llm-setup-banner.tsx` — reuses the shared
  `["llm-settings"]` cache key so the banner and the Settings view share one
  request.
- `frontend/components/settings-view.tsx` — seeds the form only once
  (`prev ?? toForm(settings)`) so background refetches keep unsaved edits; a
  single `resetTransientState()` clears test/saved/error state from both edit
  paths; `buildUpdate()` is side-effect free and returns `{update, error}`; the
  test result resets on a successful save; the failure text is no longer
  double-prefixed; and a failed settings load shows an explicit message instead
  of an endless skeleton.

## Review fix round 3

Five further findings (all in `frontend/components/settings-view.tsx`) were
addressed:

- The form was seeded only once, so a server-side change fetched by the 5-minute
  refetch was invisible while the next full-payload Save silently reverted it. A
  dirty flag (`dirtyRef`) now gates re-seeding: an untouched form picks up the
  refetched values, while unsaved edits are preserved. Every edit path marks the
  form dirty, and a successful save clears the flag.
- The nested `isLoading ? … : !form ? … : …` render ternary was replaced with
  three flat branches (loading skeleton / load-failed message / form).
- `buildUpdate()` is now validated once when the confirm dialog opens; the built
  update is stored in `pendingUpdate` and submitted on confirm, removing the
  unreachable second validation.
- The "changing the provider clears the stored API key" warning now also requires
  `settings.api_key.configured`, so it no longer appears on a fresh install with
  no stored key.
- The API-key input and the Clear/Keep button now call `resetTransientState()`
  (and mark the form dirty), like every other edit path.

## Review fix round 4

Ten further findings were addressed (findings 2-5 were already fixed in round 3):

- `frontend/components/settings-view.tsx` — `dirtyRef` was a one-way latch, so the
  form permanently ignored server state after the first edit with no way to reload
  it. It is now `const [dirty, setDirty] = useState(false)`: the seed effect skips
  while dirty, and a new `Discard changes` action resets the flag and re-syncs the
  form (and the typed key) from `settings`.
- The four timeout inputs now declare `min="0.1"` (matching the backend `> 0`
  rule) instead of `min="0"`.
- `frontend/components/error-banner.tsx` — the message wraps
  (`min-w-0 break-words`) instead of being truncated, so long backend validation
  details stay readable.
- `frontend/lib/use-llm-settings.ts` (new) — shared `useLlmSettings()` hook for the
  `["llm-settings"]` query (same key/`fetchLlmSettings`/5-minute interval), used by
  both `settings-view.tsx` and `llm-setup-banner.tsx` so the options cannot drift.
- `frontend/components/settings-view.tsx` — `buildUpdate()` now includes
  `provider` only when it changed, so an instance bootstrapped with a non-preset
  `LLM_PROVIDER` (e.g. `ollama`) can still save other fields.
- `handleProviderChange` now always applies the preset defaults (clearing
  `base_url`/`model` for the Custom preset) and resets the typed key/clear flag, so
  a stale key or endpoint is no longer submitted under the wrong provider.

## Review fix round 5

Six further findings were addressed:

- `src/llm/client.py` — the completion URL is now `f"{config.base_url.rstrip('/')}/chat/completions"`,
  matching `POST /api/llm/test`, so a base URL with a trailing slash no longer
  produces a doubled slash.
- `src/llm/config.py` — `set_active` now returns `False` without touching the
  runtime cache when `save_config` fails, so a failed persist no longer hot-swaps
  a config the route then reports as rejected.
- `src/llm/config.py` — `mask_api_key` returns `"…"` for keys of four characters
  or fewer, so a short key is never revealed by the preview.
- `frontend/components/settings-view.tsx` — the seed effect tracks the last
  synced `settings` object in a `syncedSettings` ref, so a post-save
  `setDirty(false)` no longer re-seeds the form from a stale query cache; the form
  re-syncs only when the query data object actually changes.
- `frontend/components/settings-view.tsx` — the provider `Select` renders from the
  single `providerItems` memo instead of duplicating the mapping inline.
- `frontend/components/settings-view.tsx` — `handleProviderChange` also clears the
  typed key, the clear flag, and `summary_model`, so a stale value is not silently
  submitted under the new provider.

## Review fix round 6

Six further findings were addressed:

- `frontend/lib/http-error.ts` — `readError` now also renders FastAPI 422
  validation failures: when `detail`/`error` is an array it collects the string
  `msg` values and joins them with `"; "`, instead of falling back to the generic
  `"<status> <statusText>"`.
- `frontend/components/settings-view.tsx` — the four timeout inputs and the max
  retries input shared one `Field` + `Input type="number"` block; they are now
  rendered through a single `NumberField` ({label, min, step, value, onChange}),
  removing the duplication and keeping the numeric attributes consistent.
- `src/llm/config.py` — the `save_config` docstring contract was stale after
  round 5: a failed persist returns `False` and `set_active` no longer applies the
  config at runtime, so the parenthetical now reads "the change is rejected, not
  applied".
- `frontend/lib/llm-settings-api.ts` — the four functions repeated the same
  `fetch` + `!response.ok` + `readError` + `json()` boilerplate; it is now a
  shared `requestJson(path, init, errorPrefix)` helper, matching the
  `mutatePlugin` convention already used by `plugins-api.ts`.
- `frontend/components/settings-view.tsx` — the "Enable model thinking" switch is
  now `disabled={!form.thinking_template}` (with a hint), since
  `src/llm/client.py` only sends `chat_template_kwargs` when `thinking_template`
  is on, so toggling it alone did nothing.
- `src/api/llm.py` — the `_merge` `base_url` guard now validates the
  client-supplied value (`supplied_base_url`) like the `provider` guard, instead
  of the merged value, so a `PUT` that omits `base_url` no longer fails with a
  400 on an inherited scheme-less value from the env bootstrap or a hand-edited
  file.

## Review fix round 7

Seven further findings were addressed:

- `frontend/lib/llm-settings-api.ts` — `requestJson` no longer spreads
  `init.headers` as a plain object (which silently dropped headers for a
  `Headers` instance and mangled a `string[][]`); a `requestHeaders` helper merges
  the auth headers through the `Headers` API.
- `frontend/lib/llm-settings-api.ts` — the `fetch` call is wrapped so a rejected
  request (offline, DNS, CORS, reset) becomes `${errorPrefix} (reason)` instead of
  a raw `TypeError` ("Failed to fetch") that bypassed both the prefix and
  `readError`.
- `frontend/lib/llm-settings-api.ts` — the success body is read as text and only
  parsed when non-empty, so an empty 2xx body (e.g. a future `204`) yields
  `undefined` instead of throwing a raw `SyntaxError`.
- `frontend/components/settings-view.tsx` — turning "Send thinking template" off
  now clears `enable_thinking` too, so the persisted config cannot end up with
  `enable_thinking=true` while `thinking_template=false`.
- `frontend/components/settings-view.tsx` — the "Only sent when the thinking
  template is enabled" hint moved outside the `<label>`, so the switch's
  accessible name is just "Enable model thinking" and not the hint text.
- `frontend/components/settings-view.tsx` — the API-key input is no longer
  `disabled` in clear mode, so typing a replacement key cancels the clear rather
  than the `setClearKey(false)` branch being unreachable dead code.
- `frontend/components/settings-view.tsx` — the saved-settings message and the
  connection-test result are now `role="status"` live regions, matching the other
  status feedback (`ErrorBanner`/`LlmSetupBanner`).

## Verification

- `uv run ruff check .` -> All checks passed!
- `uv run pytest tests/test_api_llm.py tests/test_llm_config.py tests/test_llm_client.py -q`
  -> 46 passed, 1 skipped
- Combined gate `tests/test_router.py tests/test_startup_checks.py
  tests/test_llm_config.py tests/test_llm_client.py tests/test_api_llm.py -q`
  -> 94 passed, 1 skipped
- `uv run pytest -m "not integration and not e2e" -q` -> 1041 passed,
  12 skipped, 5 deselected (tmp/tests_llm_round5_full.log; baseline 1004)
- `cd frontend; npx tsc --noEmit` -> clean (re-run after the round-5
  frontend-only edits; the Python gates were unaffected)
- Round-6 re-run: `uv run ruff check .` -> All checks passed!;
  `uv run pytest tests/test_api_llm.py tests/test_llm_config.py
  tests/test_llm_client.py -q` -> 46 passed, 1 skipped;
  `uv run pytest -m "not integration and not e2e" -q` -> 1041 passed,
  12 skipped, 5 deselected (tmp/tests_llm_round6_full.log);
  `cd frontend; npx tsc --noEmit` -> clean
- Round-7 re-run (frontend-only changes): `cd frontend; npx tsc --noEmit` ->
  clean; `npm run build` -> compiled successfully (tmp/frontend_build_round7.log)
- `rg -i hetzner` outside `docs/tasks/**`, `tmp/**`, lockfiles -> only the
  intentional negative assertions in tests/test_api_llm.py
- Integration/e2e not run (no Postgres; e2e needs Linux + root).

## Tests added / updated

- new `tests/test_llm_config.py` (default fallback, env mapping, corrupt/missing
  file, round-trip, 0600 on POSIX, masking, configured property, path resolution,
  cache invalidation)
- rewritten `tests/test_llm_client.py` (config-driven payloads, hot switch,
  `model=` override, opt-in `chat_template_kwargs`, `LlmNotConfigured`, retries,
  client reuse/close)
- new `tests/test_api_llm.py` (no Hetzner preset, key never returned, admin gate,
  validation 400s, persistence, key cleared on provider change, audit excludes
  the key, connection test)
- `tests/test_router.py` and `tests/test_startup_checks.py` updated to provide a
  configured provider and cover the unconfigured warning.

## Notes / risks

- Breaking change: all `HETZNER_*` env variables are ignored; users selecting a
  Hetzner model now use the Custom preset or a generic `LLM_*` bootstrap.
- The key is stored in plaintext in `/data/llm.json` (mode 0600) and is
  documented as such; it is never returned, logged or audited.
- `POST /api/llm/test` tolerates gateways without `/models` (reports the error
  instead of failing).
- Keyless local endpoints are supported (`configured` does not require a key).

## Changed files

Modified: .env.example, CHANGELOG.md, README.md, RULES.md, deploy/install.sh,
docker-compose.yml, docs/setup/DEPLOY.md, docs/setup/VDS_RUNBOOK.md,
frontend/components/dashboard-shell.tsx, frontend/components/dashboard-sidebar.tsx,
frontend/components/plugins-view.tsx, frontend/lib/plugins-api.ts,
frontend/lib/llm-settings-api.ts, frontend/components/llm-setup-banner.tsx,
frontend/components/settings-view.tsx, frontend/components/dashboard-sidebar.tsx,
src/app.py, src/config.py, src/llm/client.py, src/core/startup_checks.py,
src/llm/exceptions.py, src/llm/router.py, tests/test_api_llm.py,
tests/test_llm_client.py, tests/test_router.py, tests/test_startup_checks.py.
New: src/api/llm.py, src/llm/config.py, src/llm/providers.py, tests/test_api_llm.py,
tests/test_llm_config.py, frontend/lib/http-error.ts, frontend/lib/use-llm-settings.ts,
frontend/components/ui/select.tsx, frontend/components/settings-view.tsx,
frontend/components/llm-setup-banner.tsx, frontend/components/error-banner.tsx,
frontend/app/(dashboard)/settings/page.tsx.

## Suggested commits

1. `feat(llm): add runtime provider config store and remove the hardcoded provider`
2. `refactor(llm): read active provider config per request`
3. `feat(api): add admin LLM provider settings endpoints`
4. `feat(frontend): add Settings tab and unconfigured banner`
5. `docs: document runtime LLM provider settings`

Commits created: none
