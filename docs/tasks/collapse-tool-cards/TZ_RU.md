# TZ_RU.md — Collapse tool cards in the chat feed

## 1. Goal

The chat feed must show the assistant's message first; the raw tool output must
not be visible by default. Tool cards collapse to a single header line and expand
on click.

## 2. Non-goals

- No backend/protocol change: the `tool_response` payload (`format`, `compact`,
  `artifacts`) is unchanged.
- The tool status, download buttons and errors stay reachable.
- Plan cards and the assistant bubble are unchanged.

## 3. Approved decisions (locked)

- **D1** `frontend/components/chat-message.tsx`: extract the tool branch into a
  `ToolCard` component (client) that is collapsed by default. The header is a
  button (tool name + a `ChevronRight` that rotates when open + the status line);
  the output body renders only when expanded. A card without a body (compact) or
  with no content is not toggleable.
- **D2** Artifacts (e.g. WireGuard QR/config downloads) stay always visible under
  the header, so collapsing never hides a download.
- **D3** A tool that ends in `error` auto-expands (via an effect on
  `message.toolStatus`) so the failure is not hidden.

## 4. Data model / interfaces

No change. The card keeps using `message.tool`, `message.toolStatus`,
`message.toolSummary`, `message.toolCode`, `message.content`, `message.format`,
`message.compact`, `message.artifacts`.

## 5. Parts

1. `frontend/components/chat-message.tsx` — `ToolCard` component + collapsed
   default + auto-expand on error.

## 6. Tests

No frontend test covers `ChatMessageItem` (there is no React test setup); verified
with `npx tsc --noEmit` and `npm run build`.

## 7. Acceptance criteria

- A completed tool shows a one-line card (name + status); its output is hidden
  until the header is clicked.
- A failed tool is expanded automatically.
- Download buttons remain visible when collapsed.
- `npx tsc --noEmit` clean, `npm run test` green, `npm run build` succeeds.

## 8. Commits

- `feat(frontend): collapse tool cards in the chat feed`.

## 9. Risks

- Automatically expanding on error can be noisy for plans with many failed steps;
  the owner can still collapse each card.
- Collapsed-by-default hides useful raw output for power users; one click restores
  it (and an explicit "raw output" request still shows it in the assistant bubble).
