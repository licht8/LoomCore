// ===== Типы сообщений чата =====
// ЕДИНЫЙ ИСТОЧНИК ИСТИНЫ — импортировать из @/lib/chat-types
// Этот файл экспортирует всё из chat-types.ts для обратной совместимости

export type {
  ChatMessage,
  ChatMessageRole,
  UseChatState,
  ApprovalRequest,
  WsMessage,
} from "@/lib/chat-types"

export type NavItem = {
  label: string
  icon: "assistant" | "status"
  active?: boolean
}

export const navItems: NavItem[] = [
  { label: "AI Assistant", icon: "assistant", active: true },
  { label: "System Status", icon: "status" },
]
