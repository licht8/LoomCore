"use client"

import Link from "next/link"

import { useQuery, useQueryClient } from "@tanstack/react-query"
import { ExternalLink, Inbox, RotateCw, TriangleAlert } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { ErrorBanner } from "@/components/error-banner"
import { fetchApprovals } from "@/lib/approvals-api"
import { cn } from "@/lib/utils"

function age(iso: string): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ""
  const minutes = Math.floor((Date.now() - then) / 60_000)
  if (minutes < 1) return "just now"
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

function riskVariant(risk: string) {
  if (risk === "dangerous") return "destructive" as const
  if (risk === "moderate") return "outline" as const
  return "secondary" as const
}

export function ApprovalsView() {
  const queryClient = useQueryClient()
  const { data: approvals = [], isLoading, isFetching, error } = useQuery({
    queryKey: ["approvals"],
    queryFn: fetchApprovals,
    refetchInterval: 20_000,
  })

  return (
    <div className="flex-1 overflow-y-auto px-6 pt-24 pb-10">
      <div className="mx-auto w-full max-w-3xl">
        <div className="flex items-center justify-between gap-3">
          <h1 className="text-2xl font-bold tracking-tight text-foreground">Approvals</h1>
          <div className="flex items-center gap-3">
            {approvals.length > 0 && (
              <span className="font-mono text-xs text-muted-foreground">
                {approvals.length} pending
              </span>
            )}
            <Button
              variant="ghost"
              size="icon-sm"
              title="Refresh"
              onClick={() => queryClient.invalidateQueries({ queryKey: ["approvals"] })}
              disabled={isFetching}
            >
              <RotateCw className={cn(isFetching && "animate-spin")} />
              <span className="sr-only">Refresh</span>
            </Button>
          </div>
        </div>

        <p className="mt-2 text-xs text-muted-foreground">
          Read-only — approve, edit or reject plans in the chat.
        </p>

        {error && (
          <ErrorBanner
            message={
              error instanceof Error ? error.message : "Failed to load approvals"
            }
            actionLabel="Retry"
            onAction={() => queryClient.invalidateQueries({ queryKey: ["approvals"] })}
          />
        )}

        {isLoading ? (
          <div className="mt-6 space-y-3">
            {Array.from({ length: 3 }).map((_, index) => (
              <div key={index} className="h-24 animate-pulse rounded-2xl bg-muted/40" />
            ))}
          </div>
        ) : approvals.length === 0 ? (
          <div className="mt-16 flex flex-col items-center gap-2 text-center text-muted-foreground">
            <Inbox className="size-8" />
            <p className="text-sm">No plans are waiting for approval.</p>
          </div>
        ) : (
          <ul className="mt-6 flex flex-col gap-3">
            {approvals.map((approval) => {
              const dangerous = approval.risks.includes("dangerous")
              return (
                <li
                  key={approval.plan_id}
                  className="rounded-2xl border border-border bg-card/40 p-4"
                >
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-card-foreground">
                        {approval.goal || "Untitled plan"}
                      </p>
                      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                        <span>
                          {approval.step_count} step
                          {approval.step_count === 1 ? "" : "s"}
                        </span>
                        <span>{age(approval.created_at)}</span>
                        <Link
                          href={`/?session=${encodeURIComponent(approval.session_id)}`}
                          className="font-mono text-primary hover:underline"
                        >
                          {approval.session_id.slice(0, 12)}…
                        </Link>
                      </div>
                      <div className="mt-2 flex flex-wrap items-center gap-1.5">
                        {approval.risks.map((risk) => (
                          <Badge key={risk} variant={riskVariant(risk)} className="uppercase">
                            {risk}
                          </Badge>
                        ))}
                      </div>
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      render={
                        <Link
                          href={`/?session=${encodeURIComponent(approval.session_id)}`}
                        />
                      }
                    >
                      <ExternalLink />
                      Open in chat
                    </Button>
                  </div>
                  {dangerous && (
                    <p className="mt-3 flex items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                      <TriangleAlert className="size-3.5 shrink-0" />
                      This plan contains a dangerous step and needs explicit approval.
                    </p>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </div>
  )
}
