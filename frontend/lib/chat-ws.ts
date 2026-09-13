import type { UseChatState, WsMessage } from "@/lib/chat-types"
import {
  applyPlanStep,
  finalizePendingTool,
  formatTimestamp,
  upsertPlanMessage,
} from "@/lib/chat-hydrate"
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
        planRequest: null,
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
        planRequest: null,
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

    case "plan_proposed": {
      const plan = msg.plan
      if (!plan) return state
      const requiresApproval = Boolean(msg.requires_approval)
      return {
        ...state,
        isThinking: true,
        error: null,
        planRequest: requiresApproval ? { id: plan.plan_id, plan } : null,
        messages: upsertPlanMessage(state.messages, plan),
      }
    }

    case "plan_step": {
      if (!msg.plan_id || !msg.step) return state
      return {
        ...state,
        messages: applyPlanStep(state.messages, msg.plan_id, msg.step),
      }
    }

    case "plan_done": {
      const plan = msg.plan
      const planId = plan?.plan_id ?? msg.plan_id
      return {
        ...state,
        isThinking: false,
        isSending: false,
        planRequest:
          state.planRequest && state.planRequest.plan.plan_id === planId
            ? null
            : state.planRequest,
        messages: plan ? upsertPlanMessage(state.messages, plan) : state.messages,
      }
    }

    case "error":
      return {
        ...state,
        isThinking: false,
        isSending: false,
        planRequest: null,
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
        planRequest: null,
        messages: finalizePendingTool(state.messages, {
          toolStatus: "cancelled",
          toolSummary: "Cancelled",
        }),
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
