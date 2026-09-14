"use client"

import { useState, useEffect, useRef, useCallback } from "react"
import type { PlanStep, UseChatState, WsMessage } from "@/lib/chat-types"
import { clearSessionHistory, fetchSessionHistory, getSessionId } from "@/lib/chat-api"
import { finalizePendingTool, formatTimestamp, hydrateMessages } from "@/lib/chat-hydrate"
import { applyWsMessage, buildChatWsUrl } from "@/lib/chat-ws"

/**
 * Custom hook managing the WebSocket connection and chat state.
 * Connection/URL logic lives in `chat-ws`, REST in `chat-api` and the
 * stored-row mapping in `chat-hydrate`.
 */
export function useChat(onPlanResponse?: (action: "approve" | "edit" | "reject") => void) {
  const [state, setState] = useState<UseChatState>({
    messages: [],
    isThinking: false,
    planRequest: null,
    error: null,
    isSending: false,
    wsReady: false,
  })

  const wsRef = useRef<WebSocket | null>(null)
  const sessionId = useRef<string>(getSessionId())
  const onPlanRef = useRef(onPlanResponse)

  // Sync the callback ref
  useEffect(() => {
    onPlanRef.current = onPlanResponse
  }, [onPlanResponse])

  // === Open the WebSocket connection ===
  useEffect(() => {
    let isActive = true
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let attempt = 0
    let ws: WebSocket | null = null

    const connect = () => {
      if (!isActive) return
      const url = buildChatWsUrl(sessionId.current)
      console.log("Connecting to WS:", url)

      const socket = new WebSocket(url)
      ws = socket
      wsRef.current = socket

      socket.onopen = () => {
        if (!isActive || wsRef.current !== socket) {
          socket.close()
          return
        }
        attempt = 0
        setState((prev) => ({ ...prev, wsReady: true, error: null }))
      }

      socket.onmessage = (event: MessageEvent) => {
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

      socket.onerror = (error) => {
        // React Strict Mode can close the first development connection during
        // effect cleanup before its handshake completes.
        if (!isActive || wsRef.current !== socket) return
        console.error("WebSocket error:", error)
        setState((prev) => ({
          ...prev,
          error: "Could not connect to the server. Check that the backend is running.",
          isThinking: false,
          isSending: false,
          planRequest: null,
          messages: finalizePendingTool(prev.messages, {
            toolStatus: "error",
            toolSummary: "Disconnected",
          }),
        }))
      }

      socket.onclose = () => {
        if (!isActive || wsRef.current !== socket) return
        wsRef.current = null
        // Clear any busy state so the input is usable, then reconnect with
        // capped backoff rather than leaving the client jammed until reload.
        setState((prev) => ({
          ...prev,
          wsReady: false,
          isThinking: false,
          isSending: false,
          planRequest: null,
          messages: finalizePendingTool(prev.messages, {
            toolStatus: "error",
            toolSummary: "Disconnected",
          }),
        }))
        attempt += 1
        const delay = Math.min(1000 * 2 ** (attempt - 1), 15000)
        console.warn(`WebSocket closed; reconnecting in ${delay}ms`)
        reconnectTimer = setTimeout(connect, delay)
      }
    }

    connect()

    // Cleanup on unmount
    return () => {
      isActive = false
      if (reconnectTimer) clearTimeout(reconnectTimer)
      if (wsRef.current === ws) {
        wsRef.current = null
      }
      if (ws && ws.readyState === WebSocket.OPEN) {
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
      planRequest: null,
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

  /** Approve, edit or reject the plan awaiting a decision. */
  const sendPlanResponse = useCallback(
    (action: "approve" | "edit" | "reject", steps?: PlanStep[]) => {
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return

      const request = state.planRequest
      if (!request) return
      const { plan } = request

      if (action === "approve") {
        wsRef.current.send(
          JSON.stringify({
            action: "plan_approve",
            plan_id: plan.plan_id,
            revision: plan.revision,
          }),
        )
        setState((prev) => ({
          ...prev,
          planRequest: null,
          isThinking: true,
          isSending: true,
          error: null,
        }))
      } else if (action === "edit") {
        wsRef.current.send(
          JSON.stringify({
            action: "plan_edit",
            plan_id: plan.plan_id,
            revision: plan.revision,
            steps: steps ?? plan.steps,
          }),
        )
        // A fresh plan_proposed event will arrive with the revised plan.
        setState((prev) => ({
          ...prev,
          planRequest: null,
          isThinking: true,
          isSending: true,
          error: null,
        }))
      } else {
        wsRef.current.send(
          JSON.stringify({ action: "plan_reject", plan_id: plan.plan_id }),
        )
        setState((prev) => ({
          ...prev,
          planRequest: null,
          isThinking: false,
          isSending: false,
        }))
      }

      if (onPlanRef.current) onPlanRef.current(action)
    },
    [state.planRequest],
  )

  /** Cancel the request currently in flight (LLM and/or plan). */
  const cancelRequest = useCallback(() => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return
    wsRef.current.send(JSON.stringify({ action: "cancel" }))
    setState((prev) => ({
      ...prev,
      isThinking: false,
      isSending: false,
      planRequest: null,
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
      planRequest: null,
      error: null,
    }))
  }, [])

  return {
    messages: state.messages,
    isThinking: state.isThinking,
    planRequest: state.planRequest,
    error: state.error,
    isSending: state.isSending,
    wsReady: state.wsReady,
    sendMessage,
    sendPlanResponse,
    cancelRequest,
    clearConversation,
  }
}
