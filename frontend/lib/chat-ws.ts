import type { UseChatState, WsMessage } from "@/lib/chat-types"
import { finalizePendingTool, formatTimestamp } from "@/lib/chat-hydrate"
import { withAdminToken } from "@/lib/admin-token"

/** Build the admin-authenticated WebSocket URL for a session. */
export function buildChatWsUrl(sessionId: string): string {
  const configured = process.env.NEXT_PUBLIC_WS_URL
  const protocol = window.location.protocol === "https:" ? "wss" : "ws"
  const base =
    configured ||
    (window.location.port === "3000"
      ? `${protocol}://${window.location.hostname}:8000`
      : `${protocol}://${window.location.host}`)
  return withAdminToken(`${base.replace(/\/$/, "")}/ws/${sessionId}`)
}

/** Apply one backend event to the chat state. Pure — no side effects. */
export function applyWsMessage(state: UseChatState, msg: WsMessage): UseChatState {
  switch (msg.type) {
    case "thinking":
      return { ...state, isThinking: true, error: null }

    case "response":
      return {
        ...state,
        isThinking: false,
        isSending: false,
        error: null,
        messages: [
          ...state.messages,
          {
            id: `msg_${Date.now()}`,
            role: "assistant",
            content: msg.content || msg.text || "",
            timestamp: formatTimestamp(),
          },
        ],
      }

    case "tool_call":
      return {
        ...state,
        isThinking: true,
        error: null,
        messages: [
          ...state.messages,
          {
            id: `msg_${Date.now()}`,
            role: "tool",
            content: msg.content || msg.text || `Calling tool: ${msg.tool}`,
            timestamp: formatTimestamp(),
            tool: msg.tool || "unknown",
            toolStatus: "pending",
            toolSummary: msg.summary || "awaiting response…",
            toolCode: msg.code || "",
            artifacts: msg.artifacts,
          },
        ],
      }

    case "tool_response":
      return {
        ...state,
        isThinking: false,
        isSending: false,
        approvalRequest: null,
        messages: finalizePendingTool(state.messages, {
          toolStatus: msg.status === "error" ? "error" : "success",
          toolSummary: msg.summary || (msg.status === "error" ? "Error" : "Done"),
          toolCode: msg.code,
          artifacts: msg.artifacts,
          content: msg.content,
        }),
        error:
          msg.status === "error"
            ? msg.error || msg.text || "Tool execution failed"
            : null,
      }

    case "approval_request":
      return {
        ...state,
        isThinking: true,
        error: null,
        approvalRequest: {
          id: `approval_${Date.now()}`,
          action: msg.action || msg.tool || "unknown_action",
          message: msg.content || msg.text || `Confirm running tool ${msg.tool || ""}`,
          tool: msg.tool,
          args: msg.args,
        },
      }

    case "error":
      return {
        ...state,
        isThinking: false,
        isSending: false,
        approvalRequest: null,
        messages: finalizePendingTool(state.messages, {
          toolStatus: "error",
          toolSummary: "Error",
        }),
        error: msg.error || msg.text || "Unknown server error",
      }

    case "cancelled":
      return {
        ...state,
        isThinking: false,
        isSending: false,
        approvalRequest: null,
        messages: finalizePendingTool(state.messages, {
          toolStatus: "cancelled",
          toolSummary: "Cancelled",
        }),
      }

    case "tool_denied":
      return {
        ...state,
        isThinking: false,
        isSending: false,
        approvalRequest: null,
        messages: finalizePendingTool(state.messages, {
          toolStatus: "cancelled",
          toolSummary: "Denied",
        }),
        error: null,
      }

    case "busy":
      return {
        ...state,
        error: "A request is already running. Pause it before sending another.",
      }

    case "schema_warning":
      return state

    default:
      console.warn("Unknown WS message type:", (msg as { type: string }).type)
      return state
  }
}
