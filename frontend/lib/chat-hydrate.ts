import type { ChatMessage, Plan, PlanStatus, PlanStep, SessionMessage } from "@/lib/chat-types"

/** Planner tool names that, leaked as text, must never render as a reply. */
const PLANNER_TOOL_NAMES = new Set(["reply", "agent_plan"])

/** A planner tool call written as text: `<tool> { ...json... }`. */
const PREFIXED_TOOL_CALL = /^(?:reply|agent_plan|core_[a-z_]+)\s*(\{[\s\S]*\})$/

/** True when a stored assistant message is just a leaked planner tool name. */
function isBareToolName(content: string): boolean {
  const cleaned = content
    .trim()
    .replace(/^[`"']+|[`"']+$/g, "")
    .trim()
    .toLowerCase()
  return PLANNER_TOOL_NAMES.has(cleaned)
}

/** Parse `value` as a single JSON object, rejecting arrays and primitives. */
function parseJsonObject(value: string): Record<string, unknown> | null {
  try {
    const parsed: unknown = JSON.parse(value)
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>
    }
  } catch {
    // Not JSON — treat it as prose.
  }
  return null
}

/** True when an object has the minimum keys of a planner call. */
function isPlannerCallShape(obj: Record<string, unknown>): boolean {
  return Array.isArray(obj.steps) || typeof obj.goal === "string"
}

/**
 * True when a stored assistant message is a leaked planner tool call written as
 * text instead of being executed, e.g. `agent_plan { "steps": [...] }`. Such rows
 * must never render as an assistant bubble. Detection prefers the whole message
 * parsing as one JSON object (optionally after a tool-name prefix) so prose that
 * merely quotes a plan still renders, but also catches wrapped leaks the backend
 * itself recovers (`_recover_textual_plan_args`: `[tool name] {...}`, fenced
 * ```json blocks, trailing prose).
 */
function isLeakedToolCall(content: string): boolean {
  if (isBareToolName(content)) return true
  const trimmed = content.trim()
  const prefixed = trimmed.match(PREFIXED_TOOL_CALL)
  if (prefixed) return parseJsonObject(prefixed[1]) !== null
  const parsed = parseJsonObject(trimmed)
  if (parsed !== null && isPlannerCallShape(parsed)) return true
  // Leaked calls may be fenced, tagged (`[tool name] {...}`) or followed by
  // prose, so the whole-message parse above misses them. Only fall back when
  // such a wrapper is present, so plain prose that merely quotes a plan renders.
  return (
    /^\s*(?:\[tool\s+\w+\]|```)/.test(trimmed) && /"steps"\s*:\s*\[/.test(trimmed)
  )
}

/** Runtime guard for a persisted plan row (`tool_calls` JSONB is untyped). */
function isPlan(value: unknown): value is Plan {
  if (!value || typeof value !== "object") return false
  const plan = value as Record<string, unknown>
  return (
    typeof plan.plan_id === "string" &&
    typeof plan.goal === "string" &&
    typeof plan.revision === "number" &&
    typeof plan.status === "string" &&
    Array.isArray(plan.steps)
  )
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

/**
 * Apply a streamed assistant fragment to the message with `messageId`.
 * When `reset` is true the content is replaced (a re-ask restarted); otherwise
 * the fragment is appended. A missing message is appended, so a stream that
 * begins before any bubble exists still renders. An empty reset with no existing
 * bubble is a no-op: it would otherwise leave a stray empty message behind.
 */
export function appendAssistantDelta(
  messages: ChatMessage[],
  messageId: string,
  fragment: string,
  reset = false,
): ChatMessage[] {
  const index = messages.findIndex((m) => m.id === messageId)
  if (index === -1) {
    if (reset && !fragment) return messages
    return [
      ...messages,
      {
        id: messageId,
        role: "assistant",
        content: fragment,
        timestamp: formatTimestamp(),
      },
    ]
  }
  const updated = [...messages]
  const current = updated[index]
  updated[index] = {
    ...current,
    content: reset ? fragment : current.content + fragment,
  }
  return updated
}

/** Reconcile the final assistant text for a streamed message id. */
export function finalizeAssistant(
  messages: ChatMessage[],
  messageId: string,
  content: string,
): ChatMessage[] {
  return appendAssistantDelta(messages, messageId, content, true)
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
    const known = plan.steps.some((existing) => existing.id === step.id)
    const steps = plan.steps.map((existing) =>
      existing.id === step.id ? { ...existing, ...step } : existing,
    )
    // Unknown ids (e.g. a revised plan whose steps were renumbered) are appended
    // rather than silently dropped, so progress is never lost.
    if (!known) steps.push(step)
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
      const plan = stored as unknown
      if (isPlan(plan)) {
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
      // Older sessions may have persisted a leaked planner tool name or an
      // unwrapped tool call as text; skip those rows so they never render.
      if (isLeakedToolCall(row.content)) continue
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
