"use client"

import { Plus } from "lucide-react"

import { Button } from "@/components/ui/button"
import { SessionList } from "@/components/session-list"
import { useChatContext } from "@/lib/chat-context"

/**
 * Middle zone of the Loom Panel sidebar: a fixed `CHATS` header with a new-chat
 * button, a hairline, and the scrollable conversation rows. The zone itself is
 * `flex-1 min-h-0` so it absorbs the remaining height and keeps the settings
 * zone pinned to the bottom.
 */
export function ChatsSection() {
  const {
    sessions,
    sessionsLoading,
    sessionId,
    selectSession,
    createSession,
    deleteSession,
    isThinking,
    isSending,
  } = useChatContext()

  const busy = isThinking || isSending

  return (
    <div className="flex flex-1 min-h-0 flex-col">
      <div className="flex h-8 shrink-0 items-center justify-between gap-2">
        <span className="px-3 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
          CHATS
        </span>
        <Button
          variant="ghost"
          size="icon-sm"
          title="New chat"
          onClick={createSession}
          disabled={busy}
        >
          <Plus />
          <span className="sr-only">New chat</span>
        </Button>
      </div>

      <div className="mx-3 border-t border-border/50" />

      <div className="flex-1 min-h-0 overflow-y-auto py-3">
        {!sessionsLoading && sessions.length === 0 ? (
          <div className="flex flex-col items-center gap-3 px-2 py-8 text-center">
            <p className="text-xs text-muted-foreground">No chats yet</p>
            <Button
              variant="outline"
              size="sm"
              onClick={createSession}
              disabled={busy}
            >
              Start a chat
            </Button>
          </div>
        ) : (
          <SessionList
            sessions={sessions}
            activeSessionId={sessionId}
            isLoading={sessionsLoading}
            disabled={busy}
            onSelect={selectSession}
            onDelete={deleteSession}
          />
        )}
      </div>
    </div>
  )
}
