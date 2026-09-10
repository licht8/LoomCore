// ===== Типы для WebSocket-интеграции =====

/** Событие, приходящее от бэкенда через WebSocket */
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

/** Тип сообщения в UI-ленте чата */
export type ChatMessageRole = "user" | "assistant" | "tool"

export interface ChatMessage {
  id: string
  role: ChatMessageRole
  content: string
  timestamp: string
  // Для tool-сообщений:
  tool?: string
  toolStatus?: "pending" | "success" | "error"
  toolSummary?: string
  toolCode?: string
  artifacts?: Array<{ label: string; url: string }>
}

/** Внутреннее состояние хука useChat (не экспортируется в UI) */
export interface UseChatState {
  messages: ChatMessage[]
  isThinking: boolean
  approvalRequest: ApprovalRequest | null
  error: string | null
  isSending: boolean
  wsReady: boolean
}

/** Запрос на подтверждение действия пользователем */
export interface ApprovalRequest {
  id: string
  action: string // команда, которую нужно подтвердить
  message: string // текст запроса
  tool?: string
  args?: Record<string, unknown>
}
