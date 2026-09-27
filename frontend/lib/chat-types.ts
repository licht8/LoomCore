// ===== Types for the WebSocket integration =====

/** Downloadable artifact attached to a tool result. */
export interface ArtifactLink {
  label: string
  url: string
}

/** Valid plan-step risks, exported as the single source of truth for the union. */
export const TOOL_RISKS = ["safe", "moderate", "dangerous"] as const

/** Risk class assigned to a plan step by the backend. */
export type ToolRisk = (typeof TOOL_RISKS)[number]

/** Lifecycle of a single plan step. */
export type PlanStepStatus = "pending" | "running" | "done" | "failed" | "blocked"

/** Lifecycle of a tool card in the UI feed. */
export type ToolStatus = "pending" | "success" | "error" | "cancelled"

/** Lifecycle of a whole plan. */
export type PlanStatus =
  | "proposed"
  | "approved"
  | "running"
  | "done"
  | "failed"
  | "rejected"
  | "cancelled"

/** One ordered action inside a plan. */
export interface PlanStep {
  id: string
  tool: string
  args: Record<string, unknown>
  why: string
  risk: ToolRisk
  status: PlanStepStatus
  error?: string
}

/** A first-class agent plan, produced before any tool runs. */
export interface Plan {
  type: "plan"
  plan_id: string
  revision: number
  goal: string
  steps: PlanStep[]
  status: PlanStatus
  mode?: "direct" | "plan"
  created_at: string
}

/** Plan awaiting the user's decision. */
export interface PlanRequest {
  id: string
  plan: Plan
}

/** Event arriving from the backend over WebSocket (discriminated on `type`). */
export type WsMessage =
  | { type: "thinking" }
  | { type: "response"; content?: string; text?: string; message_id?: string }
  | {
      type: "delta"
      message_id?: string
      content?: string
      reset?: boolean
    }
  | {
      type: "plan_progress"
      message_id?: string
      content?: string
      reset?: boolean
    }
  | {
      type: "tool_call"
      content?: string
      text?: string
      tool?: string
      args?: Record<string, unknown>
      summary?: string
      code?: string
      artifacts?: ArtifactLink[]
    }
  | {
      type: "tool_response"
      // In-flight tool responses may carry "running"; chat-ws coerces it to
      // "success" when building the tool card.
      status?: Exclude<ToolStatus, "pending"> | "running"
      content?: string
      text?: string
      error?: string
      summary?: string
      code?: string
      artifacts?: ArtifactLink[]
      format?: "markdown" | "text"
      compact?: boolean
    }
  | {
      type: "plan_proposed"
      plan?: Plan
      plan_id?: string
      revision?: number
      goal?: string
      requires_approval?: boolean
      content?: string
      summary?: string
    }
  | {
      type: "plan_step"
      plan_id?: string
      revision?: number
      status?: PlanStatus
      step?: PlanStep
      phase?: "started" | "done" | "failed"
    }
  | {
      type: "plan_done"
      plan?: Plan
      plan_id?: string
      revision?: number
      status?: PlanStatus
    }
  | { type: "schema_warning"; unavailable_plugins?: string[] }
  | { type: "cancelled" }
  | { type: "busy" }
  | { type: "error"; error?: string; text?: string; content?: string }

/** Chat message type in the UI feed */
export type ChatMessageRole = "user" | "assistant" | "tool" | "plan"

export interface ChatMessage {
  id: string
  role: ChatMessageRole
  content: string
  timestamp: string
  // For tool messages:
  tool?: string
  toolStatus?: ToolStatus
  toolSummary?: string
  toolCode?: string
  artifacts?: ArtifactLink[]
  // Tool-card rendering: Markdown body, and a compact card (header only) when
  // the raw output is already shown in the assistant bubble.
  format?: "markdown" | "text"
  compact?: boolean
  // For plan messages:
  plan?: Plan
}

/** One chat session summary returned by GET /api/sessions. */
export interface SessionSummary {
  session_id: string
  title: string
  preview: string
  message_count: number
  last_message_at: string
}

/** Internal state of the useChat hook (not exported to the UI) */
export interface UseChatState {
  messages: ChatMessage[]
  isThinking: boolean
  planProgress: string
  planRequest: PlanRequest | null
  error: string | null
  isSending: boolean
  wsReady: boolean
  sessions: SessionSummary[]
  sessionsLoading: boolean
}

/** Metadata stored inside a persisted message's `tool_calls` JSON. */
export interface StoredToolCall {
  name: string
  arguments?: Record<string, unknown> | null
  status?: Exclude<ToolStatus, "pending">
  summary?: string
  risk?: string
  artifacts?: ArtifactLink[]
  format?: "markdown" | "text"
  compact?: boolean
}

/** A stored message row returned by GET /api/sessions/{id}/messages. */
export interface SessionMessage {
  id: string
  // "system" rows are persisted but intentionally skipped during hydration.
  role: ChatMessageRole | "system"
  content: string
  created_at: string
  tool_calls?: StoredToolCall[] | null
  tool_result?: string | null
}
