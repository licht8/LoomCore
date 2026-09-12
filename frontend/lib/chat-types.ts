// ===== Types for the WebSocket integration =====

/** Event arriving from the backend over WebSocket */
export interface WsMessage {
  type: "thinking" | "response" | "tool_call" | "tool_response" | "approval_request" | "error"
  content?: string
  text?: string
  result?: string
  tool?: string
  args?: Record<string, unknown>
  risk?: string
  status?: "success" | "running" | "error"
  summary?: string
  code?: string
  needs_approval?: boolean
  action?: string
  error?: string
  artifacts?: Array<{ label: string; url: string }>
}

/** Chat message type in the UI feed */
export type ChatMessageRole = "user" | "assistant" | "tool"

export interface ChatMessage {
  id: string
  role: ChatMessageRole
  content: string
  timestamp: string
  // For tool messages:
  tool?: string
  toolStatus?: "pending" | "success" | "error"
  toolSummary?: string
  toolCode?: string
  artifacts?: Array<{ label: string; url: string }>
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
