"use client"

import { useState } from "react"
import { Trash2 } from "lucide-react"

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
import type { SessionSummary } from "@/lib/chat-types"
import { cn } from "@/lib/utils"

interface SessionListProps {
  sessions: SessionSummary[]
  activeSessionId: string
  isLoading?: boolean
  disabled?: boolean
  onSelect: (id: string) => void
  onDelete: (id: string) => void
}

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ""
  const minutes = Math.floor((Date.now() - then) / 60_000)
  if (minutes < 1) return "just now"
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

/**
 * Rows-only conversation list. The surface, header and scroll container live in
 * `ChatsSection`; this component renders just the rows (and the delete dialog)
 * using the same interaction idiom as `DashboardSidebar`.
 */
export function SessionList({
  sessions,
  activeSessionId,
  isLoading = false,
  disabled = false,
  onSelect,
  onDelete,
}: SessionListProps) {
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)

  if (isLoading) {
    return (
      <div className="space-y-2 px-1">
        {Array.from({ length: 4 }).map((_, index) => (
          <div key={index} className="h-12 animate-pulse rounded-md bg-muted/40" />
        ))}
      </div>
    )
  }

  return (
    <>
      {sessions.map((session) => {
        const active = session.session_id === activeSessionId
        return (
          <div key={session.session_id} className="group relative">
            <button
              type="button"
              onClick={() => onSelect(session.session_id)}
              disabled={disabled}
              aria-current={active ? "true" : undefined}
              className={cn(
                "relative flex h-10 w-full flex-col justify-center gap-0.5 rounded-md px-3 text-left transition-colors duration-150 disabled:pointer-events-none disabled:opacity-50",
                active
                  ? "font-medium text-foreground"
                  : "font-normal hover:bg-foreground/[0.03]",
              )}
            >
              {active && (
                <span
                  aria-hidden
                  className="absolute top-1 bottom-1 left-0 w-0.5 rounded-full bg-foreground/60"
                />
              )}
              <span className="truncate text-sm font-medium text-foreground">
                {session.title || "Untitled"}
              </span>
              <span className="truncate text-xs text-muted-foreground">
                {relativeTime(session.last_message_at)} · {session.message_count} msgs
              </span>
            </button>
            <Button
              variant="ghost"
              size="icon-xs"
              title="Delete conversation"
              disabled={disabled}
              onClick={() => setDeleteTarget(session.session_id)}
              className="absolute top-1 right-1 text-muted-foreground opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
            >
              <Trash2 />
              <span className="sr-only">Delete conversation</span>
            </Button>
          </div>
        )
      })}

      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete conversation?</AlertDialogTitle>
            <AlertDialogDescription>
              This permanently deletes all messages in this conversation and resets
              the assistant&apos;s memory. This can&apos;t be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="gap-2 bg-destructive text-white hover:bg-destructive/90 focus-visible:ring-destructive/30"
              onClick={() => {
                if (deleteTarget) onDelete(deleteTarget)
                setDeleteTarget(null)
              }}
            >
              <Trash2 className="size-4" />
              Delete conversation
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
