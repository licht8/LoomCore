import type { ChatMessage, SessionMessage } from "@/lib/chat-types"

/** Format the current timestamp as a local HH:MM label. */
export function formatTimestamp(): string {
  return new Date().toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })
}

/** Format a stored UTC timestamp as a local HH:MM label. */
export function formatTimestampFromIso(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ""
  return date.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })
}

/**
 * Resolve the newest still-pending tool message with the given patch.
 * Undefined patch values are ignored so existing fields are preserved.
 */
export function finalizePendingTool(
  messages: ChatMessage[],
  patch: Partial<ChatMessage>,
): ChatMessage[] {
  const clean = Object.fromEntries(
    Object.entries(patch).filter(([, value]) => value !== undefined),
  ) as Partial<ChatMessage>
  const updated = [...messages]
  for (let i = updated.length - 1; i >= 0; i--) {
    if (updated[i].role === "tool" && updated[i].toolStatus === "pending") {
      updated[i] = { ...updated[i], ...clean }
      break
    }
  }
  return updated
}

/** Build a tool card from a stored tool call. */
export function toolCardFromRow(row: SessionMessage, timestamp: string): ChatMessage {
  const meta = Array.isArray(row.tool_calls) ? row.tool_calls[0] : null
  const status = meta?.status ?? "success"
  return {
    id: row.id,
    role: "tool",
    content: row.content || "",
    timestamp,
    tool: meta?.name || "tool",
    toolStatus: status,
    toolSummary:
      meta?.summary ||
      (status === "error" ? "Error" : status === "cancelled" ? "Cancelled" : "Done"),
    toolCode: row.tool_result || "",
    artifacts: meta?.artifacts,
  }
}

/**
 * Convert stored DB rows into UI messages.
 * - Assistant rows with empty content are tool-call decisions; they are
 *   represented by the following tool row instead of a blank bubble.
 * - A tool-call decision with no following tool row (e.g. reload while an
 *   approval was pending) becomes an "Interrupted" card.
 */
export function hydrateMessages(rows: SessionMessage[]): ChatMessage[] {
  const hydrated: ChatMessage[] = []
  let pendingDecision: ChatMessage | null = null

  const flushDecision = () => {
    if (pendingDecision) {
      hydrated.push(pendingDecision)
      pendingDecision = null
    }
  }

  for (const row of rows) {
    const timestamp = formatTimestampFromIso(row.created_at)

    if (row.role === "tool") {
      pendingDecision = null
      hydrated.push(toolCardFromRow(row, timestamp))
      continue
    }

    flushDecision()

    if (row.role === "user") {
      hydrated.push({ id: row.id, role: "user", content: row.content, timestamp })
    } else if (row.role === "assistant" && (row.content || "").trim()) {
      hydrated.push({
        id: row.id,
        role: "assistant",
        content: row.content,
        timestamp,
      })
    } else if (row.role === "assistant") {
      const meta = Array.isArray(row.tool_calls) ? row.tool_calls[0] : null
      pendingDecision = {
        id: row.id,
        role: "tool",
        content: "",
        timestamp,
        tool: meta?.name || "tool",
        toolStatus: "cancelled",
        toolSummary: "Interrupted",
      }
    }
  }

  flushDecision()
  return hydrated
}
