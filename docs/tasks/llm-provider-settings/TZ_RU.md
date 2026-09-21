# TZ_RU.md — runtime LLM provider settings

Task id: `llm-provider-settings`. Follow RULES.md (English only, no comments unless
existing style has them, ruff clean, tests mandatory). Executor does NOT commit;
the coordinator commits after acceptance (RULES 0a). This is core LLM
configuration (it reconfigures the core router), so it stays in `src/`, not a
plugin.

## 1. Goal

Ship **no default provider**. The app boots *unconfigured*; an admin picks a
provider and enters its settings from a new Settings tab, applied at runtime
without a restart and persisted on the data volume.

- **Presets (locked):** OpenAI, OpenRouter, Groq, DeepSeek, Custom
  (OpenAI-compatible). **Hetzner is removed entirely** — no preset, no
  `HETZNER_*` settings, no Hetzner naming. A user who wants it uses the Custom
  preset.
- **Scope (locked):** exactly one *active* provider, plus an optional separate
  model for the plan summary.
- **Persistence (locked):** a single JSON file on the `loomdata` volume
  (`/data/llm.json`, override `LLM_SETTINGS_FILE`), atomic writes, fail-safe
  reads — mirroring `src/plugins/state.py` and the revoked store.
- **API key (locked):** stored in that file (mode 0600), **write-only** — never
  returned by the API (GET reports `configured` + a short preview), never logged,
  never written to the audit log. Admin can set / replace / clear it.
- **Env bootstrap (locked):** optional generic `LLM_*` env vars pre-seed the
  config for headless/IaC deploys. All empty by default, so a plain compose boots
  unconfigured.
- **Unconfigured UX (locked):** chat stays usable; sending returns a clear
  "provider not configured" message, and the dashboard shows a banner linking to
  Settings.

The client is OpenAI-compatible (`{base_url}/chat/completions`, Bearer auth), so
a provider is only `base_url + model + api_key` (+ a few knobs).

## 2. Non-goals

- No multiple saved provider profiles, no per-request/per-role routing (planner
  vs summary only), no embeddings/fine-tuning endpoints.
- No change to the agent loop, plan schema, plugins, or the daily budget API/UI.
- No migration: no DB table (decision D1).
- No change to auth/credential handling.
- No new external dependency (use `httpx` already present).
- No backward-compat shim for `HETZNER_*` env vars (removed on purpose).

## 3. Approved decisions (locked)

### D1 — JSON file, not a DB table
`/data/llm.json` on the existing `loomdata` volume, default from
`LLM_SETTINGS_FILE`; atomic temp-file + `os.replace`; missing/corrupt/unreadable
file falls back to the env-derived config and logs a warning. Never fail startup.

### D2 — one active provider + optional summary model
`LlmConfig` holds a single active provider. `summary_model` (optional, default
empty) overrides the model for `summarize_plan` only.

### D3 — API key is write-only, never carried across providers
`GET /api/llm/settings` returns `api_key: {"configured": bool, "preview": "…last4"}`
(no raw value). `PUT` `api_key` semantics: `null`/omitted → keep the existing key
**only if `provider` is unchanged**, otherwise clear it; `""` → clear; any other
string → replace. Audit and logs record provider / model / base_url only.
`configured` is `bool(base_url and model)` — the key is optional so keyless local
endpoints (Custom/vLLM/Ollama-style) work.

### D4 — read config per request (hot switch, no restart)
`HetznerClient` becomes `OpenAICompatClient` and reads the active `LlmConfig` at
the start of every `chat_completion` call (model, base URL, key, timeouts,
retries). Remove the class-level frozen `MODEL` and all `settings.hetzner_*`
reads. No router rebuild: the two `AIRouter` singletons stay.

### D5 — `chat_template_kwargs` is opt-in, not per-vendor
Today the client always sends `chat_template_kwargs: {"enable_thinking": false}`
unless thinking is enabled (`client.py:91-95`). With Hetzner gone, make this an
explicit `LlmConfig.thinking_template: bool` (default `False`), exposed under
Advanced in the UI; the field is sent **only** when `True` (for local vLLM
deployments). Default `False` keeps OpenAI/OpenRouter/Groq/DeepSeek safe.

### D6 — generic `LLM_*` env bootstrap, empty by default
Replace the `hetzner_*` fields in `src/config.py` with generic ones:
`llm_provider`, `llm_base_url`, `llm_model`, `llm_api_key`, `llm_connect_timeout`,
`llm_read_timeout`, `llm_write_timeout`, `llm_pool_timeout`, `llm_max_retries`,
`llm_enable_thinking`, `llm_settings_file`. `default_config()` maps them onto
`LlmConfig` and yields the **unconfigured** state when base URL/model are empty.
An existing `.env` with `HETZNER_*` simply no longer matches (documented).

### D7 — unconfigured state
`LlmConfig.configured` = `bool(base_url and model)`. Add a typed
`LlmNotConfigured` error (`src/llm/exceptions.py`). `AIRouter.route` and
`summarize_plan` return the friendly message
`"No LLM provider is configured yet. Open Settings to choose one."` instead of
calling the provider. `check_security_posture()` logs a warning (not fatal) when
unconfigured. The dashboard renders a banner with a link to `/settings` while
unconfigured.

## 4. Data model / interfaces

### 4.1 Provider catalog — `src/llm/providers.py` (new)
```python
@dataclass(frozen=True)
class ProviderPreset:
    id: str
    label: str
    default_base_url: str
    default_model: str

PROVIDER_PRESETS: dict[str, ProviderPreset]  # openai/openrouter/groq/deepseek/custom
```

### 4.2 Config store — `src/llm/config.py` (new)
```python
@dataclass(frozen=True)
class LlmConfig:
    provider: str = ""
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    summary_model: str = ""
    enable_thinking: bool = False
    thinking_template: bool = False
    connect_timeout: float = 15.0
    read_timeout: float = 90.0
    write_timeout: float = 30.0
    pool_timeout: float = 15.0
    max_retries: int = 2

    @property
    def configured(self) -> bool: return bool(self.base_url and self.model)

def default_config() -> LlmConfig            # from src.config.settings (LLM_*)
def load_config(path: Path) -> LlmConfig     # fail-safe -> default_config()
def save_config(path: Path, cfg: LlmConfig) -> bool   # atomic, chmod 0600
def mask_api_key(key: str) -> str            # "…abcd" / ""
def resolve_config_path(configured: str = "") -> Path
def get_active() -> LlmConfig                # cached; reloads if mtime changed
def set_active(cfg: LlmConfig) -> bool       # update cache + persist
```
`_atomic_write_text` may be copied from `src/plugins/state.py` (or factored to a
shared helper).

### 4.3 Client — `src/llm/client.py` (edit)
- Rename `HetznerClient` -> `OpenAICompatClient`; update `src/llm/router.py`
  (lines 22, 171-173) and `tests/test_llm_client.py` (11 references).
- `__init__(self, api_key: str | None = None, config: LlmConfig | None = None)`;
  keep the `api_key` override for tests, otherwise use the active config.
- `chat_completion(..., model: str | None = None)`: resolve `cfg = self._config`;
  payload `model = model or cfg.model`; headers from `cfg.api_key`; per-call
  timeouts/`max_retries` from `cfg`; send `chat_template_kwargs` only when
  `cfg.thinking_template` (D5).
- Budget logic unchanged (`settings.llm_daily_call_limit` still read per call).
- `src/llm/exceptions.py`: add `LlmNotConfigured`.
- `src/llm/router.py`: guard on `not cfg.configured`; `summarize_plan` passes
  `model=get_active().summary_model or None`.

### 4.4 REST — `src/api/llm.py` (new), registered in `src/app.py`
`APIRouter(prefix="/api")`, admin via `require_admin_token(extract_admin_token(...), request)`,
`@limiter.limit(...)`, plain dict responses (match `src/api/plugins.py`):

| Method | Path | Limit | Behaviour |
|---|---|---|---|
| GET | `/api/llm/providers` | 60/min | catalog: `{providers:[{id,label,default_base_url,default_model}]}` |
| GET | `/api/llm/settings` | 60/min | active config, key masked per D3, plus `configured` |
| PUT | `/api/llm/settings` | 10/min | validate + persist + `set_active` + audit (no key) |
| POST | `/api/llm/test` | 10/min | probe active/overridden config via `GET {base_url}/models` (10s); no budget consumed; `{ok,status,models_count?,error?}`; unconfigured -> 400 |

`PUT` body `LlmSettingsUpdate` (Pydantic, all optional) with validation: known
provider id; `base_url` starts with `http://`/`https://`; `model` non-empty;
positive numeric timeouts; `max_retries >= 0`. Invalid -> 400 (not 500).
Audit via `record_mutation_isolated`, action `llm_settings_update`, fields
`provider`/`model`/`base_url` only.

## 5. Parts

1. `src/config.py` — replace `hetzner_*` with generic `llm_*` + `llm_settings_file`.
2. `src/llm/providers.py` (new) — preset catalog (no Hetzner).
3. `src/llm/config.py` (new) — `LlmConfig`, load/save/get/set, masking, path.
4. `src/llm/exceptions.py` — add `LlmNotConfigured`.
5. `src/llm/client.py` — rename + read active config per call + D5 gating.
6. `src/llm/router.py` — new class name; unconfigured guard; `summary_model`.
7. `src/core/startup_checks.py` — warn when unconfigured.
8. `src/api/llm.py` (new) + `src/app.py` registration.
9. Frontend:
   - `frontend/lib/llm-settings-api.ts` (new) — `fetchLlmProviders`,
     `fetchLlmSettings`, `updateLlmSettings`, `testLlmConnection`
     (`getApiBase()` + `authHeaders()` + `credentials: "include"`, copy
     `plugins-api.ts`).
   - `frontend/components/ui/select.tsx` (new) — minimal base-ui Select.
   - `frontend/components/settings-view.tsx` (new) — React Query load/mutate;
     fields: provider (select), base URL, model, API key (password; placeholder
     shows existing preview, blank = unchanged, explicit Clear action), summary
     model (optional), enable-thinking switch, Advanced (thinking-template switch,
     timeouts, max retries), Test connection, Save (AlertDialog confirm). Reuse
     `Card`, `Input`, `Switch`, `Button`, `AlertDialog`, `Badge`.
   - `frontend/components/llm-setup-banner.tsx` (new) — shows while
     `configured == false`, links to `/settings`; rendered from
     `dashboard-shell.tsx`.
   - `frontend/app/(dashboard)/settings/page.tsx` (new) — renders `SettingsView`.
   - `frontend/components/dashboard-sidebar.tsx` — convert the dead footer
     Settings `<button>` (lines ~90-98) into `<Link href="/settings">` with the
     active style. Do not also add a nav item (avoid duplication).
10. Docs/compose: `README.md`, `docs/setup/DEPLOY.md`, `.env.example`,
    `docker-compose.yml` (drop `HETZNER_*`; add generic `LLM_*`),
    `CHANGELOG.md` (note the removal of `HETZNER_*` as a breaking change).

## 6. Tests

- New `tests/test_llm_config.py`: unconfigured default when env empty; env
  bootstrap mapping; missing/corrupt file -> default; save/load round-trip; file
  mode 0600; `mask_api_key`; `set_active` updates the cached value; `configured`
  property.
- `tests/test_llm_client.py` (edit): import rename; model/base_url/headers come
  from the active config; switching config between calls changes the payload;
  `chat_template_kwargs` present only when `thinking_template` is true; `model=`
  override; `LlmNotConfigured` when unconfigured; existing retry/budget tests
  still pass (no `settings.hetzner_*`).
- New `tests/test_api_llm.py` (TestClient): providers list has no Hetzner; GET
  settings never contains the raw key; PUT without admin -> 401/403; PUT invalid
  base URL / unknown provider -> 400; PUT persists and is reflected by GET; key
  carried across a provider change is cleared; audit entry contains no key;
  `POST /test` unconfigured -> 400 and mocked transport otherwise.
- Frontend: `cd frontend; npx tsc --noEmit`.

Gate commands:
```
ruff check .
pytest -m "not integration and not e2e"
pytest tests/test_llm_config.py tests/test_llm_client.py tests/test_api_llm.py
cd frontend; npx tsc --noEmit
```

## 7. Acceptance criteria

- `docker compose up` with **no** LLM env vars boots successfully; the dashboard
  shows the "configure provider" banner and chat returns the friendly
  not-configured message.
- An admin sets a provider in Settings and the change takes effect on the next
  chat turn **without a restart**.
- The raw API key never appears in any HTTP response, log line, or audit row;
  the file is mode 0600.
- No `HETZNER_*` settings, references, presets, or compose/env/doc mentions
  remain (grep clean).
- Switching provider without supplying a key clears the previous key rather than
  reusing it.
- Invalid input returns 400 with a clear message, never 500.
- `ruff check .` clean; fast suite green; `npx tsc --noEmit` clean.

## 8. Commits (coordinator commits only after acceptance, RULES 0a)

1. `feat(llm): add runtime provider config store and remove the hardcoded provider`
2. `refactor(llm): read active provider config per request`
3. `feat(api): add admin LLM provider settings endpoints`
4. `feat(frontend): add Settings tab and unconfigured banner`
5. `docs: document runtime LLM provider settings`

## 9. Risks

- **Breaking change:** existing `HETZNER_*` env vars stop working; deployments
  must configure via Settings (or the new `LLM_*` vars). Documented in CHANGELOG
  and DEPLOY.
- **Secrets at rest:** the key is plaintext in `/data/llm.json` (0600). Documented;
  env-only remains possible by leaving the UI key blank.
- **Provider quirks:** `/models` is optional on some gateways; `POST /test` must
  degrade gracefully (clear error, no 500). `thinking_template` is vLLM-only and
  off by default (D5).
- **Keyless endpoints:** `configured` does not require a key, so local Custom
  endpoints work; the UI should say the key is "optional for local providers".
- **Frontend auth:** `AdminGate` already protects `/settings`; mutating calls still
  need `authHeaders()` + `credentials: "include"` like `plugins-api.ts`.
- **Single-process assumption:** `set_active` updates the in-process cache; the
  mtime reload guard keeps multi-worker/file edit consistent.
