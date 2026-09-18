// ===== Types for the WebSocket integration =====

/** Downloadable artifact attached to a tool result. */
export interface ArtifactLink {
  label: string
  url: string
}

/** Risk class assigned to a plan step by the backend. */
export type ToolRisk = "safe" | "moderate" | "dangerous"

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
  | { type: "response"; content?: string; text?: string }
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
      status?: ToolStatus | "running"
      content?: string
      text?: string
      error?: string
      summary?: string
      code?: string
      artifacts?: ArtifactLink[]
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
  // For plan messages:
  plan?: Plan
}

/** Internal state of the useChat hook (not exported to the UI) */
export interface UseChatState {
  messages: ChatMessage[]
  isThinking: boolean
  planRequest: PlanRequest | null
  error: string | null
  isSending: boolean
  wsReady: boolean
}

/** Metadata stored inside a persisted message's `tool_calls` JSON. */
export interface StoredToolCall {
  name: string
  arguments?: Record<string, unknown> | null
  status?: Exclude<ToolStatus, "pending">
  summary?: string
  risk?: string
  artifacts?: ArtifactLink[]
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
