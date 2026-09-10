"use client"

import { CheckCircle2, Loader2, XCircle, Sparkles, Terminal } from "lucide-react"
import { cn } from "@/lib/utils"
import type { ChatMessage } from "@/lib/chat-data"

function renderBold(text: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? (
      <strong key={i} className="font-semibold">
        {part.slice(2, -2)}
      </strong>
    ) : (
      part
    ),
  )
}

/**
 * Компонент отображения одного сообщения в чате.
 * Поддерживает три роли: user, assistant, tool.
 * Для tool-сообщений показывает статус выполнения (pending/success/error).
 */
export function ChatMessageItem({ message }: { message: ChatMessage }) {
  // --- Пользовательское сообщение ---
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[75%]">
          <div className="rounded-2xl rounded-br-md bg-[oklch(0.72_0.07_305)] px-4 py-3 text-sm leading-relaxed text-[oklch(0.18_0.02_295)] shadow-sm">
            {message.content}
          </div>
          <p className="mt-1 pr-1 text-right text-xs text-muted-foreground">{message.timestamp}</p>
        </div>
      </div>
    )
  }

  // --- Сообщение от ассистента (LLM) ---
  if (message.role === "assistant") {
    return (
      <div className="flex justify-start gap-3">
        <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
          <Sparkles className="size-4" />
        </div>
        <div className="max-w-[75%]">
          <div className="rounded-2xl rounded-tl-md border border-border bg-card px-4 py-3 text-sm leading-relaxed text-card-foreground shadow-sm backdrop-blur-md">
            {renderBold(message.content)}
          </div>
          <p className="mt-1 pl-1 text-xs text-muted-foreground">{message.timestamp}</p>
        </div>
      </div>
    )
  }

  // --- Tool-сообщение ---
  return (
    <div className="flex justify-start gap-3">
      <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full bg-accent text-accent-foreground">
        <Terminal className="size-4" />
      </div>
      <div className="w-full max-w-[75%]">
        <div className="overflow-hidden rounded-2xl rounded-tl-md border border-primary/25 bg-card shadow-sm backdrop-blur-md">
          {/* Шапка с названием инструмента и статусом */}
          <div className="flex items-center justify-between gap-2 border-b border-border bg-secondary/60 px-4 py-2.5">
            <div className="flex items-center gap-2">
              <span className="font-mono text-xs font-medium text-secondary-foreground">
                {message.tool || "tool"}
              </span>
            </div>
            <span
              className={cn(
                "flex items-center gap-1.5 text-xs font-medium",
                message.toolStatus === "success"
                  ? "text-emerald-600"
                  : message.toolStatus === "error"
                  ? "text-destructive"
                  : "text-primary",
              )}
            >
              {message.toolStatus === "success" ? (
                <>
                  <CheckCircle2 className="size-3.5" />
                  {message.toolSummary || "Выполнено"}
                </>
              ) : message.toolStatus === "error" ? (
                <>
                  <XCircle className="size-3.5" />
                  {message.toolSummary || "Ошибка"}
                </>
              ) : (
                <>
                  <Loader2 className="size-3.5 animate-spin" />
                  {message.toolSummary || "Выполняется…"}
                </>
              )}
            </span>
          </div>
          {/* Тело с кодом/результатом */}
          <pre className="overflow-x-auto bg-[oklch(0.28_0.06_285)] px-4 py-3 font-mono text-xs leading-relaxed text-[oklch(0.92_0.05_310)]">
            <code>{message.toolCode || message.content}</code>
          </pre>
          {message.artifacts && (
            <div className="flex flex-wrap gap-2 border-t border-border px-4 py-3">
              {message.artifacts.map((artifact) => (
                <a
                  key={artifact.url}
                  href={artifact.url}
                  className="rounded-md bg-primary px-3 py-1.5 text-xs text-primary-foreground hover:opacity-90"
                >
                  {artifact.label}
                </a>
              ))}
            </div>
          )}
        </div>
        <p className="mt-1 pl-1 text-xs text-muted-foreground">{message.timestamp}</p>
      </div>
    </div>
  )
}
