"use client"

import { useState, useEffect, useRef, useCallback } from "react"
import type { UseChatState, WsMessage } from "@/lib/chat-types"
import { clearSessionHistory, fetchSessionHistory, getSessionId } from "@/lib/chat-api"
import { finalizePendingTool, formatTimestamp, hydrateMessages } from "@/lib/chat-hydrate"
import { applyWsMessage, buildChatWsUrl } from "@/lib/chat-ws"

/**
 * Custom hook managing the WebSocket connection and chat state.
 * Connection/URL logic lives in `chat-ws`, REST in `chat-api` and the
 * stored-row mapping in `chat-hydrate`.
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
    const url = buildChatWsUrl(sessionId.current)
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
        if (msg.type === "schema_warning") {
          console.warn("Unavailable plugins:", msg.unavailable_plugins)
        }
        setState((prev) => applyWsMessage(prev, msg))
      } catch (err) {
        console.error("Failed to parse WS message:", err)
        setState((prev) => ({
          ...prev,
          error: "Failed to process the server response",
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
      const rows = await fetchSessionHistory(sessionId.current)
      if (!active || !rows) return
      setState((prev) =>
        prev.messages.length ? prev : { ...prev, messages: hydrateMessages(rows) },
      )
    }

    loadHistory()
    return () => {
      active = false
    }
  }, [])

  /**
   * Send a user message over the WebSocket.
   * Appends the user message to the feed and sends JSON to the backend.
   */
  const sendMessage = useCallback((text: string) => {
    if (!text.trim() || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return

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
    await clearSessionHistory(sessionId.current)
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
