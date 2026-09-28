# TZ_RU.md — merge the chats panel into the Loom Panel sidebar

## 1. Goal

Turn the two-column left area (Standalone `ChatsPanel` column + `DashboardSidebar`)
into ONE sidebar column with three vertical zones:

1. **Nav** (top, fixed) — the existing navigation items.
2. **Chats** (middle, `flex-1 min-h-0 overflow-y-auto`, scrolls on its own) — a
   `CHATS` header with a `+` (new chat) button, then the conversation rows.
3. **Settings** (bottom, fixed) — the existing Settings link.

Zones are separated by the same hairline used today
(`<div className="mx-3 border-t border-border/50" />`). There is no second
column, no second surface, no resize handle.

Reference: Notion / Linear sidebars — flat navigation on top, a content list
below, settings pinned at the bottom, hairlines between zones, one scrollable
middle region.

## 2. Non-goals

- No backend change. `frontend/` only.
- Do not touch the LLM provider settings feature or its files
  (`frontend/components/settings-view.tsx`, `frontend/lib/llm-settings-api.ts`,
  `frontend/components/llm-setup-banner.tsx`).
- Do not change `ChatProvider` shape, `useChat`, `ChatRoom` data flow, the WS,
  `?session=` adoption, or the plugins/usage/audit/approvals pages.
- Do not add a second column, a resize handle, per-chat rename/pin, or the
  `LoomCore Assistant` main header (it stays as-is in `dashboard-header.tsx`).
- No new dependencies.

## 3. Approved decisions (locked)

- **D1 — One column, three zones.** `aside` keeps its current material, width
  (`w-64`) and border (`glass-sidebar flex w-64 shrink-0 flex-col rounded-2xl
  px-3 pb-5`). Inside it: brand block → hairline → nav → *chats zone* → settings
  zone (hairline + Settings), all in one `flex flex-col`.
- **D2 — Chats zone scrolls, nav + settings do not.** The chats zone is
  `flex flex-1 min-h-0 flex-col` and its list container is
  `flex-1 min-h-0 overflow-y-auto`. Because it is `flex-1`, the hairline above
  Settings stays pinned to the bottom even when the list is short.
- **D3 — `CHATS` header row.** A fixed ~32px row (`flex h-8 shrink-0
  items-center justify-between gap-2`) with the label
  `CHATS` (`text-[10px] font-mono uppercase tracking-wider text-muted-foreground`)
  and a `+` new-chat icon button on the right (`Button variant="ghost"
  size="icon-sm"`, `Plus` from `lucide-react`, `disabled={busy}` where
  `busy = isThinking || isSending`). Below it a hairline
  (`mx-3 border-t border-border/50`). The header does not scroll.
- **D4 — Rows reuse the nav idiom exactly (no new row style).** Keep the
  existing `SessionList` rows as-is: `min-h-10 ... rounded-md px-3 py-2`,
  `hover:bg-foreground/[0.03]`, active = `font-medium text-foreground` + the
  `absolute ... w-0.5 bg-foreground/60` 2px left bar; title `text-sm
  font-medium truncate`, preview `text-xs truncate text-muted-foreground`,
  timestamp `text-[10px] text-muted-foreground/70` with `relativeTime` and
  `· N msgs`. `min-h-10` is kept (not `h-10`) because a row carries three lines.
  Do not add horizontal padding to the list container — rows already carry
  `px-3`, matching the nav rows (both sit inside the aside's `px-3`).
- **D5 — Empty state.** When not loading and there are no sessions: quiet,
  centered in the chats zone (when space allows): `No chats yet` plus a
  `Start a chat` button (`variant="outline" size="sm"`, `disabled={busy}`,
  calls `createSession`). No illustration. Reuse the copy from the current
  `ChatsPanel`.
- **D6 — Remove the separate column and its collapse.** Delete the column-2
  `ChatsPanel` usage and the chats-column collapse entirely:
  - `dashboard-shell.tsx` drops `ChatsPanel`, the `collapsed` state, the
    `useEffect` that reads storage, `toggleCollapsed`,
    `COLLAPSED_STORAGE_KEY = "loomcore_chats_collapsed"` and
    `DESKTOP_BREAKPOINT`. It keeps `<ChatProvider>` wrapping the shell.
  - No `loomcore_chats_collapsed` key remains anywhere in the repo.
  - There is currently **no** sidebar collapse to keep; do not add one. The
    final sidebar width is `w-64` (256px) and it does not collapse.
- **D7 — Chats zone lives in a dedicated section component.** Create
  `frontend/components/chats-section.tsx` (the middle zone: header + hairline +
  scrollable list + empty state; it calls `useChatContext()`). Import it from
  `dashboard-sidebar.tsx` as the middle zone. `session-list.tsx` stays rows-only
  and unchanged.
- **D8 — File disposition (RULES 11).** `frontend/components/chats-panel.tsx`
  is no longer used. Do not delete it in place: move it to
  `tmp/_trash/chats-panel.tsx` (`tmp/` is gitignored) with a one-line reason,
  and remove all imports of it.
- **D9 — `DashboardSidebar` stays presentational for nav.** It keeps
  `usePathname`, `navItems`, `settingsItem`, `itemClass`, `iconClass`, `NavLink`
  and the brand text `Loom Panel` exactly as today; it only gains the middle
  `<ChatsSection />` between the nav and the settings zone. The settings zone
  can drop its redundant `mt-auto` (the `flex-1` chats zone already pushes it
  down) — keep the hairline and `py-4`.

## 4. Data model / interfaces

No new types, no new context, no REST/WS change.

- `ChatsSection` consumes only the existing `useChatContext()` value:
  `sessions`, `sessionsLoading`, `sessionId`, `selectSession`, `createSession`,
  `deleteSession`, `isThinking`, `isSending`.
- `SessionList` props are unchanged (`sessions`, `activeSessionId`,
  `isLoading`, `disabled`, `onSelect`, `onDelete`).
- `ChatProvider` / `useChat` / `ChatRoom` / `?session=` adoption: unchanged.

## 5. Parts

1. `frontend/components/chats-section.tsx` (new) — chats zone markup from D3,
   D4, D5; `busy = isThinking || isSending`; renders `<SessionList .../>` when
   there are rows, the empty state otherwise; wrapper
   `flex flex-1 min-h-0 flex-col`, list
   `flex-1 min-h-0 overflow-y-auto`.
2. `frontend/components/dashboard-sidebar.tsx` — add `<ChatsSection />` between
   the `<nav>` and the settings zone; keep everything else (D1, D2, D9).
3. `frontend/components/dashboard-shell.tsx` — remove `ChatsPanel`, the collapse
   state/storage/breakpoint, and the `ChatsPanel` import (D6); keep
   `<ChatProvider>` and render `DashboardSidebar` → `main` only.
4. Move `frontend/components/chats-panel.tsx` → `tmp/_trash/chats-panel.tsx`
   (D8).

## 6. Tests

No frontend test runner exists; verification is:

- `cd frontend; npx tsc --noEmit` → clean.
- `cd frontend; npm run build` → succeeds.
- Repo grep: no `ChatsPanel`, no `loomcore_chats_collapsed`, no imports of
  `chats-panel`.

## 7. Acceptance criteria (locked)

- One left column only. Three visible zones with the existing hairline style
  between them (`mx-3 border-t border-border/50`).
- No overlap with the main `LoomCore Assistant` header.
- Nav zone does not scroll when the chat list is long; the chats zone scrolls
  independently; the Settings zone stays pinned at the bottom (also when the
  chat list is short).
- `CHATS` label uses `text-[10px] font-mono uppercase tracking-wider
  text-muted-foreground`; the `+` creates a new chat (disabled while busy).
- Clicking a chat switches the session without remounting `ChatRoom`.
- `/` → `/plugins` → `/` keeps the active session, messages and the WS
  connection.
- The old column is gone: no leftover component, no leftover
  `loomcore_chats_collapsed` key.
- `npx tsc --noEmit` clean and `npm run build` passes.

Executor report must state: final sidebar width, whether the sidebar itself can
still collapse, and whether the chats list uses `h-10` or `min-h-10`.

## 8. Commits

Executor commits nothing (RULES 0a). The coordinator commits after acceptance,
with a message such as
`refactor(frontend): merge chats into the Loom Panel sidebar`.

## 9. Risks

- **Starting state.** This builds on the uncommitted `chats-panel-column` work
  (`chats-panel.tsx`, `chat-context.tsx`, and the modified `dashboard-shell.tsx`,
  `session-list.tsx`, `chat-room.tsx`, `app/(dashboard)/page.tsx`). Keep the
  accepted parts (ChatProvider, rows-only `SessionList`, plain `page.tsx`);
  only the column-2 chrome and its collapse go away.
- **Sidebar vertical budget.** With nav + a `CHATS` header + a scrolling list +
  settings, the `flex-1 min-h-0` middle is what keeps settings pinned — do not
  drop `min-h-0`, or the chats list will push Settings off-screen.
- **Long titles/previews.** Rows must stay single-line truncated to avoid the
  list overflowing horizontally.
- **No sidebar collapse exists today**, so none is added; do not invent one.
