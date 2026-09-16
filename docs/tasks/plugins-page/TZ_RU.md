# TS: Plugins page and expanding plugin tiles

> Filename follows the `docs/tasks/<name>/TZ_RU.md` convention (RULES 16); the
> content is English per RULES 0, matching the newest specs such as
> `docs/tasks/host-observability/TZ_RU.md`.

## Goal

Add a bottom **"Plugins"** entry to the dashboard sidebar (active on its own
route) and a real `/plugins` page of plugin tiles whose detail slides out
smoothly inline. Each tile exposes **Enabled/Disabled** state and
**Enable / Disable / Reload** actions with confirmation, so an admin can see and
operate plugin lifecycle from the Web UI — the same lifecycle the REST API and
the `core_*` meta-tools already expose.

## Non-goals

- No new backend route: reuse `GET /api/plugins/status` and the existing
  `POST /api/plugins/{id}/enable|disable|reload`.
- No plugin logic changes and no new plugin (RULES 1); the core edit is a
  metadata-only status extension.
- No ru/en dictionaries yet — the app is English-only; the RULES 12 i18n gap is
  flagged as a follow-up task.
- No file deletions; the `frontend/AGENTS.md` Next block stays untouched.
- No change to `/` chat behaviour, the WebSocket flow, or the LLM.

## Context / findings that shape the spec

- `GET /api/plugins/status` (`src/api/plugins.py:17`) returns
  `get_plugin_status()` = `{name, state, pid, restart_attempt, is_healthy}` only —
  no `enabled`, so the UI cannot tell a **disabled** plugin from a **crashed**
  one.
- `ManagedPlugin` (`src/plugins/manager.py:79-96`) already carries `enabled`,
  `version`, `manifest` (id/name/version/description/permissions), `tools`,
  `permission_warnings`. Exposing them is a tiny method edit, no new route
  (RULES 10).
- Lifecycle already exists: `POST /api/plugins/{id}/enable|disable|reload`
  (`src/api/plugins.py:68-116`, admin, 10/min) → matches the requested tile
  actions + RULES 13 approvals.
- There is no router today: `frontend/app/page.tsx` renders the shell
  (`AdminGate` → sidebar + main + header + `ChatRoom`) inline. The sidebar
  (`frontend/components/dashboard-sidebar.tsx`) is a server component with
  hardcoded non-clickable nav and a static `mt-auto` LoomWG card.
- `@base-ui/react` ships a real `Collapsible` (Root/Trigger/Panel) exposing
  `data-open`/`data-closed` + `--collapsible-panel-height` — the clean,
  accessible way to do the smooth slide-out.
- `frontend/lib/hub-status.ts` already defines a `PluginStatus` type and
  `fetchHubSnapshot()`; the new API layer must not duplicate it blindly.
- Confirmed tests pin the status dict: `tests/test_plugin_manager.py:316` (exact
  `==` on the missing-plugin dict) and `:828` — both must be updated.

## Part 1 — Core: expose lifecycle + manifest metadata (`src/plugins/manager.py`)

Extend `get_plugin_status()` (`manager.py:623`); no route or response-envelope
change.

- Add to the present-plugin return: `enabled`, `version`, `description` (from
  `manifest.get("description", "")`), `tools` (`len(plugin.tools)`),
  `permission_warnings`.
- Mirror safe defaults in the missing-plugin branch: `enabled: False`,
  `version: ""`, `description: ""`, `tools: 0`, `permission_warnings: []`.
- Keep `name`, `state`, `pid`, `restart_attempt`, `is_healthy` unchanged.
- Update the two pinned tests: `tests/test_plugin_manager.py:316` (extend the
  exact dict) and `:828` (assert `enabled is False`).

## Part 2 — Frontend routing/shell (no route group, no file moves)

- New `frontend/components/dashboard-shell.tsx` (server component) holding the
  exact current wrapper:
  `<div className="flex h-screen gap-4 overflow-hidden p-4">` +
  `<DashboardSidebar />` +
  `<main className="glass relative flex flex-1 flex-col overflow-hidden rounded-3xl">`
  `<DashboardHeader />{children}</main>`.
- `app/page.tsx` becomes
  `<AdminGate><DashboardShell><ChatRoom /></DashboardShell></AdminGate>`
  (byte-identical output).
- New `app/plugins/page.tsx` →
  `<AdminGate><DashboardShell><PluginsView /></DashboardShell></AdminGate>`.

## Part 3 — Sidebar (`frontend/components/dashboard-sidebar.tsx` → `"use client"`)

- `NavItem = { label: string; icon; href?: string }`; use `usePathname()` from
  `next/navigation`; render `next/link` when `href` is set, a plain `<button>`
  otherwise.
- **AI Assistant** → `/`; **System Status** stays a non-navigating button (out
  of scope).
- New **Plugins** item (lucide `Plugins` icon, `href="/plugins"`) in the
  `mt-auto` block above the LoomWG card; same styling; active when
  `pathname === "/plugins"`; `aria-current="page"` when active.
- Preserve the existing static LoomWG card markup.

## Part 4 — API layer — new `frontend/lib/plugins-api.ts`

- `PluginInfo` type (status fields + the Part 1 additions).
- `fetchPlugins()` — `GET /api/plugins/status`, `authHeaders()` +
  `credentials: "include"`, sorted by `name`.
- `enablePlugin(id)` / `disablePlugin(id)` / `reloadPlugin(id)` — `POST` to the
  existing routes, throw on `!response.ok`.
- Reuse `getApiBase()` (`frontend/lib/chat-api.ts:23`) and `authHeaders()`
  (`frontend/lib/admin-token.ts`).

## Part 5 — Plugins UI

- `frontend/components/plugins-view.tsx` (client): scroll container
  `flex-1 overflow-y-auto px-6 pt-24 pb-10` (clears the absolute header),
  title/count, Refresh, responsive grid `sm:grid-cols-2 xl:grid-cols-3`,
  skeleton loading, error banner + Retry, empty state (RULES 11), 20s poll;
  owns the confirm-dialog target + per-action pending state.
- `frontend/components/plugin-tile.tsx` (client): Base UI `Collapsible.Root`;
  `Collapsible.Trigger` = header (name, version, status badge, health dot,
  rotating chevron); `Collapsible.Panel` = detail (PID, restarts, tool count,
  enabled, permission warnings) animated via the panel's
  `--collapsible-panel-height` + `data-open`/`data-closed` transition; action
  buttons **Enable** (if disabled) / **Disable** / **Reload** (if enabled).
- Confirm every mutation with the existing `frontend/components/ui/alert-dialog.tsx`
  (RULES 13; reference usage in `frontend/components/chat-input.tsx:246`),
  disable buttons while in-flight (10/min limit), inline error on failure,
  refetch on success.

## Acceptance criteria

1. The bottom **Plugins** button navigates and reflects active state; **AI
   Assistant** is active on `/`.
2. `/plugins` renders inside the shared dashboard shell.
3. Tiles expand/collapse smoothly with `aria-expanded`.
4. Lifecycle actions are confirmed and refresh status on success.
5. Status exposes `enabled` (+ `version`/`description`/`tools`/`permission_warnings`)
   with the pinned tests updated.
6. `npx tsc --noEmit` + `npm run build` + `ruff check .` +
   `pytest -m "not integration and not e2e"` all green.
7. No new route, plugin logic untouched, no files deleted.

## Commits

1. `docs(tasks): add plugins page and expanding tiles spec`
2. `feat(core): expose plugin lifecycle metadata in status`
3. `feat(frontend): add shared dashboard shell and plugins route`
4. `feat(frontend): add plugins page with expanding tiles`

## Risks

- The sidebar client conversion affects the shell for every page (mitigated:
  identical markup).
- The 20s status poll and lifecycle actions share the 10/min limit.
- Base UI `Collapsible` animation must be verified in the browser.
- Adding status keys can break the two tests pinned to the exact status dict
  (`tests/test_plugin_manager.py:316`, `:828`).
