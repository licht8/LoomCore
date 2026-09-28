# TZ_RU.md — Collapsible Chats panel as a third column

## 1. Goal

Turn the chat/conversations list into a **persistent, collapsible second column**
of the dashboard layout, so the desktop dashboard reads as three columns:

```
┌──────────┬──────────────┬─────────────────────────┐
│ Loom     │ Chats        │ LoomCore Assistant      │
│ Panel    │ [+ New] [«]  │ Conversational...       │
│          │              │                         │
│ • AI     │ ─────────    │                         │
│ • Over.. │ Chat 1       │   [messages]            │
│ • Tools  │ Chat 2       │                         │
│ • ...    │ Chat 3       │                         │
│          │ ...          │                         │
│ ─────    │              │                         │
│ Settings │              │   [input]               │
└──────────┴──────────────┴─────────────────────────┘
```

As part of the same change, fix the **structural** bug where the chats list
renders on top of the "LoomCore Assistant" header (overlapping its title and
subtitle). This is a layout/ownership bug, not a styling bug.

Scope is **`frontend/` only**. The backend is not touched.

## 2. Non-goals

- No backend change of any kind.
- No change to the LLM provider settings feature or any of its files.
- No resize handle between columns (separate future task).
- No per-chat actions beyond delete (already present): no rename, pin, archive.
- No full mobile drawer; below the breakpoint the panel is simply collapsed by
  default and toggleable.
- No restyle of the sidebar or the main content.
- No change to the chat list data fetching, sorting, or filtering.
- No new dependencies.
- No per-message or per-tool behavior changes.

## 3. Approved decisions (locked)

- **D1 — Three-column shell.** `DashboardShell` renders, as siblings in its
  existing flex row: `(1)` `DashboardSidebar` (unchanged), `(2)` the new
  `ChatsPanel` (column 2), `(3)` `<main>` holding the `DashboardHeader`,
  `LlmSetupBanner`, and `{children}`. The chats panel is **moved out of
  `chat-room.tsx`** and out of `<main>`.

- **D2 — Header ownership (overlap fix).** There is exactly one `LoomCore
  Assistant` header: `frontend/components/dashboard-header.tsx:7`. A repo-wide
  grep confirms no duplicate header component exists in the UI. The overlap is
  caused by the conversations rail being rendered **inside `<main>` underneath
  the absolutely-positioned header** (`DashboardHeader` is `absolute inset-x-0
  top-0 z-20` inside `relative` `<main>`). Fix structurally: the header stays a
  child of `<main>` only (it already spans column 3 only once the rail is no
  longer inside `<main>`), and the chats panel becomes a flex sibling of
  `<main>`. Do **not** leave any element that overlays the header.

- **D3 — Panel chrome.** `ChatsPanel` uses the same surface idiom as the
  sidebar: `glass-sidebar rounded-2xl` (the shell's `gap-4` plus each surface's
  hairline border already separates the three columns — so a literal `border-r`
  is not needed; this is the "close variant" allowed by the brief). Width is
  **`w-[260px]` expanded** and **`w-11` (44px) collapsed**. It scrolls
  independently (`overflow-y-auto` on the list only).

- **D4 — Panel header band.** The panel has its own small header band of height
  `h-[var(--header-h)]`, matching the sidebar brand band and the main header so
  all three read as one horizontal band. It contains: a `Chats` label
  (`text-xs font-medium uppercase tracking-wide text-muted-foreground`), a `+`
  button (new chat), and a collapse toggle
  (`PanelLeftClose` when expanded / `PanelLeftOpen` when collapsed, from
  `lucide-react`). Reuse the existing `--header-h` token defined by
  `DashboardShell` (`81px`); do **not** introduce a new constant.

- **D5 — Collapsed representation = (b) narrow strip.** Collapsed width is
  `w-11` (44px): the strip shows only the expand toggle (`PanelLeftOpen`) and a
  `MessageSquare` icon; clicking the strip or the toggle expands the panel.
  Confirmed with the owner.

- **D6 — Collapse state lives in `DashboardShell`** (not inside the panel), so
  the main content reflows and expands when collapsed. Persisted to
  `localStorage` under the key **`loomcore_chats_collapsed`** (`"1"`/`"0"`).
  Read in a mount effect (never during render) to avoid a hydration mismatch.

- **D7 — Animation.** Animate the panel `width` with a **~200ms ease-out CSS
  transition** (`transition-[width] duration-200 ease-out`), and
  `overflow-hidden`. Never use `display: none` (it kills the animation).
  Respect reduced motion: add `motion-reduce:transition-none` so it toggles
  instantly.

- **D8 — Responsive breakpoint = `lg` (1024px).** Below `lg` the panel is
  **collapsed by default** (still toggleable to the expanded width; no drawer).
  A stored `loomcore_chats_collapsed` value overrides the default.

- **D9 — Single chat state via a dashboard-level provider.** Session state
  (sessions list, active session id, select/create/delete, busy) currently lives
  in `useChat`, called inside `ChatRoom` on `/` only. Lift a **single `useChat`
  instance** into a new client provider (`ChatProvider` + `useChatContext()`)
  mounted by `DashboardShell`, consumed by both `ChatsPanel` and `ChatRoom`.
  Confirmed with the owner. Consequence: the chat WebSocket and feed now stay
  mounted across all dashboard routes (this matches the existing intent comment
  in `app/(dashboard)/layout.tsx`).

- **D10 — Deep link handled client-side.** The `?session=` deep link can no
  longer be passed from the page up to a layout-level provider (Next layouts do
  not receive `searchParams`). `ChatProvider` adopts `window.location.search`'s
  `session` param once on mount via `selectSession(id)` (persisting it), falling
  back to `getSessionId()`. `app/(dashboard)/page.tsx` reverts to a plain server
  component returning `<ChatRoom />` (drop the `searchParams`/`initialSessionId`
  prop); `/` becomes static again.

- **D11 — Frontend-only, no file deletion.** Do not delete `session-list.tsx`;
  repurpose it. Verification is `npx tsc --noEmit` + `npm run build` + the
  manual checks in §7 (there is no frontend test runner).

## 4. Data model / interfaces

No backend, database, or wire-protocol change. The relevant client contracts are
unchanged:

- `useChat()` (frontend/lib/use-chat.ts) already returns everything the panel
  needs: `sessions`, `sessionsLoading`, `sessionId`, `selectSession`,
  `createSession`, `deleteSession`, `refreshSessions`, and `isThinking`/`isSending`.
- `SessionSummary` (frontend/lib/chat-types.ts): `{ session_id, title, preview,
  message_count, last_message_at }`.
- New context surface (shape locked):

  ```ts
  // frontend/lib/chat-context.tsx
  "use client"
  export function ChatProvider({ children }: { children: ReactNode }): JSX.Element
  // Returns the full object returned by useChat(), plus nothing else.
  export function useChatContext(): ReturnType<typeof useChat> // throws outside the provider
  ```

## 5. Parts

### Part 1 — Chat state provider (`frontend/lib/chat-context.tsx`, new)
- `"use client"`. `ChatProvider` calls `useChat()` once and exposes the result
  through a React context. `useChatContext()` throws a clear error if used
  outside the provider.
- On mount, adopt `?session=` (D10): a one-time effect reads
  `new URLSearchParams(window.location.search).get("session")`; if present and
  different from the current `sessionId`, call `selectSession(id)` (it is a
  no-op only while busy; at mount it is not busy). Keep it client-only so SSR
  renders deterministically.

### Part 2 — `DashboardShell` (`frontend/components/dashboard-shell.tsx`)
- Add `"use client"`.
- Own the collapse state: `const [collapsed, setCollapsed] = useState(false)`;
  a mount effect initializes it from `localStorage.getItem("loomcore_chats_collapsed")`
  and, when there is no stored value, from `window.innerWidth < 1024` (D8); a
  setter that also writes the key.
- Wrap the shell body in `<ChatProvider>`. Render
  `<DashboardSidebar />`, `<ChatsPanel collapsed={collapsed} onToggle={…} />`,
  then `<main>` (unchanged classes, header/banner/children as today).
- Keep `style={{ "--header-h": "81px", "--banner-h": "2rem" }}`.

### Part 3 — `ChatsPanel` (`frontend/components/chats-panel.tsx`, new)
- `"use client"`. Props: `{ collapsed: boolean; onToggle: () => void }`.
- Container: `glass-sidebar rounded-2xl` + `shrink-0 overflow-hidden
  transition-[width] duration-200 ease-out motion-reduce:transition-none` and
  `w-[260px]` / `w-11` by `collapsed`. `Flex flex-col`.
- When **expanded**:
  - Header band `h-[var(--header-h)]`: `Chats` label, `+` (new chat) button, and
    the collapse toggle (`PanelLeftClose`).
  - A hairline divider (`border-t border-border/50`) under the band, matching the
    sidebar's divider idiom.
  - A body with `overflow-y-auto` that renders the conversation rows (compose
    `SessionList`, Part 4) and, when the list is empty and not loading, a minimal
    empty state: "No chats yet" + a button to start one. No illustration. No
    mount/stagger animation.
  - Row actions are blocked while a turn is in flight: pass `disabled =
    isThinking || isSending` from the context.
- When **collapsed**: render only the narrow strip — a full-height vertical flex
  with the expand toggle (`PanelLeftOpen`) and a `MessageSquare` icon; the whole
  strip is clickable and calls `onToggle`. Do not render the list (it is not
  needed while collapsed).
- Use context via `useChatContext()` for `sessions`, `sessionsLoading`,
  `sessionId`, `selectSession`, `createSession`, `deleteSession`, `isThinking`,
  `isSending`.

### Part 4 — `SessionList` (`frontend/components/session-list.tsx`)
- Reduce to a **rows-only** list (no `<aside>` surface, no panel header, no
  fixed width — the surface/header/collapse now belong to `ChatsPanel`).
- Keep the existing delete `AlertDialog` and the row content (title bold
  truncated, preview muted truncated one line, timestamp small muted).
- Adopt the sidebar's interaction idiom: rows use `h-10`-class padding
  (`px-3`), `rounded-md`, `hover:bg-foreground/[0.03]`, and the active row gets
  the 2px left bar + `font-medium text-foreground` (mirror
  `dashboard-sidebar.tsx` `itemClass`). Do not introduce new row styling.

### Part 5 — `ChatRoom` (`frontend/components/chat-room.tsx`)
- Consume `useChatContext()` instead of calling `useChat()`.
- Remove the `<SessionList …>` column and the `SessionList` import; render only
  the chat column (messages scroller + `ChatInput`). Keep all auto-scroll /
  `ResizeObserver` / stick-to-bottom logic and props unchanged.
- Drop the `initialSessionId` prop (the provider owns session selection now).

### Part 6 — Page (`frontend/app/(dashboard)/page.tsx`)
- Revert to the plain server component: `export default function Page() {
  return <ChatRoom /> }` (no `searchParams`, no `async`).

### Part 7 — Header
- `frontend/components/dashboard-header.tsx` needs no change (it stays a child
  of `<main>`). Confirm after the reflow that it spans column 3 only and is
  never overlapped.

## 6. Tests

There is no frontend test runner in this repo (verification is type-check +
build + manual — see §7). No backend tests are affected. Per RULES 0a the
executor reports; the coordinator commits after acceptance.

## 7. Acceptance criteria

Gates (run from `frontend/`):

- `npx tsc --noEmit` — clean.
- `npm run build` — succeeds (Next.js; `/` may be static again per D10).

Manual, at the desktop breakpoint (`>= lg`):

- The "LoomCore Assistant" header is fully visible, never overlapped by the
  chats panel.
- Three columns read as three distinct surfaces with visible hairline borders
  (Loom Panel unchanged, Chats, main).
- The Chats header band height matches the main header height (both use
  `--header-h`).
- The collapse toggle animates smoothly (~200ms) and the main content expands
  to fill the freed width; there is no `display: none` flash.
- After a reload, the collapsed/expanded state is restored from
  `loomcore_chats_collapsed`.
- Navigating between `/` and `/plugins` does not remount the chats panel and
  does not reset its scroll position (it is chrome in `DashboardShell`).
- Below `lg` the panel starts collapsed and can be toggled.
- `prefers-reduced-motion: reduce` disables the width animation (instant toggle).
- The `?session=` deep link still selects the right conversation.
- Selecting, creating, and deleting conversations still work; those actions are
  disabled while a turn is in flight.

Report back: chosen breakpoint (`lg`/1024px), panel width (`260px` expanded /
`44px` collapsed), and collapsed representation (narrow strip, option b).

## 8. Commits

The executor commits nothing (RULES 0a). The coordinator commits after
acceptance; one cohesive commit is expected, e.g.:

```
feat(frontend): move chats into a collapsible dashboard column
```

## 9. Risks

- **Provider lift changes WS lifetime.** The chat WebSocket now connects on every
  dashboard route and persists across navigation (intended; matches the layout
  comment). Watch for duplicate connections during dev HMR.
- **Hydration.** Collapse state and the `?session=` adoption must be read in
  effects, not during render, to avoid SSR/client mismatch. Default is expanded;
  the effect corrects it. A brief initial layout shift on mobile is acceptable.
- **Deep link moves client-side** (D10): `page.tsx` stops reading `searchParams`,
  so `/` becomes static; verify the deep link still works from `/approvals`
  ("Open in chat") and the audit log.
- **Visual separation.** Locked to per-column rounded glass surfaces (D3); if
  the result does not read as three columns, add a hairline `border-r` on a
  non-rounded variant — but keep the surface material consistent.
- **Scope creep.** Resist adding per-chat actions, a resize handle, or a mobile
  drawer; all are explicitly out of scope.
