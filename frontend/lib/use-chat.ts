"use client"

import { useState, useEffect, useRef, useCallback } from "react"
import type { ChatMessage, WsMessage, ApprovalRequest, UseChatState } from "@/lib/chat-types"

/** Генерация случайного ID сессии */
function generateSessionId(): string {
  return `sess_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`
}

/** Получить или создать ID сессии (из localStorage) */
function getSessionId(): string {
  if (typeof window === "undefined") return generateSessionId()
  let sid = localStorage.getItem("loomhub_session_id")
  if (!sid) {
    sid = generateSessionId()
    localStorage.setItem("loomhub_session_id", sid)
  }
  return sid
}

/** Форматирование текущей отметки времени */
function formatTimestamp(): string {
  return new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })
}

/**
 * Кастомный хук для управления WebSocket-соединением и состоянием чата.
 * Заменяет статические mock-данные на реальное соединение с FastAPI.
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

  // Синхронизация ref callback
  useEffect(() => {
    onApprovalRef.current = onApprovalResponse
  }, [onApprovalResponse])

  // === Открытие WebSocket-соединения ===
  useEffect(() => {
    const configuredWsUrl = process.env.NEXT_PUBLIC_WS_URL
    const protocol = window.location.protocol === "https:" ? "wss" : "ws"
    const baseWsUrl =
      configuredWsUrl ||
      (window.location.port === "3000"
        ? `${protocol}://${window.location.hostname}:8000`
        : `${protocol}://${window.location.host}`)
    const url = `${baseWsUrl.replace(/\/$/, "")}/ws/${sessionId.current}`
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
        console.error("Ошибка парсинга WS-сообщения:", err)
        setState((prev) => ({
          ...prev,
          error: `Ошибка обработки ответа сервера`,
          isThinking: false,
        }))
      }
    }

    ws.onerror = (error) => {
      // React Strict Mode can close the first development connection during
      // effect cleanup before its handshake completes.
      if (!isActive || wsRef.current !== ws) return
      console.error("WebSocket ошибка:", error)
      setState((prev) => ({
        ...prev,
        error: "Ошибка соединения с сервером. Проверьте, запущен ли бэкенд.",
        isThinking: false,
      }))
    }

    ws.onclose = () => {
      if (!isActive || wsRef.current !== ws) return
      setState((prev) => ({ ...prev, wsReady: false }))
    }

    // Очистка при размонтировании
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

  // === Обработка входящих WS-событий ===
  const handleWsMessage = useCallback((msg: WsMessage) => {
    switch (msg.type) {
      case "thinking": {
        setState((prev) => ({ ...prev, isThinking: true, error: null }))
        break
      }

      case "response": {
        // Добавляем ответ ассистента
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
        // Инструмент вызван, показываем pending
        setState((prev) => ({
          ...prev,
          isThinking: true,
          messages: [
            ...prev.messages,
            {
              id: `msg_${Date.now()}`,
              role: "tool",
              content: msg.content || msg.text || `Вызов инструмента: ${msg.tool}`,
              timestamp: formatTimestamp(),
              tool: msg.tool || "unknown",
              toolStatus: "pending",
              toolSummary: msg.summary || "ожидание ответа…",
              toolCode: msg.code || "",
              artifacts: msg.artifacts,
            },
          ],
          error: null,
        }))
        break
      }

      case "tool_response": {
        // Обновляем последний pending tool-сообщение
        setState((prev) => {
          const updated = [...prev.messages]
          for (let i = updated.length - 1; i >= 0; i--) {
            if (updated[i].role === "tool" && updated[i].toolStatus === "pending") {
              updated[i] = {
                ...updated[i],
                toolStatus: msg.status === "error" ? "error" : "success",
                toolSummary: msg.summary || updated[i].toolSummary || "",
                toolCode: msg.code || updated[i].toolCode || "",
                artifacts: msg.artifacts || updated[i].artifacts,
                content: msg.content || updated[i].content,
              }
              break
            }
          }
          return {
            ...prev,
            isThinking: false,
            isSending: false,
            messages: updated,
            error: msg.status === "error" ? (msg.error || msg.text || "Ошибка выполнения инструмента") : null,
          }
        })
        break
      }

      case "approval_request": {
        // Запрос на подтверждение — показываем UI
        setState((prev) => ({
          ...prev,
          isThinking: true,
          approvalRequest: {
            id: `approval_${Date.now()}`,
            action: msg.action || msg.tool || "unknown_action",
            message: msg.content || msg.text || `Подтвердите выполнение инструмента ${msg.tool || ""}`,
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
          error: msg.error || msg.text || "Неизвестная ошибка сервера",
        }))
        break
      }

      default:
        console.warn("Неизвестный тип WS-сообщения:", msg.type)
    }
  }, [])

  /**
   * Отправка сообщения пользователе через WebSocket.
   * Добавляет пользовательское сообщение в ленту и шлёт JSON на бэкенд.
   */
  const sendMessage = useCallback((text: string) => {
    if (!text.trim() || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return

    // Добавляем сообщение пользователя в ленту
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

    // Шлём на бэкенд
    wsRef.current.send(JSON.stringify({ message: text.trim() }))
  }, [])

  /** Отправка ответа на approval-запрос */
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
      setState((prev) => ({ ...prev, approvalRequest: null, isSending: false }))
      if (onApprovalRef.current) onApprovalRef.current(action)
    },
    [state.approvalRequest],
  )

  return {
    messages: state.messages,
    isThinking: state.isThinking,
    approvalRequest: state.approvalRequest,
    error: state.error,
    isSending: state.isSending,
    wsReady: state.wsReady,
    sendMessage,
    sendApprovalResponse,
  }
}
