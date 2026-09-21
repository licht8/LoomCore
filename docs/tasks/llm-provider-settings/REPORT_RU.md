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

## Verification

- `uv run ruff check .` -> All checks passed!
- `uv run pytest tests/test_llm_config.py tests/test_llm_client.py tests/test_api_llm.py -q`
  -> 42 passed, 1 skipped
- Combined gate `tests/test_router.py tests/test_startup_checks.py
  tests/test_llm_config.py tests/test_llm_client.py tests/test_api_llm.py -q`
  -> 94 passed, 1 skipped
- `uv run pytest -m "not integration and not e2e" -q` -> 1037 passed,
  12 skipped, 5 deselected (tmp/tests_llm_provider_full2.log; baseline 1004)
- `cd frontend; npx tsc --noEmit` -> clean
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
src/app.py, src/config.py, src/core/startup_checks.py, src/llm/client.py,
src/llm/exceptions.py, src/llm/router.py, tests/test_llm_client.py,
tests/test_router.py, tests/test_startup_checks.py.
New: src/api/llm.py, src/llm/config.py, src/llm/providers.py, tests/test_api_llm.py,
tests/test_llm_config.py, frontend/lib/llm-settings-api.ts,
frontend/components/ui/select.tsx, frontend/components/settings-view.tsx,
frontend/components/llm-setup-banner.tsx, frontend/app/(dashboard)/settings/page.tsx.

## Suggested commits

1. `feat(llm): add runtime provider config store and remove the hardcoded provider`
2. `refactor(llm): read active provider config per request`
3. `feat(api): add admin LLM provider settings endpoints`
4. `feat(frontend): add Settings tab and unconfigured banner`
5. `docs: document runtime LLM provider settings`

Commits created: none
