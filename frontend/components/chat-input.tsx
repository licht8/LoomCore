"use client"

import { useState, useRef, useEffect } from "react"
import { Eraser, Paperclip, Pause, SendHorizonal } from "lucide-react"
import { Button } from "@/components/ui/button"
import type { ApprovalRequest } from "@/lib/chat-types"

interface ChatInputProps {
  onSend: (text: string) => void
  onCancel?: () => void
  onClear?: () => void
  isThinking?: boolean
  isSending?: boolean
  approvalRequest?: ApprovalRequest | null
  onApprovalResponse?: (action: "approve" | "deny") => void
}

/**
 * Chat message input component.
 * - Sends on Enter or the "Send" button
 * - The submit button rotates and morphs into a pause control while running;
 *   clicking it cancels the in-flight request
 * - Shows a confirmation dialog (approve/deny) when an approval_request arrives
 * - Disables sending while a request is running
 *
 * The CSS structure is unchanged — only the logic.
 */
export function ChatInput({
  onSend,
  onCancel,
  onClear,
  isThinking = false,
  isSending = false,
  approvalRequest,
  onApprovalResponse,
}: ChatInputProps) {
  const [text, setText] = useState("")
  const inputRef = useRef<HTMLInputElement>(null)
  const isRunning = isThinking || isSending

  // Focus the input on mount and after sending
  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!text.trim() || isRunning) return
    onSend(text.trim())
    setText("")
    inputRef.current?.focus()
  }

  // Handle approval (approve/deny)
  const handleApproval = (action: "approve" | "deny") => {
    if (onApprovalResponse) onApprovalResponse(action)
  }

  // Clear the stored conversation (also resets the assistant's memory)
  const handleClear = () => {
    if (isRunning || !onClear) return
    if (
      typeof window !== "undefined" &&
      !window.confirm(
        "Clear this conversation? This also resets the assistant's memory.",
      )
    ) {
      return
    }
    onClear()
  }

  return (
    <div className="absolute inset-x-0 bottom-0 z-20 px-6 pb-4 pt-10">
      {/* Frosted panel behind the input: messages scroll under this bar and
          are softly blurred, while the field and buttons stay sharp. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-gradient-to-t from-background/60 via-background/30 to-transparent backdrop-blur-xl [mask-image:linear-gradient(to_top,black_45%,transparent)]"
      />

      {/* Confirmation request block — above the input */}
      {approvalRequest && (
        <div className="relative z-10 mx-auto mb-3 max-w-3xl rounded-xl border border-amber-300 bg-amber-50/80 px-4 py-3 text-sm text-amber-900 backdrop-blur-md dark:border-amber-600/50 dark:bg-amber-950/60">
          <p className="mb-2 font-medium">⚠️ Confirmation required</p>
          <p className="mb-3 text-xs opacity-80">{approvalRequest.message}</p>
          <div className="flex gap-2">
            <Button
              size="sm"
              className="bg-emerald-600 text-white hover:bg-emerald-700"
              onClick={() => handleApproval("approve")}
            >
              OK — Confirm
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => handleApproval("deny")}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}

      <div className="relative z-10 mx-auto flex max-w-[61rem] items-stretch gap-2">
        {onClear && <div aria-hidden className="w-24 shrink-0" />}
        <form
          className="flex flex-1 items-center gap-2 rounded-2xl border border-input bg-background/50 px-3 py-2 shadow-sm backdrop-blur-md transition-shadow focus-within:ring-2 focus-within:ring-ring"
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
            placeholder="Ask LoomCore to manage clusters, models, or deployments…"
            className="flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground focus:outline-none"
          />
          {isRunning ? (
            <Button
              type="button"
              size="icon"
              onClick={onCancel}
              title="Pause / stop"
              aria-label="Pause request"
              className="relative size-9 shrink-0 rounded-xl bg-primary text-primary-foreground hover:bg-primary/90"
            >
              <span
                aria-hidden
                className="absolute inset-1.5 animate-spin rounded-full border-2 border-primary-foreground/30 border-t-primary-foreground"
              />
              <Pause className="size-3.5" />
              <span className="sr-only">Pause request</span>
            </Button>
          ) : (
            <Button
              type="submit"
              size="icon"
              disabled={!text.trim()}
              className="size-9 shrink-0 rounded-xl bg-primary hover:bg-primary/90 disabled:opacity-50"
            >
              <SendHorizonal className="size-4.5" />
              <span className="sr-only">Send message</span>
            </Button>
          )}
        </form>
        {onClear && (
          <Button
            type="button"
            variant="ghost"
            onClick={handleClear}
            disabled={isRunning}
            className="h-auto w-24 shrink-0 self-stretch gap-2 rounded-2xl border border-input bg-background/50 text-sm font-medium shadow-sm backdrop-blur-md transition-shadow hover:bg-background/70 focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Eraser className="size-4" />
            Clear
          </Button>
        )}
      </div>

      {/* Static hint below the input. The submit button already rotates while
          a request is running, so no second spinner is shown here. */}
      <p className="relative z-10 mt-2 text-center text-xs text-muted-foreground">
        LoomCore can execute infrastructure changes. Review tool calls before confirming.
      </p>
    </div>
  )
}
