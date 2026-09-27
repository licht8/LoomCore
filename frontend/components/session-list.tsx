"use client"

import { useState } from "react"
import { MessageSquarePlus, Trash2 } from "lucide-react"

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
  onNew: () => void
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
 * Left-hand conversations rail inside the Chat page. Selection/creation/deletion
 * are blocked while a turn is in flight (`disabled`).
 */
export function SessionList({
  sessions,
  activeSessionId,
  isLoading = false,
  disabled = false,
  onSelect,
  onNew,
  onDelete,
}: SessionListProps) {
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)

  return (
    <aside className="hidden w-64 shrink-0 flex-col border-r border-border/50 md:flex">
      <div className="flex items-center justify-between gap-2 px-3 py-3">
        <span className="text-sm font-medium text-foreground">Conversations</span>
        <Button
          variant="ghost"
          size="icon-sm"
          title="New chat"
          onClick={onNew}
          disabled={disabled}
        >
          <MessageSquarePlus />
          <span className="sr-only">New chat</span>
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto px-2 pb-3">
        {isLoading ? (
          <div className="space-y-2 px-1">
            {Array.from({ length: 4 }).map((_, index) => (
              <div key={index} className="h-12 animate-pulse rounded-lg bg-muted/40" />
            ))}
          </div>
        ) : sessions.length === 0 ? (
          <p className="px-3 py-6 text-center text-xs text-muted-foreground">
            No conversations yet.
          </p>
        ) : (
          sessions.map((session) => {
            const active = session.session_id === activeSessionId
            return (
              <div
                key={session.session_id}
                className={cn(
                  "group relative mb-1 rounded-lg",
                  active && "bg-muted/60",
                )}
              >
                <button
                  type="button"
                  onClick={() => onSelect(session.session_id)}
                  disabled={disabled}
                  className="flex w-full flex-col gap-0.5 rounded-lg px-3 py-2 text-left transition-colors hover:bg-foreground/[0.04] disabled:opacity-60"
                >
                  <span className="truncate text-sm font-medium text-foreground">
                    {session.title || "Untitled"}
                  </span>
                  <span className="truncate text-xs text-muted-foreground">
                    {session.preview}
                  </span>
                  <span className="text-[10px] text-muted-foreground/70">
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
          })
        )}
      </div>

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
    </aside>
  )
}
