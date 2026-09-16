# Report — plugins page

Task spec: `docs/tasks/plugins-page/TZ_RU.md`.

## Goal

Add a sidebar **Plugins** route and a `/plugins` admin page that lists hub
plugins and lets the admin enable, disable and reload them (confirmation
required). The final design is a dense single-column **row list** in the spirit
of Linear/Vercel, replacing the earlier square card grid.

## Delivered

Backend:

- `src/plugins/manager.py` — `get_plugin_status()` additionally returns
  `enabled`, `version`, `description`, `tools`, `permission_warnings`; the
  missing-plugin branch returns safe defaults (`enabled: False`, `""`, `""`,
  `0`, `[]`). Existing keys are unchanged and no new route was added.
- `tests/test_plugin_manager.py` — the exact status dict (~line 316) and
  `test_get_plugin_status_disabled_has_no_pid` (~line 828) updated for the new
  keys.

Frontend:

- `frontend/lib/plugins-api.ts` — `PluginAction`, `PluginInfo`,
  `fetchPlugins()`, `enablePlugin()`, `disablePlugin()`, `reloadPlugin()`.
- `frontend/components/dashboard-shell.tsx` — shared dashboard chrome
  (sidebar + header), reused by `/` and `/plugins`.
- `frontend/app/plugins/page.tsx` — `AdminGate > DashboardShell > PluginsView`.
- `frontend/app/page.tsx` — re-pointed to `DashboardShell`.
- `frontend/components/dashboard-sidebar.tsx` — client component with
  pathname-aware `next/link` nav and a bottom **Plugins** item.
- `frontend/components/plugins-view.tsx` — page header (`Plugins` + mono
  `N plugins · K running` counter + ghost icon Refresh), a thin destructive
  error line with a text Retry, flat skeleton rows, a one-line empty state and
  a   `divide-y divide-border` list. 20s poll, refetch after a mutation and
  per-plugin pending state are preserved. Enable, disable and reload run
  immediately with no confirmation dialog (owner decision; RULES 13
  confirmation intentionally waived for this admin surface).
- `frontend/components/plugin-row.tsx` — one ~64px row: status dot (emerald /
  amber / muted), name + optional `TriangleAlert` warnings tooltip, mono meta
  line (`v0.2.0 · 5 tools` / `no version · 0 tools`), uppercase mono status
  (`RUNNING` / `BUSY` / `STOPPED` / `DISABLED`), `Switch` and a ghost Reload
  icon button. Flat: no rounded cards, glass, shadow or badges.
- `frontend/components/ui/switch.tsx` — Base UI switch (gray/emerald, animated
  thumb, `role="switch"`).
- `frontend/app/globals.css` — `@keyframes plugin-row-in` + `.plugin-row-in`
  (160ms ease-out, `backwards`) with a `prefers-reduced-motion: reduce` opt-out;
  the first 8 rows stagger via an inline `animationDelay`, animation runs only
  on first mount.

The rename `plugin-tile.tsx` → `plugin-row.tsx` was explicitly requested in the
task, so the old file was removed.

## Verified

- `npx tsc --noEmit` → clean.
- `npm run build` → ok, routes `/`, `/_not-found`, `/plugins`.
- `ruff check .` → All checks passed.
- `uv run pytest -m "not integration and not e2e" -q` → 608 passed,
  2 deselected.
- Visual check on `http://localhost:3000/plugins` (dev + local backend).

## Notes / risks

- No latency metric exists in `PluginInfo`, so the meta line omits the `12ms`
  example from the spec rather than showing a placeholder.
- The stack uses Base UI, not Radix/shadcn; `ui/switch.tsx` reproduces the
  required look and behaviour without adding a dependency.
- The shared shell and body background gradients are intentionally untouched to
  avoid changing the accepted chat page.
- UI strings are English-only; the RULES 12 ru/en dictionaries remain a
  follow-up.

Коммит не создавал
