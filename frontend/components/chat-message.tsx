"use client"

import {
  CheckCircle2,
  Circle,
  CircleStop,
  ListChecks,
  Loader2,
  Sparkles,
  Terminal,
  XCircle,
} from "lucide-react"
import { cn } from "@/lib/utils"
import type { ChatMessage, PlanStatus } from "@/lib/chat-types"
import { authHeaders } from "@/lib/admin-token"
import { getApiBase } from "@/lib/chat-api"
import { Markdown } from "@/components/markdown"

/** Fetch an artifact with the auth header, then save it via a blob URL. */
async function downloadArtifact(url: string, fallbackName: string) {
  const target = url.startsWith("http") ? url : `${getApiBase()}${url}`
  try {
    const response = await fetch(target, {
      credentials: "include",
      headers: authHeaders(),
    })
    if (!response.ok) {
      console.error(`Artifact download failed (${response.status}): ${target}`)
      return
    }
    const disposition = response.headers.get("Content-Disposition") || ""
    const match = /filename="?([^"]+)"?/.exec(disposition)
    const filename = match ? match[1] : fallbackName
    const objectUrl = URL.createObjectURL(await response.blob())
    const link = document.createElement("a")
    link.href = objectUrl
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    // Give the browser time to start the download before releasing the URL.
    setTimeout(() => URL.revokeObjectURL(objectUrl), 1000)
  } catch (error) {
    console.error(`Artifact download failed: ${target}`, error)
  }
}

const PLAN_STATUS_LABEL: Record<PlanStatus, string> = {
  proposed: "Awaiting approval",
  approved: "Approved",
  running: "Running",
  done: "Done",
  failed: "Failed",
  rejected: "Rejected",
  cancelled: "Cancelled",
}

function planStatusClass(status: PlanStatus): string {
  switch (status) {
    case "done":
      return "text-emerald-600"
    case "failed":
    case "rejected":
      return "text-destructive"
    case "cancelled":
      return "text-muted-foreground"
    default:
      return "text-primary"
  }
}

function StepStatusIcon({ status }: { status: string }) {
  if (status === "done") return <CheckCircle2 className="size-4 shrink-0 text-emerald-600" />
  if (status === "failed") return <XCircle className="size-4 shrink-0 text-destructive" />
  if (status === "running") return <Loader2 className="size-4 shrink-0 animate-spin text-primary" />
  if (status === "blocked") return <CircleStop className="size-4 shrink-0 text-muted-foreground" />
  return <Circle className="size-4 shrink-0 text-muted-foreground/50" />
}

function MessageTimestamp({ timestamp, className }: { timestamp: string; className?: string }) {
  return <p className={cn("mt-1 pl-1 text-xs text-muted-foreground", className)}>{timestamp}</p>
}

/**
 * Component rendering a single chat message.
 * Supports four roles: user, assistant, tool, plan.
 * For tool messages it shows the execution status (pending/success/error).
 * For plan messages it shows the goal, ordered steps and their status.
 */
export function ChatMessageItem({ message }: { message: ChatMessage }) {
  // --- User message ---
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[75%]">
          <div className="rounded-2xl rounded-br-md bg-[oklch(0.72_0.07_305)] px-3 py-2 text-sm leading-relaxed text-[oklch(0.18_0.02_295)] shadow-sm">
            <Markdown content={message.content} />
          </div>
          <p className="mt-1 pr-1 text-right text-xs text-muted-foreground">{message.timestamp}</p>
        </div>
      </div>
    )
  }

  // --- Assistant (LLM) message ---
  if (message.role === "assistant") {
    return (
      <div className="flex justify-start gap-3">
        <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
          <Sparkles className="size-4" />
        </div>
        <div className="max-w-[75%]">
          <div className="rounded-2xl rounded-tl-md border border-border bg-card px-3 py-2 text-sm leading-relaxed text-card-foreground shadow-sm backdrop-blur-md">
            <Markdown content={message.content} />
          </div>
          <p className="mt-1 pl-1 text-xs text-muted-foreground">{message.timestamp}</p>
        </div>
      </div>
    )
  }

  // --- Plan message ---
  if (message.role === "plan" && message.plan) {
    const plan = message.plan
    return (
      <div className="flex justify-start gap-3">
        <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/15 text-primary">
          <ListChecks className="size-4" />
        </div>
        <div className="w-full max-w-[75%]">
          <div className="overflow-hidden rounded-2xl rounded-tl-md border border-primary/25 bg-card shadow-sm backdrop-blur-md">
            <div className="flex items-center justify-between gap-2 border-b border-border bg-secondary/60 px-4 py-2.5">
              <span className="text-xs font-medium text-secondary-foreground">
                Plan · revision {plan.revision}
              </span>
              <span className={cn("text-xs font-medium", planStatusClass(plan.status))}>
                {PLAN_STATUS_LABEL[plan.status]}
              </span>
            </div>
            <div className="px-4 py-3">
              <p className="mb-3 text-sm font-medium text-card-foreground">{plan.goal}</p>
              <ol className="flex flex-col gap-2">
                {plan.steps.map((step, index) => (
                  <li key={step.id} className="flex items-start gap-2.5">
                    <StepStatusIcon status={step.status} />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-xs font-medium text-foreground">
                          {index + 1}. {step.tool}
                        </span>
                        <span
                          className={cn(
                            "rounded-full px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide",
                            step.risk === "dangerous"
                              ? "bg-destructive/15 text-destructive"
                              : step.risk === "moderate"
                              ? "bg-amber-500/15 text-amber-600"
                              : "bg-secondary text-secondary-foreground",
                          )}
                        >
                          {step.risk}
                        </span>
                      </div>
                      {step.why && (
                        <p className="mt-0.5 text-xs text-muted-foreground">{step.why}</p>
                      )}
                      {step.error && (
                        <p className="mt-0.5 text-xs text-destructive">{step.error}</p>
                      )}
                    </div>
                  </li>
                ))}
              </ol>
            </div>
          </div>
          <MessageTimestamp timestamp={message.timestamp} />
        </div>
      </div>
    )
  }

  // --- Tool message ---
  return (
    <div className="flex justify-start gap-3">
      <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full bg-accent text-accent-foreground">
        <Terminal className="size-4" />
      </div>
      <div className="w-full max-w-[75%]">
        <div className="overflow-hidden rounded-2xl rounded-tl-md border border-primary/25 bg-card shadow-sm backdrop-blur-md">
          {/* Header with the tool name and status */}
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
                  : message.toolStatus === "cancelled"
                  ? "text-muted-foreground"
                  : "text-primary",
              )}
            >
              {message.toolStatus === "success" ? (
                <>
                  <CheckCircle2 className="size-3.5" />
                  {message.toolSummary || "Done"}
                </>
              ) : message.toolStatus === "error" ? (
                <>
                  <XCircle className="size-3.5" />
                  {message.toolSummary || "Error"}
                </>
              ) : message.toolStatus === "cancelled" ? (
                <>
                  <CircleStop className="size-3.5" />
                  {message.toolSummary || "Cancelled"}
                </>
              ) : (
                <>
                  <Loader2 className="size-3.5 animate-spin" />
                  {message.toolSummary || "Running…"}
                </>
              )}
            </span>
          </div>
          {/* Body with the code/result */}
          <pre className="overflow-x-auto bg-[oklch(0.28_0.06_285)] px-4 py-3 font-mono text-xs leading-relaxed text-[oklch(0.92_0.05_310)]">
            <code>{message.toolCode || message.content}</code>
          </pre>
          {message.artifacts && (
            <div className="flex flex-wrap gap-2 border-t border-border px-4 py-3">
              {message.artifacts.map((artifact) => (
                <button
                  key={artifact.url}
                  type="button"
                  onClick={() => downloadArtifact(artifact.url, artifact.label)}
                  className="rounded-md bg-primary px-3 py-1.5 text-xs text-primary-foreground hover:opacity-90"
                >
                  {artifact.label}
                </button>
              ))}
            </div>
          )}
        </div>
        <p className="mt-1 pl-1 text-xs text-muted-foreground">{message.timestamp}</p>
      </div>
    </div>
  )
}
