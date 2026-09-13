"use client"

import { useState, useEffect, useRef, useCallback } from "react"
import type {
  ChatMessage,
  WsMessage,
  ApprovalRequest,
  SessionMessage,
  UseChatState,
} from "@/lib/chat-types"
import { withAdminToken } from "@/lib/admin-token"

/** Generate a random session ID */
function generateSessionId(): string {
  return `sess_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`
}

/** Get or create the session ID (from localStorage) */
function getSessionId(): string {
  if (typeof window === "undefined") return generateSessionId()
  let sid = localStorage.getItem("loomcore_session_id")
  if (!sid) {
    sid = generateSessionId()
    localStorage.setItem("loomcore_session_id", sid)
  }
  return sid
}

/** Format the current timestamp */
function formatTimestamp(): string {
  return new Date().toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })
}

/**
 * Resolve the newest still-pending tool message with the given patch.
 * Undefined patch values are ignored so existing fields are preserved.
 */
function finalizePendingTool(
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

/** Base URL for REST calls (empty string means same-origin via nginx). */
function getApiBase(): string {
  if (typeof window === "undefined") return ""
  const configured = process.env.NEXT_PUBLIC_API_URL
  if (configured) return configured.replace(/\/$/, "")
  const protocol = window.location.protocol === "https:" ? "https" : "http"
  // Next dev runs on :3000 while the backend listens on :8000.
  return window.location.port === "3000"
    ? `${protocol}://${window.location.hostname}:8000`
    : ""
}

/** Format a stored UTC timestamp as a local HH:MM label. */
function formatTimestampFromIso(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ""
  return date.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })
}

/** Build a tool card from a stored tool call. */
function toolCardFromRow(row: SessionMessage, timestamp: string): ChatMessage {
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
 */
function hydrateMessages(rows: SessionMessage[]): ChatMessage[] {
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

    flushDecision()

    if (row.role === "user") {
      hydrated.push({ id: row.id, role: "user", content: row.content, timestamp })
    } else if (row.role === "assistant" && (row.content || "").trim()) {
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

/**
 * Custom hook managing the WebSocket connection and chat state.
 * Replaces static mock data with a real connection to FastAPI.
 */
export function useChat(onApprovalResponse?: (action: "approve" | "deny") => void) {
  const [state, setState] = useState<UseChatState>({
    messages: [],
    isThinking: false,
    approvalRequest: null,
    error: null,
    isSending: false,
    wsReady: false,
  })

  const wsRef = useRef<WebSocket | null>(null)
  const sessionId = useRef<string>(getSessionId())
  const onApprovalRef = useRef(onApprovalResponse)

  // Sync the callback ref
  useEffect(() => {
    onApprovalRef.current = onApprovalResponse
  }, [onApprovalResponse])

  // === Open the WebSocket connection ===
  useEffect(() => {
    const configuredWsUrl = process.env.NEXT_PUBLIC_WS_URL
    const protocol = window.location.protocol === "https:" ? "wss" : "ws"
    const baseWsUrl =
      configuredWsUrl ||
      (window.location.port === "3000"
        ? `${protocol}://${window.location.hostname}:8000`
        : `${protocol}://${window.location.host}`)
    const url = withAdminToken(`${baseWsUrl.replace(/\/$/, "")}/ws/${sessionId.current}`)
    console.log("Connecting to WS:", url)

    const ws = new WebSocket(url)
    let isActive = true
    wsRef.current = ws

    ws.onopen = () => {
      if (!isActive || wsRef.current !== ws) {
        ws.close()
        return
      }
      setState((prev) => ({ ...prev, wsReady: true, error: null }))
    }

    ws.onmessage = (event: MessageEvent) => {
      try {
        const msg: WsMessage = JSON.parse(event.data)
        handleWsMessage(msg)
      } catch (err) {
        console.error("Failed to parse WS message:", err)
        setState((prev) => ({
          ...prev,
          error: `Failed to process the server response`,
          isThinking: false,
        }))
      }
    }

    ws.onerror = (error) => {
      // React Strict Mode can close the first development connection during
      // effect cleanup before its handshake completes.
      if (!isActive || wsRef.current !== ws) return
      console.error("WebSocket error:", error)
      setState((prev) => ({
        ...prev,
        error: "Could not connect to the server. Check that the backend is running.",
        isThinking: false,
        isSending: false,
        approvalRequest: null,
        messages: finalizePendingTool(prev.messages, {
          toolStatus: "error",
          toolSummary: "Disconnected",
        }),
      }))
    }

    ws.onclose = () => {
      if (!isActive || wsRef.current !== ws) return
      setState((prev) => ({
        ...prev,
        wsReady: false,
        isThinking: false,
        isSending: false,
        approvalRequest: null,
        messages: finalizePendingTool(prev.messages, {
          toolStatus: "error",
          toolSummary: "Disconnected",
        }),
      }))
    }

    // Cleanup on unmount
    return () => {
      isActive = false
      if (wsRef.current === ws) {
        wsRef.current = null
      }
      if (ws.readyState === WebSocket.OPEN) {
        ws.close()
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // === Restore stored history on mount (survives a page reload) ===
  useEffect(() => {
    let active = true

    async function loadHistory() {
      try {
        const url = withAdminToken(
          `${getApiBase()}/api/sessions/${encodeURIComponent(sessionId.current)}/messages?limit=200`,
        )
        const response = await fetch(url, { credentials: "include" })
        if (!response.ok) return
        const data = await response.json()
        if (!active || !Array.isArray(data.messages)) return
        setState((prev) =>
          prev.messages.length
            ? prev
            : { ...prev, messages: hydrateMessages(data.messages as SessionMessage[]) },
        )
      } catch (err) {
        console.error("Failed to load chat history:", err)
      }
    }

    loadHistory()
    return () => {
      active = false
    }
  }, [])

  // === Handle incoming WS events ===
  const handleWsMessage = useCallback((msg: WsMessage) => {
    switch (msg.type) {
      case "thinking": {
        setState((prev) => ({ ...prev, isThinking: true, error: null }))
        break
      }

      case "response": {
        // Append the assistant response
        setState((prev) => ({
          ...prev,
          isThinking: false,
          isSending: false,
          messages: [
            ...prev.messages,
            {
              id: `msg_${Date.now()}`,
              role: "assistant",
              content: msg.content || msg.text || "",
              timestamp: formatTimestamp(),
            },
          ],
          error: null,
        }))
        break
      }

      case "tool_call": {
        // A tool was called; show it as pending
        setState((prev) => ({
          ...prev,
          isThinking: true,
          messages: [
            ...prev.messages,
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
          error: null,
        }))
        break
      }

      case "tool_response": {
        // Update the last pending tool message with the terminal state
        setState((prev) => ({
          ...prev,
          isThinking: false,
          isSending: false,
          approvalRequest: null,
          messages: finalizePendingTool(prev.messages, {
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
        }))
        break
      }

      case "approval_request": {
        // Confirmation request — show the UI
        setState((prev) => ({
          ...prev,
          isThinking: true,
          approvalRequest: {
            id: `approval_${Date.now()}`,
            action: msg.action || msg.tool || "unknown_action",
            message: msg.content || msg.text || `Confirm running tool ${msg.tool || ""}`,
            tool: msg.tool,
            args: msg.args,
          },
          error: null,
        }))
        break
      }

      case "error": {
        setState((prev) => ({
          ...prev,
          isThinking: false,
          isSending: false,
          approvalRequest: null,
          messages: finalizePendingTool(prev.messages, {
            toolStatus: "error",
            toolSummary: "Error",
          }),
          error: msg.error || msg.text || "Unknown server error",
        }))
        break
      }

      case "cancelled": {
        setState((prev) => ({
          ...prev,
          isThinking: false,
          isSending: false,
          approvalRequest: null,
          messages: finalizePendingTool(prev.messages, {
            toolStatus: "cancelled",
            toolSummary: "Cancelled",
          }),
        }))
        break
      }

      case "tool_denied": {
        setState((prev) => ({
          ...prev,
          isThinking: false,
          isSending: false,
          approvalRequest: null,
          messages: finalizePendingTool(prev.messages, {
            toolStatus: "cancelled",
            toolSummary: "Denied",
          }),
          error: null,
        }))
        break
      }

      case "busy": {
        setState((prev) => ({
          ...prev,
          error: "A request is already running. Pause it before sending another.",
        }))
        break
      }

      case "schema_warning": {
        // Some plugins were unavailable while building the tool schema.
        console.warn("Unavailable plugins:", msg.unavailable_plugins)
        break
      }

      default:
        console.warn("Unknown WS message type:", msg.type)
    }
  }, [])

  /**
   * Send a user message over the WebSocket.
   * Appends the user message to the feed and sends JSON to the backend.
   */
  const sendMessage = useCallback((text: string) => {
    if (!text.trim() || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return

    // Append the user message to the feed
    setState((prev) => ({
      ...prev,
      isSending: true,
      messages: [
        ...prev.messages,
        {
          id: `msg_${Date.now()}`,
          role: "user",
          content: text.trim(),
          timestamp: formatTimestamp(),
        },
      ],
    }))

    // Send it to the backend
    wsRef.current.send(JSON.stringify({ message: text.trim() }))
  }, [])

  /** Send a response to an approval request */
  const sendApprovalResponse = useCallback(
    (action: "approve" | "deny") => {
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return

      const approval = state.approvalRequest
      wsRef.current.send(
        JSON.stringify({
          action,
          tool: approval?.tool,
          args: approval?.args,
        }),
      )
      if (action === "approve") {
        // Keep the "running" state until the tool_response arrives.
        setState((prev) => ({
          ...prev,
          approvalRequest: null,
          isThinking: true,
          isSending: true,
        }))
      } else {
        setState((prev) => ({
          ...prev,
          approvalRequest: null,
          isThinking: false,
          isSending: false,
          messages: finalizePendingTool(prev.messages, {
            toolStatus: "cancelled",
            toolSummary: "Denied",
          }),
        }))
      }
      if (onApprovalRef.current) onApprovalRef.current(action)
    },
    [state.approvalRequest],
  )

  /** Cancel the request currently in flight (LLM and/or tool). */
  const cancelRequest = useCallback(() => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return
    wsRef.current.send(JSON.stringify({ action: "cancel" }))
    setState((prev) => ({
      ...prev,
      isThinking: false,
      isSending: false,
      approvalRequest: null,
      messages: finalizePendingTool(prev.messages, {
        toolStatus: "cancelled",
        toolSummary: "Cancelled",
      }),
    }))
  }, [])

  /** Delete this session's stored history and clear the feed. */
  const clearConversation = useCallback(async () => {
    try {
      const url = withAdminToken(
        `${getApiBase()}/api/sessions/${encodeURIComponent(sessionId.current)}/messages`,
      )
      await fetch(url, { method: "DELETE", credentials: "include" })
    } catch (err) {
      console.error("Failed to clear conversation:", err)
    }
    setState((prev) => ({
      ...prev,
      messages: [],
      isThinking: false,
      isSending: false,
      approvalRequest: null,
      error: null,
    }))
  }, [])

  return {
    messages: state.messages,
    isThinking: state.isThinking,
    approvalRequest: state.approvalRequest,
    error: state.error,
    isSending: state.isSending,
    wsReady: state.wsReady,
    sendMessage,
    sendApprovalResponse,
    cancelRequest,
    clearConversation,
  }
}
