import type { ChatMessage, Plan, PlanStatus, PlanStep, SessionMessage } from "@/lib/chat-types"

/** Format the current timestamp as a local HH:MM label. */
const PLANNER_TOOL_NAMES = new Set(["reply", "agent_plan"])

/** True when a stored assistant message is just a leaked planner tool name. */
function isBareToolName(content: string): boolean {
  const cleaned = content
    .trim()
    .replace(/^[`"']+|[`"']+$/g, "")
    .trim()
    .toLowerCase()
  return PLANNER_TOOL_NAMES.has(cleaned)
}

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

/** Insert or replace the card for a plan (keyed by plan_id, so revisions collapse). */
export function upsertPlanMessage(messages: ChatMessage[], plan: Plan): ChatMessage[] {
  const index = messages.findIndex(
    (m) => m.role === "plan" && m.plan?.plan_id === plan.plan_id,
  )
  if (index === -1) {
    return [
      ...messages,
      {
        id: `plan_${plan.plan_id}_${plan.revision}`,
        role: "plan",
        content: plan.goal,
        timestamp: formatTimestamp(),
        plan,
      },
    ]
  }
  const updated = [...messages]
  updated[index] = { ...updated[index], content: plan.goal, plan }
  return updated
}

/** Merge a single step update into its plan's card, keeping the plan status current. */
export function applyPlanStep(
  messages: ChatMessage[],
  planId: string,
  step: PlanStep,
  status?: PlanStatus,
): ChatMessage[] {
  return messages.map((message) => {
    if (message.role !== "plan" || message.plan?.plan_id !== planId) return message
    const plan = message.plan
    const steps = plan.steps.map((existing) =>
      existing.id === step.id ? { ...existing, ...step } : existing,
    )
    return { ...message, plan: { ...plan, steps, ...(status ? { status } : {}) } }
  })
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
 * - Plan rows are collapsed by plan_id so only the latest revision is shown.
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

    if (row.role === "plan") {
      flushDecision()
      const stored = Array.isArray(row.tool_calls) ? row.tool_calls[0] : null
      const plan = stored as unknown as Plan | null
      if (plan && plan.plan_id) {
        const message: ChatMessage = {
          id: row.id,
          role: "plan",
          content: plan.goal || row.content,
          timestamp,
          plan,
        }
        const existing = hydrated.findIndex(
          (m) => m.role === "plan" && m.plan?.plan_id === plan.plan_id,
        )
        if (existing >= 0) hydrated[existing] = message
        else hydrated.push(message)
      }
      continue
    }

    flushDecision()

    if (row.role === "user") {
      hydrated.push({ id: row.id, role: "user", content: row.content, timestamp })
    } else if (row.role === "assistant" && (row.content || "").trim()) {
      // Older sessions may have persisted a leaked planner tool name as text;
      // skip those rows so they never render as an assistant bubble.
      if (isBareToolName(row.content)) continue
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
