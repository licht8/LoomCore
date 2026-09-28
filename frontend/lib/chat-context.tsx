"use client"

import { createContext, useContext, useEffect, useRef, type ReactNode } from "react"

import { useChat } from "@/lib/use-chat"

type ChatContextValue = ReturnType<typeof useChat>

const ChatContext = createContext<ChatContextValue | null>(null)

/**
 * Dashboard-level chat state. Mounts a single `useChat` instance so the
 * conversations list (`ChatsSection`) and the chat feed (`ChatRoom`) share one
 * WebSocket and one session selection across every dashboard route.
 */
export function ChatProvider({ children }: { children: ReactNode }) {
  const chat = useChat()
  const { sessionId, selectSession } = chat
  const adopted = useRef(false)

  // Adopt the `?session=` deep link once on mount. Kept in an effect so SSR
  // renders deterministically; `selectSession` is a no-op while a turn is in
  // flight, which cannot happen at mount.
  useEffect(() => {
    if (adopted.current) return
    adopted.current = true
    const id = new URLSearchParams(window.location.search).get("session")
    if (id && id !== sessionId) selectSession(id)
  }, [sessionId, selectSession])

  return <ChatContext.Provider value={chat}>{children}</ChatContext.Provider>
}

/** Access the shared chat state; throws if rendered outside `ChatProvider`. */
export function useChatContext(): ChatContextValue {
  const context = useContext(ChatContext)
  if (!context) {
    throw new Error("useChatContext must be used within a ChatProvider")
  }
  return context
}
