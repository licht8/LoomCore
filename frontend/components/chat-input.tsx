"use client"

import { useState, useRef, useEffect } from "react"
import { Eraser, Paperclip, Pause, SendHorizonal, TriangleAlert } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import type { PlanRequest, PlanStep } from "@/lib/chat-types"

interface ChatInputProps {
  onSend: (text: string) => void
  onCancel?: () => void
  onClear?: () => void
  isThinking?: boolean
  isSending?: boolean
  planRequest?: PlanRequest | null
  onPlanResponse?: (action: "approve" | "edit" | "reject", steps?: PlanStep[]) => void
  error?: string | null
}

/**
 * Chat message input component.
 * - Sends on Enter or the "Send" button
 * - The submit button rotates and morphs into a pause control while running;
 *   clicking it cancels the in-flight request
 * - Shows the agent plan for approval (approve/edit/reject) when one arrives
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
  planRequest,
  onPlanResponse,
  error,
}: ChatInputProps) {
  const [text, setText] = useState("")
  const [clearOpen, setClearOpen] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [editText, setEditText] = useState("")
  const [editError, setEditError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const isRunning = isThinking || isSending

  // Focus the input on mount and after sending
  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  // Reset the edit form whenever a new plan arrives
  useEffect(() => {
    setEditOpen(false)
    setEditError(null)
    setEditText(planRequest ? JSON.stringify(planRequest.plan.steps, null, 2) : "")
  }, [planRequest])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!text.trim() || isRunning) return
    onSend(text.trim())
    setText("")
    inputRef.current?.focus()
  }

  const handlePlan = (action: "approve" | "reject") => {
    onPlanResponse?.(action)
  }

  const handleStartEdit = () => {
    if (!planRequest) return
    setEditText(JSON.stringify(planRequest.plan.steps, null, 2))
    setEditError(null)
    setEditOpen(true)
  }

  const handleSubmitEdit = () => {
    try {
      const parsed = JSON.parse(editText)
      if (!Array.isArray(parsed)) {
        setEditError("Steps must be a JSON array.")
        return
      }
      onPlanResponse?.("edit", parsed as PlanStep[])
      setEditOpen(false)
    } catch {
      setEditError("Invalid JSON. Fix the steps and try again.")
    }
  }

  // Open the confirmation dialog (the actual clear happens on confirm)
  const handleClear = () => {
    if (isRunning || !onClear) return
    setClearOpen(true)
  }

  // The user confirmed: delete the stored conversation and close the dialog
  const handleConfirmClear = () => {
    onClear?.()
    setClearOpen(false)
  }

  return (
    <div className="absolute inset-x-0 bottom-0 z-20 px-6 pb-8 pt-10">
      {/* Frosted panel behind the input: messages scroll under this bar and
          are softly blurred, while the field and buttons stay sharp. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-gradient-to-t from-background/60 via-background/30 to-transparent backdrop-blur-xl [mask-image:linear-gradient(to_top,black_45%,transparent)]"
      />

      {/* Plan approval block — above the input */}
      {planRequest && (
        <div className="relative z-10 mx-auto mb-3 max-w-3xl rounded-xl border border-amber-300 bg-amber-50/80 px-4 py-3 text-sm text-amber-900 backdrop-blur-md dark:border-amber-600/50 dark:bg-amber-950/60">
          <p className="mb-1 font-medium">Approval required</p>
          <p className="mb-2 text-xs opacity-80">{planRequest.plan.goal}</p>

          {!editOpen ? (
            <>
              <ol className="mb-3 flex flex-col gap-1 text-xs">
                {planRequest.plan.steps.map((step, index) => (
                  <li key={step.id} className="flex items-start gap-2">
                    <span className="font-mono opacity-80">
                      {index + 1}. {step.tool}
                    </span>
                    <span className="rounded-full bg-amber-900/10 px-1.5 text-[10px] uppercase tracking-wide">
                      {step.risk}
                    </span>
                    {step.why && <span className="opacity-70">{step.why}</span>}
                  </li>
                ))}
              </ol>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  className="bg-emerald-600 text-white hover:bg-emerald-700"
                  onClick={() => handlePlan("approve")}
                >
                  Approve
                </Button>
                <Button size="sm" variant="outline" onClick={handleStartEdit}>
                  Edit
                </Button>
                <Button size="sm" variant="outline" onClick={() => handlePlan("reject")}>
                  Reject
                </Button>
              </div>
            </>
          ) : (
            <>
              <textarea
                value={editText}
                onChange={(e) => setEditText(e.target.value)}
                rows={8}
                spellCheck={false}
                className="mb-2 w-full resize-y rounded-md border border-amber-300 bg-background/70 p-2 font-mono text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              />
              {editError && <p className="mb-2 text-xs text-destructive">{editError}</p>}
              <div className="flex gap-2">
                <Button
                  size="sm"
                  className="bg-emerald-600 text-white hover:bg-emerald-700"
                  onClick={handleSubmitEdit}
                >
                  Apply changes
                </Button>
                <Button size="sm" variant="outline" onClick={() => setEditOpen(false)}>
                  Cancel edit
                </Button>
              </div>
            </>
          )}
        </div>
      )}

      {/* Error banner — in-flow, snug above the input, glass-styled */}
      {error ? (
        <div className="relative z-10 mx-auto mb-2 flex max-w-3xl items-center justify-center gap-2 rounded-xl border border-destructive/40 bg-destructive/10 px-4 py-2 text-center text-xs font-medium text-destructive shadow-sm backdrop-blur-md">
          <TriangleAlert className="size-3.5 shrink-0" />
          <span className="min-w-0 break-words">{error}</span>
        </div>
      ) : null}

      <div className="relative z-10 mx-auto flex max-w-[61rem] items-stretch gap-2">
        {onClear && <div aria-hidden className="w-10 shrink-0" />}
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
            title="Clear conversation"
            className="size-10 shrink-0 self-center rounded-full border border-input bg-background/50 text-muted-foreground shadow-sm backdrop-blur-md transition-all hover:bg-background/70 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Eraser className="size-4.5" />
            <span className="sr-only">Clear conversation</span>
          </Button>
        )}
      </div>

      <AlertDialog open={clearOpen} onOpenChange={setClearOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <div className="flex items-center gap-3">
              <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-destructive/10 text-destructive">
                <Eraser className="size-5" />
              </span>
              <AlertDialogTitle>Clear conversation?</AlertDialogTitle>
            </div>
            <AlertDialogDescription>
              This permanently deletes all messages in this conversation and
              resets the assistant&apos;s memory. This can&apos;t be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleConfirmClear}
              className="gap-2 bg-destructive text-white hover:bg-destructive/90 focus-visible:ring-destructive/30"
            >
              <Eraser className="size-4" />
              Clear conversation
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
