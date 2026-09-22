# TZ_RU.md — LLM Settings tab hardening

Task id: `llm-settings-ui-hardening`. Follow RULES.md (English only, no comments
unless existing style has them, ruff clean, tests mandatory). Executor does NOT
commit; the coordinator commits after acceptance (RULES 0a). Frontend only — the
backend and the LLM settings contract are frozen. Read `frontend/AGENTS.md`
before editing (Next 16).

## 1. Goal

Fix six low-severity review findings in the LLM Settings UI:

1. `frontend/lib/llm-settings-api.ts:80-81` — a 2xx response is parsed with an
   unguarded `JSON.parse` and an empty body is cast `undefined as T`. A non-JSON
   2xx (e.g. an HTML page from an intermediary) throws a raw `SyntaxError`; an
   empty body makes `fetchLlmProviders` dereference `undefined` and throw a
   `TypeError`. This helper backs all three LLM endpoints.
2. `frontend/components/settings-view.tsx:94-112` — `Field` renders one native
   `<label>` around arbitrary children, so it wraps the `Select` trigger, the
   `Switch`, and the Key field's `Input` + `Button`. Nesting multiple interactive
   controls in one `<label>` relies on implicit first-descendant association and
   yields surprising focus/activation and ambiguous accessible names.
3. `frontend/components/settings-view.tsx:467-472` and `480-487` — the API-key
   clear/keep transition is duplicated between the input `onChange` and the
   button `onClick`; the two paths can drift.
4. `frontend/components/settings-view.tsx:145` — `const { data: providers = [] }`
   allocates a new array on every render while the query has no data, so the
   `useMemo(..., [providers])` at 169-172 recomputes each time.
5. `frontend/components/llm-setup-banner.tsx:12` — the banner is mounted in the
   shared `DashboardShell`, so it also shows on `/settings` (linking to the page
   the user is already on) and its spacer shifts that page down.
6. `frontend/components/settings-view.tsx:184-185` — after a successful `PUT`
   the cache is only invalidated and refetched. If the follow-up `GET` fails, the
   cached `settings` stays on the pre-save values while the form shows the new
   ones, so the `Configured` badge, `keyPlaceholder`, and `Discard changes`
   become inconsistent with what was saved.

## 2. Non-goals

- No backend, API-contract, or agent-loop change.
- No new dependency, no test framework introduced (none is configured).
- No restyling or layout redesign; do not touch translations/markup beyond what
  each finding requires.
- No rename of existing components, hooks, or query keys.

## 3. Approved decisions (locked)

### D1 — guard the success body parse (`requestJson`)
After the `!response.ok` check:
```ts
const text = await response.text()
if (!text) {
  throw new Error(`${errorPrefix} (empty response)`)
}
try {
  return JSON.parse(text) as T
} catch {
  throw new Error(`${errorPrefix} (invalid JSON response)`)
}
```
Both cases must surface the existing `errorPrefix` (e.g. "Failed to load
providers (invalid JSON response)") instead of `SyntaxError`/`TypeError`.

### D2 — `Field` must not wrap multiple controls in a `<label>`
Rework `Field` so the association is explicit. Use `useId()` for stable ids and
add a variant:
```ts
function Field({
  label, hint, children, htmlFor, group = false,
}: {
  label: string; hint?: string; children: React.ReactNode
  htmlFor?: string; group?: boolean
}) {
  const id = useId()
  const labelId = `${id}-label`
  const labelEl = (
    <span id={labelId} className="text-sm font-medium text-foreground">
      {label}
    </span>
  )
  const body = (
    <>
      {labelEl}
      {children}
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </>
  )
  if (group) {
    return (
      <div role="group" aria-labelledby={labelId} className="flex flex-col gap-1.5">
        {body}
      </div>
    )
  }
  return (
    <label htmlFor={htmlFor} className="flex flex-col gap-1.5">
      {body}
    </label>
  )
}
```
Rules for call sites:
- Plain `Input` fields (Base URL, Model, Summary model, `NumberField`) keep the
  label variant and pass a matching `htmlFor`/`id` (or rely on the single
  control — one labelable child is acceptable).
- Provider (`Select`), Key (`Input` + `Button`), and any field with more than one
  control use `group`; give the child control an explicit accessible name
  (`aria-label` or `aria-labelledby={labelId}` / an `id` passed down). base-ui
  primitives forward `aria-*`, so the `SelectTrigger`/`Input` can receive them.
- The two raw `<label>` wrappers around `Switch` (Enable model thinking,
  Send thinking template) must become `<div>`s with the visible text kept as a
  `<span>`, and the `Switch` given an explicit accessible name
  (`aria-label="Enable model thinking"` / `"Send thinking template"`).
- Net requirement: no `<label>` element contains more than one interactive
  control, and every control has an explicit accessible name.

### D3 — single key-change handler
Extract the duplicated transition and call it from both paths:
```ts
function handleKeyInput(value: string) {
  setDirty(true)
  resetTransientState()
  setApiKey(value)
  if (value) setClearKey(false)
}

function handleToggleClear() {
  setDirty(true)
  resetTransientState()
  setApiKey("")
  setClearKey(!clearKey && settings?.api_key.configured === true)
}
```
The `Input onChange` calls `handleKeyInput(event.target.value)`; the Clear button
`onClick` calls `handleToggleClear()`. Behaviour is unchanged.

### D4 — stable providers default
Hoist a module-level constant and keep the memo dependency stable:
```ts
const EMPTY_PROVIDERS: ProviderPreset[] = []
...
const { data: providersData, error: providersError } = useQuery({
  queryKey: ["llm-providers"],
  queryFn: fetchLlmProviders,
})
const providers = providersData ?? EMPTY_PROVIDERS
```
`useMemo(..., [providers])` may stay; `providers` is now referentially stable
while loading/empty. (`ProviderPreset` is already exported from
`@/lib/llm-settings-api`.)

### D5 — hide the banner on `/settings`
```ts
import { usePathname } from "next/navigation"
...
const pathname = usePathname()
if (!data || data.configured || pathname === "/settings") return null
```
No other behaviour changes; the banner still renders on the other dashboard
routes.

### D6 — seed the cache from the mutation result
In `saveMutation.onSuccess`, replace both invalidations with:
```ts
queryClient.setQueryData(["llm-settings"], updated)
```
Drop the `["llm-providers"]` invalidation entirely — the presets are static and
are already fetched separately (`providersError` retry keeps its own
invalidation). Keep `setForm(toForm(updated))` and the rest of the success
handler. This removes the extra round-trip and the stale window where `settings`
lagged behind the form.

## 4. Data model / interfaces

None. The HTTP contract and the `LlmSettings` / `LlmSettingsUpdate` /
`LlmTestResult` / `ProviderPreset` types are unchanged; only error messages for
malformed 2xx bodies are added.

## 5. Parts

1. `frontend/lib/llm-settings-api.ts` — D1.
2. `frontend/components/settings-view.tsx` — D2, D3, D4, D6.
3. `frontend/components/llm-setup-banner.tsx` — D5.

## 6. Tests / verification

The frontend has no unit-test runner (`package.json` has only dev/build/start),
so verification is static + build:

```
cd frontend
npx tsc --noEmit
npm run build
```

(`next.config.mjs` sets `typescript.ignoreBuildErrors: true`, so `tsc` is the
authoritative type gate.) Additionally verify by inspection:
- `requestJson` has no bare `JSON.parse` outside a `try`, and no
  `undefined as T` cast remains.
- No `<label>` in `settings-view.tsx` wraps more than one interactive control.
- `rg -n 'queryClient.invalidateQueries' frontend/components/settings-view.tsx`
  shows only the two load-error retry handlers, not the save path.
- The banner returns `null` when `usePathname() === "/settings"`.

No backend/pytest run is required, but run `uv run ruff check .` if any Python
file is touched (none should be).

## 7. Acceptance criteria

- A 2xx response with a non-JSON body and a 2xx response with an empty body both
  produce a friendly `${errorPrefix} (…)` `Error` — never `SyntaxError` or
  `TypeError`.
- Every control in the Settings form has an explicit accessible name and no
  `<label>` wraps more than one interactive element.
- The key input and the Clear/Keep button share one transition handler.
- `providers` is referentially stable while the query is loading/empty.
- The setup banner is not rendered on `/settings`.
- After a successful save the `["llm-settings"]` cache is seeded with the
  response; no `["llm-providers"]` invalidation runs on save.
- `npx tsc --noEmit` clean and `npm run build` succeeds.
- No edits outside the three files in §5.

## 8. Commits (coordinator commits only after acceptance, RULES 0a)

1. `fix(frontend): guard LLM settings responses and seed the cache from the mutation`
2. `refactor(frontend): make LLM settings labels accessible and de-duplicate key handling`

## 9. Risks

- `useId()` and `usePathname()` are React/Next APIs already used in the app
  (`admin-gate.tsx`, sidebar); no new dependency.
- `setQueryData` assumes the `PUT` response shape equals the `GET` shape — it
  does (`_serialize` on both), and `LlmSettings` types it.
- Accessibility changes are markup-only; the base-ui `Select`/`Switch` forward
  `aria-*`, but if a primitive swallows a prop, fall back to an `id` +
  `aria-labelledby` pair on a wrapper.
