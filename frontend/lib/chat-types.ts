// ===== Types for the WebSocket integration =====

/** Downloadable artifact attached to a tool result. */
export interface ArtifactLink {
  label: string
  url: string
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
      status?: "success" | "running" | "error" | "cancelled"
      content?: string
      text?: string
      error?: string
      summary?: string
      code?: string
      artifacts?: ArtifactLink[]
    }
  | {
      type: "approval_request"
      action?: string
      tool?: string
      content?: string
      text?: string
      args?: Record<string, unknown>
    }
  | { type: "schema_warning"; unavailable_plugins?: string[] }
  | { type: "tool_denied" }
  | { type: "cancelled" }
  | { type: "busy" }
  | { type: "error"; error?: string; text?: string; content?: string }

/** Chat message type in the UI feed */
export type ChatMessageRole = "user" | "assistant" | "tool"

export interface ChatMessage {
  id: string
  role: ChatMessageRole
  content: string
  timestamp: string
  // For tool messages:
  tool?: string
  toolStatus?: "pending" | "success" | "error" | "cancelled"
  toolSummary?: string
  toolCode?: string
  artifacts?: ArtifactLink[]
}

/** Internal state of the useChat hook (not exported to the UI) */
export interface UseChatState {
  messages: ChatMessage[]
  isThinking: boolean
  approvalRequest: ApprovalRequest | null
  error: string | null
  isSending: boolean
  wsReady: boolean
}

/** Request for the user to confirm an action */
export interface ApprovalRequest {
  id: string
  action: string // command to confirm
  message: string // request text
  tool?: string
  args?: Record<string, unknown>
}

/** Metadata stored inside a persisted message's `tool_calls` JSON. */
export interface StoredToolCall {
  name: string
  arguments?: Record<string, unknown> | null
  status?: "success" | "error" | "cancelled"
  summary?: string
  risk?: string
  artifacts?: ArtifactLink[]
}

/** A stored message row returned by GET /api/sessions/{id}/messages. */
export interface SessionMessage {
  id: string
  role: "user" | "assistant" | "tool" | "system"
  content: string
  created_at: string
  tool_calls?: StoredToolCall[] | null
  tool_result?: string | null
}
