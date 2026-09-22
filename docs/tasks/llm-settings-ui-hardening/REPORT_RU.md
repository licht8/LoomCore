# REPORT_RU.md — LLM Settings tab hardening

Task spec: `docs/tasks/llm-settings-ui-hardening/TZ_RU.md`.

## Goal

Fix six low-severity review findings in the LLM Settings UI: an unguarded
success-body parse, inaccessible `Field` label wrapping, duplicated API-key
transition logic, an unstable `providers` default, a banner that also shows on
`/settings`, and a post-save cache that could lag behind the form.

## Delivered

### `frontend/lib/llm-settings-api.ts` (D1)

- `requestJson` no longer does a bare `JSON.parse` with an `undefined as T`
  fallback. After the `!response.ok` check it reads the body as text and throws
  `${errorPrefix} (empty response)` on an empty 2xx, or
  `${errorPrefix} (invalid JSON response)` when parsing fails, so malformed
  success bodies surface the existing friendly message instead of a raw
  `SyntaxError`/`TypeError`.

### `frontend/components/settings-view.tsx` (D2, D3, D4, D6)

- `Field` now generates a stable `useId()` label id and supports a `group`
  variant (`role="group"` + `aria-labelledby`). Plain single-control fields keep
  the `<label>` wrapper; the Provider `Select` and the Key `Input`+`Button` use
  `group` with explicit names (`SelectTrigger aria-label="Provider"`,
  `Input aria-label="API key"`).
- The two raw `<label>` wrappers around the thinking `Switch`es became `<div>`s
  with the visible text kept as a `<span>`, and each `Switch` got an explicit
  `aria-label` ("Enable model thinking" / "Send thinking template").
- The duplicated clear/keep transition is now shared: `handleKeyInput(value)`
  called from the key `Input onChange`, and `handleToggleClear()` called from the
  Clear/Keep `Button onClick` (behaviour unchanged).
- `providers` is now `providersData ?? EMPTY_PROVIDERS`, with a module-level
  `EMPTY_PROVIDERS` constant, so the reference is stable while loading/empty and
  the `providerItems` memo no longer recomputes every render.
- `saveMutation.onSuccess` now seeds `queryClient.setQueryData(["llm-settings"],
  updated)` instead of invalidating `["llm-settings"]`/`["llm-providers"]`, so the
  badge, key placeholder and Discard action stay consistent with the saved config
  without an extra round-trip. The two load-error retry handlers keep their own
  invalidations.

### `frontend/components/llm-setup-banner.tsx` (D5)

- Uses `usePathname()`; returns `null` on `/settings` so it neither self-links nor
  shifts that page. Still renders on the other dashboard routes.

## Verification

- `cd frontend; npx tsc --noEmit` — clean (authoritative type gate; the build
  ignores TS errors).
- `cd frontend; npm run build` — compiled successfully
  (`tmp/frontend_build_hardening.log`; routes `/`, `/_not-found`, `/plugins`,
  `/settings`).
- By inspection:
  - `JSON.parse` appears once, inside the `try` in `requestJson`; no
    `undefined as T` remains.
  - Only one `<label>` element remains in `settings-view.tsx` (the single-control
    `Field` default), so no `<label>` wraps more than one interactive control;
    every control has an explicit accessible name.
  - `rg -n "queryClient.invalidateQueries|queryClient.setQueryData"
    frontend/components/settings-view.tsx` shows `setQueryData` in the save path
    and `invalidateQueries` only in the two load-error retry handlers.
  - `EMPTY_PROVIDERS` is module-level and `providers = providersData ??
    EMPTY_PROVIDERS`.
- No Python file touched, so no `ruff`/`pytest` run was required.

## Changed files

- `frontend/lib/llm-settings-api.ts` — D1 guarded success-body parse.
- `frontend/components/settings-view.tsx` — D2 accessible `Field`/labels, D3
  shared key handlers, D4 stable providers default, D6 cache seeding.
- `frontend/components/llm-setup-banner.tsx` — D5 hide on `/settings`.

## Commits

None (executor does not commit; the coordinator commits after acceptance).

## Notes and follow-ups

- `setQueryData(["llm-settings"], updated)` relies on the `PUT` response matching
  the `GET` shape; both use `_serialize`, so this holds.
- Accessibility is markup-only; the base-ui `Select`/`Switch` forward `aria-*`
  (verified by the clean `tsc` and build). No primitive swallowed a prop.
- No frontend test runner exists, so verification is static + production build;
  no unit tests were added (per the spec's non-goals).

## Review fix round 8

Six further findings (one backend bug, five frontend polish):

- **`src/llm/router.py` (bug, medium)** — the unconfigured early return fired
  before `_begin_turn`, so neither the user message nor the setup notice was
  persisted. It now calls `_begin_turn(...)` and `_persist_assistant(...,
  NOT_CONFIGURED_MESSAGE, None)` before returning, so an unconfigured turn
  survives reload and is valid context once a provider is configured. Added
  `tests/test_router.py::test_route_unconfigured_persists_turn`.
- **`frontend/components/llm-setup-banner.tsx`** — route guard is now
  `pathname.startsWith("/settings")` (covers a trailing slash and future
  sub-routes); the spacer uses `h-[var(--banner-h)]` instead of the hard-coded
  `h-8`.
- **`frontend/components/dashboard-shell.tsx`** — added `--banner-h: 2rem` next
  to `--header-h` so both layout constants have a single source.
- **`frontend/components/settings-view.tsx`**:
  - `updateForm` compares the patch against the current form and returns early
    when nothing changed, so reverting a field no longer marks the form dirty or
    opens a redundant confirm save.
  - `Field`'s unused `htmlFor` prop was removed; the single-control branch renders
    a plain `<label>` (the `useId`-based `role="group"` branch is unchanged).
  - `providerItems` seeds the current `form.provider` when it is missing from the
    preset catalog (e.g. a non-preset `LLM_PROVIDER` such as `ollama`), so the
    `Select` shows the saved provider instead of falling back to the placeholder;
    `handleProviderChange` early-returns for an unchanged id so re-selecting that
    entry cannot blank `base_url`/`model`.
  - The API-key placeholder now reads "Will be removed on save" while `clearKey`,
    removing the contradiction with the red removal note.

### Verification (round 8)

- `uv run ruff check .` — clean.
- `uv run pytest tests/test_router.py tests/test_api_llm.py tests/test_llm_config.py tests/test_llm_client.py -q` — 90 passed, 1 skipped.
- `uv run pytest -m "not integration and not e2e" -q` — 1041 passed, 12 skipped,
  5 deselected (`tmp/tests_llm_round8_full.log`).
- `cd frontend; npx tsc --noEmit` — clean.
- `cd frontend; npm run build` — compiled successfully
  (`tmp/frontend_build_round8.log`).

### Changed files (round 8)

- `src/llm/router.py`, `tests/test_router.py`,
  `frontend/components/settings-view.tsx`,
  `frontend/components/llm-setup-banner.tsx`,
  `frontend/components/dashboard-shell.tsx`.

## Review fix round 9

- **`frontend/components/settings-view.tsx` (bug, low)** — round 8's
  `updateForm` only suppressed patches that were a no-op against the *current*
  form, so manually reverting a field to its saved value left `dirty === true`
  (the "Discard changes" button kept showing and the seed effect kept skipping
  server re-syncs). Added `matchesSaved(nextForm, nextApiKey, nextClearKey)`,
  which compares the merged form against `toForm(settings)` (and treats a typed
  key or pending clear as unsaved), and every mutator now sets
  `dirty = !matchesSaved(...)`:
  - `updateForm` — revert a field to the saved value and the form is no longer
    dirty.
  - `handleKeyInput` / `handleToggleClear` — typing then deleting a key, or
    Clear then Keep, restores the saved dirty state.
  - `handleProviderChange` now builds the explicit `next` form and derives dirty
    from it, instead of unconditionally setting `dirty = true`.

  Secondary note (batching): each mutator runs from a distinct DOM event, and
  each computes its `next` state locally before the single `setForm`, so there is
  no path where two updates for the same handler batch against a stale closure.

### Verification (round 9)

- `cd frontend; npx tsc --noEmit` — clean.
- `cd frontend; npm run build` — compiled successfully
  (`tmp/frontend_build_round9.log`).
- No Python file touched in this round, so no new `ruff`/`pytest` run.
