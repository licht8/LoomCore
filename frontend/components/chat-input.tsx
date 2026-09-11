"use client"

import { useState, useRef, useEffect } from "react"
import { Paperclip, SendHorizonal } from "lucide-react"
import { Button } from "@/components/ui/button"
import type { ApprovalRequest } from "@/lib/chat-types"

interface ChatInputProps {
  onSend: (text: string) => void
  isThinking?: boolean
  isSending?: boolean
  approvalRequest?: ApprovalRequest | null
  onApprovalResponse?: (action: "approve" | "deny") => void
}

/**
 * Компонент ввода сообщения в чат.
 * - Отправка по Enter или кнопке "Send"
 * - Индикатор "печатает…" когда бэкенд обрабатывает запрос
 * - Диалог подтверждения (approve/deny) когда приходит approval_request
 * - Дизейбл кнопки отправки пока идёт обработка
 *
 * CSS-структура НЕ изменена — только логика.
 */
export function ChatInput({
  onSend,
  isThinking = false,
  isSending = false,
  approvalRequest,
  onApprovalResponse,
}: ChatInputProps) {
  const [text, setText] = useState("")
  const inputRef = useRef<HTMLInputElement>(null)

  // Фокус на инпут при монтировании и после отправки
  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!text.trim() || isSending) return
    onSend(text.trim())
    setText("")
    inputRef.current?.focus()
  }

  // Обработка approval (approve/deny)
  const handleApproval = (action: "approve" | "deny") => {
    if (onApprovalResponse) onApprovalResponse(action)
  }

  return (
    <div className="relative px-6 pb-4 pt-10">
      {/* Градиентный фон — как было */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-gradient-to-t from-background/60 via-background/30 to-transparent backdrop-blur-2xl [mask-image:linear-gradient(to_top,black_55%,transparent)]"
      />

      {/* Блок запроса на подтверждение — поверх инпута */}
      {approvalRequest && (
        <div className="mx-auto mb-3 max-w-3xl rounded-xl border border-amber-300 bg-amber-50/80 px-4 py-3 text-sm text-amber-900 backdrop-blur-md dark:border-amber-600/50 dark:bg-amber-950/60">
          <p className="mb-2 font-medium">⚠️ Требуется подтверждение</p>
          <p className="mb-3 text-xs opacity-80">{approvalRequest.message}</p>
          <div className="flex gap-2">
            <Button
              size="sm"
              className="bg-emerald-600 text-white hover:bg-emerald-700"
              onClick={() => handleApproval("approve")}
            >
              OK — Подтвердить
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => handleApproval("deny")}
            >
              Отмена
            </Button>
          </div>
        </div>
      )}

      <form
        className="mx-auto flex max-w-3xl items-center gap-2 rounded-2xl border border-input bg-background/50 px-3 py-2 shadow-sm backdrop-blur-md transition-shadow focus-within:ring-2 focus-within:ring-ring"
        onSubmit={handleSubmit}
      >
        <Button type="button" variant="ghost" size="icon" className="shrink-0 text-muted-foreground">
          <Paperclip className="size-4.5" />
          <span className="sr-only">Attach file</span>
        </Button>
        <input
          ref={inputRef}
          type="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Ask LoomHub to manage clusters, models, or deployments…"
          className="flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground focus:outline-none"
        />
        <Button
          type="submit"
          size="icon"
          disabled={isSending || !text.trim()}
          className="size-9 shrink-0 rounded-xl bg-primary hover:bg-primary/90 disabled:opacity-50"
        >
          <SendHorizonal className="size-4.5" />
          <span className="sr-only">Send message</span>
        </Button>
      </form>

      {/* Индикатор "печатает…" над полем ввода */}
      <p className="mt-2 text-center text-xs text-muted-foreground">
        {isThinking ? (
          <span className="inline-flex items-center gap-1.5">
            <svg
              className="size-3 animate-spin text-muted-foreground"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
            >
              <circle
                className="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                strokeWidth="4"
              />
              <path
                className="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
              />
            </svg>
            LoomHub печатает…
          </span>
        ) : (
          "LoomHub can execute infrastructure changes. Review tool calls before confirming."
        )}
      </p>
    </div>
  )
}
