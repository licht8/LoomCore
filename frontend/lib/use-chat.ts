"use client"

import { useState, useEffect, useRef, useCallback } from "react"
import type { PlanStep, UseChatState, WsMessage } from "@/lib/chat-types"
import {
  clearSessionHistory,
  fetchSessionHistory,
  getSessionId,
  newSessionId,
  setSessionId as persistSessionId,
} from "@/lib/chat-api"
import { fetchSessions } from "@/lib/sessions-api"
import { finalizePendingTool, formatTimestamp, hydrateMessages } from "@/lib/chat-hydrate"
import { applyWsMessage, buildChatWsProtocols, buildChatWsUrl } from "@/lib/chat-ws"

/** Initial feed/plan state when (re)loading or switching a session. */
const EMPTY_TURN: Pick<
  UseChatState,
  "messages" | "isThinking" | "isSending" | "planProgress" | "planRequest"
> = {
  messages: [],
  isThinking: false,
  isSending: false,
  planProgress: "",
  planRequest: null,
}

/**
 * Custom hook managing the WebSocket connection and chat state.
 * Connection/URL logic lives in `chat-ws`, REST in `chat-api`/`sessions-api`
 * and the stored-row mapping in `chat-hydrate`.
 */
export function useChat(
  initialSessionId?: string,
  onPlanResponse?: (action: "approve" | "edit" | "reject") => void,
) {
  const [state, setState] = useState<UseChatState>({
    ...EMPTY_TURN,
    error: null,
    wsReady: false,
    sessions: [],
    sessionsLoading: true,
  })

  const [sessionId, setSessionId] = useState<string>(
    () => initialSessionId || getSessionId(),
  )

  const wsRef = useRef<WebSocket | null>(null)
  const onPlanRef = useRef(onPlanResponse)
  const busyRef = useRef(false)

  // Sync the callback ref
  useEffect(() => {
    onPlanRef.current = onPlanResponse
  }, [onPlanResponse])

  // Mirror the busy flag so switch/new/delete can read it without a stale closure.
  useEffect(() => {
    busyRef.current = state.isThinking || state.isSending
  }, [state.isThinking, state.isSending])

  // === Open the WebSocket connection (reconnects on session switch) ===
  useEffect(() => {
    let isActive = true
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let attempt = 0
    let ws: WebSocket | null = null

    const connect = () => {
      if (!isActive) return
      const url = buildChatWsUrl(sessionId)
      console.log("Connecting to WS:", url)

      const socket = new WebSocket(url, buildChatWsProtocols())
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

    // Cleanup on unmount or session switch
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
  }, [sessionId])

  // === Restore stored history on mount or after a session switch ===
  useEffect(() => {
    let active = true
    setState((prev) => ({ ...prev, ...EMPTY_TURN, error: null }))

    async function loadHistory() {
      const rows = await fetchSessionHistory(sessionId)
      if (!active || !rows) return
      setState((prev) =>
        prev.messages.length ? prev : { ...prev, messages: hydrateMessages(rows) },
      )
    }

    loadHistory()
    return () => {
      active = false
    }
  }, [sessionId])

  /** Reload the session list (title/preview per session). */
  const refreshSessions = useCallback(async () => {
    try {
      const list = await fetchSessions()
      setState((prev) => ({ ...prev, sessions: list, sessionsLoading: false }))
    } catch (err) {
      console.error("Failed to load sessions:", err)
      setState((prev) => ({ ...prev, sessionsLoading: false }))
    }
  }, [])

  // Initial session-list load.
  useEffect(() => {
    refreshSessions()
  }, [refreshSessions])

  // Refresh the list whenever a turn finishes.
  const wasBusy = useRef(false)
  useEffect(() => {
    const busy = state.isThinking || state.isSending
    if (wasBusy.current && !busy) refreshSessions()
    wasBusy.current = busy
  }, [state.isThinking, state.isSending, refreshSessions])

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
    await clearSessionHistory(sessionId)
    setState((prev) => ({ ...prev, ...EMPTY_TURN, error: null }))
    refreshSessions()
  }, [sessionId, refreshSessions])

  /** Switch to an existing session (no-op while a turn is in flight). */
  const selectSession = useCallback((id: string) => {
    if (busyRef.current) return
    persistSessionId(id)
    setSessionId(id)
  }, [])

  /** Create and switch to a fresh session. */
  const createSession = useCallback(() => {
    if (busyRef.current) return
    const id = newSessionId()
    setState((prev) => ({ ...prev, ...EMPTY_TURN, error: null }))
    setSessionId(id)
    refreshSessions()
  }, [refreshSessions])

  /** Delete a session's stored history, clearing the feed when it is active. */
  const deleteSession = useCallback(
    async (id: string) => {
      if (busyRef.current) return
      await clearSessionHistory(id)
      if (id === sessionId) {
        setState((prev) => ({ ...prev, ...EMPTY_TURN, error: null }))
      }
      refreshSessions()
    },
    [sessionId, refreshSessions],
  )

  return {
    messages: state.messages,
    isThinking: state.isThinking,
    planProgress: state.planProgress,
    planRequest: state.planRequest,
    error: state.error,
    isSending: state.isSending,
    wsReady: state.wsReady,
    sessions: state.sessions,
    sessionsLoading: state.sessionsLoading,
    sessionId,
    sendMessage,
    sendPlanResponse,
    cancelRequest,
    clearConversation,
    selectSession,
    createSession,
    deleteSession,
    refreshSessions,
  }
}
