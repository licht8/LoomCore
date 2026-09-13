"use client"

import { useEffect, useRef } from "react"
import { useChat } from "@/lib/use-chat"
import { ChatMessageItem } from "@/components/chat-message"
import { ChatInput } from "@/components/chat-input"

/**
 * Client chat component: combines useChat with the UI components.
 * Called from the page.tsx Server Component.
 */
export function ChatRoom() {
  const {
    messages,
    isThinking,
    approvalRequest,
    error,
    isSending,
    sendMessage,
    sendApprovalResponse,
    cancelRequest,
    clearConversation,
  } = useChat()

  // --- Auto-scroll to the newest message ---
  const scrollRef = useRef<HTMLDivElement>(null)
  const endRef = useRef<HTMLDivElement>(null)
  // Follow new messages unless the user deliberately scrolls up to read
  // history. Intent is detected from the scroll direction, so programmatic
  // (smooth) scrolling and content growth never flip this off by accident.
  const stickToBottom = useRef(true)
  const lastScrollTop = useRef(0)
  const hasHydrated = useRef(false)

  const handleScroll = () => {
    const el = scrollRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24
    if (atBottom) {
      stickToBottom.current = true
    } else if (el.scrollTop < lastScrollTop.current - 2) {
      stickToBottom.current = false
    }
    lastScrollTop.current = el.scrollTop
  }

  useEffect(() => {
    if (!stickToBottom.current) return
    const behavior: ScrollBehavior = hasHydrated.current ? "smooth" : "auto"
    endRef.current?.scrollIntoView({ behavior, block: "end" })
    hasHydrated.current = true
  }, [messages, isThinking, approvalRequest, error])

  return (
    <>
      {/* Message container — overflow-y-auto for scrolling */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto px-6 pt-6 pb-32"
      >
        <div className="mx-auto flex max-w-[61rem] flex-col gap-6 px-[6.5rem]">
          {messages.map((message) => (
            <ChatMessageItem key={message.id} message={message} />
          ))}
          <div ref={endRef} />
        </div>
      </div>

      {/* Error block — red banner above the input */}
      {error && (
        <div className="absolute inset-x-0 bottom-[128px] mx-auto max-w-3xl px-6">
          <div className="rounded-xl border border-destructive/50 bg-destructive/10 px-4 py-2 text-center text-xs font-medium text-destructive">
            ⚠️ {error}
          </div>
        </div>
      )}

      <ChatInput
        onSend={sendMessage}
        onCancel={cancelRequest}
        onClear={clearConversation}
        isThinking={isThinking}
        isSending={isSending}
        approvalRequest={approvalRequest}
        onApprovalResponse={sendApprovalResponse}
      />
    </>
  )
}
