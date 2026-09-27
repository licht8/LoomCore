"use client"

import { useState } from "react"
import Link from "next/link"

import { useQuery, useQueryClient } from "@tanstack/react-query"
import { RotateCw, ShieldCheck } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { ErrorBanner } from "@/components/error-banner"
import {
  fetchAudit,
  verifyAudit,
  type AuditEntry,
} from "@/lib/audit-api"
import { cn } from "@/lib/utils"

const PAGE_SIZE = 20
const STATUSES = ["success", "failed"] as const
const RISKS = ["safe", "moderate", "dangerous"] as const

function riskVariant(risk: string | null) {
  if (risk === "dangerous") return "destructive" as const
  if (risk === "moderate") return "outline" as const
  return "secondary" as const
}

function argsPreview(args: Record<string, unknown> | null): string {
  if (!args) return ""
  const text = JSON.stringify(args)
  return text.length > 80 ? `${text.slice(0, 80)}…` : text
}

export function AuditView() {
  const queryClient = useQueryClient()
  const [offset, setOffset] = useState(0)
  const [toolFilter, setToolFilter] = useState("")
  const [status, setStatus] = useState<string>("all")
  const [risk, setRisk] = useState<string>("all")
  const [verify, setVerify] = useState<{ valid: boolean; count: number } | null>(null)
  const [verifying, setVerifying] = useState(false)

  const { data, isLoading, isFetching, error } = useQuery({
    queryKey: ["audit", offset],
    queryFn: () => fetchAudit({ limit: PAGE_SIZE, offset }),
    placeholderData: (previous) => previous,
  })

  const entries: AuditEntry[] = data?.entries ?? []
  const total = data?.total ?? 0

  const filtered = entries.filter((entry) => {
    if (status !== "all" && entry.status !== status) return false
    if (risk !== "all" && entry.risk !== risk) return false
    if (toolFilter && !entry.tool.toLowerCase().includes(toolFilter.toLowerCase())) {
      return false
    }
    return true
  })

  async function runVerify() {
    setVerifying(true)
    try {
      const result = await verifyAudit()
      setVerify({ valid: result.valid, count: result.count })
    } catch (err) {
      setVerify(null)
      console.error(err)
    } finally {
      setVerifying(false)
    }
  }

  const hasPrev = offset > 0
  const hasNext = offset + PAGE_SIZE < total

  return (
    <div className="flex-1 overflow-y-auto px-6 pt-24 pb-10">
      <div className="mx-auto w-full max-w-4xl">
        <div className="flex items-center justify-between gap-3">
          <h1 className="text-2xl font-bold tracking-tight text-foreground">Audit log</h1>
          <div className="flex items-center gap-3">
            {total > 0 && (
              <span className="font-mono text-xs text-muted-foreground">
                {total} entries
              </span>
            )}
            <Button
              variant="ghost"
              size="sm"
              onClick={runVerify}
              disabled={verifying}
            >
              <ShieldCheck className={cn(verifying && "animate-pulse")} />
              Verify chain
            </Button>
            <Button
              variant="ghost"
              size="icon-sm"
              title="Refresh"
              onClick={() => queryClient.invalidateQueries({ queryKey: ["audit"] })}
              disabled={isFetching}
            >
              <RotateCw className={cn(isFetching && "animate-spin")} />
              <span className="sr-only">Refresh</span>
            </Button>
          </div>
        </div>

        {verify && (
          <div
            className={cn(
              "mt-3 rounded-lg border px-3 py-2 text-xs",
              verify.valid
                ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                : "border-destructive/40 bg-destructive/10 text-destructive",
            )}
          >
            {verify.valid
              ? `Chain valid · ${verify.count} entries`
              : `Chain INVALID · ${verify.count} entries`}
          </div>
        )}

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <input
            value={toolFilter}
            onChange={(event) => setToolFilter(event.target.value)}
            placeholder="Filter by tool…"
            className="h-9 min-w-0 rounded-lg border border-input bg-background px-3 text-sm shadow-xs outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
          />
          <div className="flex gap-1">
            {(["all", ...RISKS] as const).map((value) => (
              <Button
                key={value}
                variant={risk === value ? "secondary" : "ghost"}
                size="xs"
                onClick={() => setRisk(value)}
              >
                {value}
              </Button>
            ))}
          </div>
          <div className="flex gap-1">
            {(["all", ...STATUSES] as const).map((value) => (
              <Button
                key={value}
                variant={status === value ? "secondary" : "ghost"}
                size="xs"
                onClick={() => setStatus(value)}
              >
                {value}
              </Button>
            ))}
          </div>
        </div>

        {error && (
          <ErrorBanner
            message={error instanceof Error ? error.message : "Failed to load the audit log"}
            actionLabel="Retry"
            onAction={() => queryClient.invalidateQueries({ queryKey: ["audit"] })}
          />
        )}

        {isLoading ? (
          <div className="mt-6 space-y-2">
            {Array.from({ length: 6 }).map((_, index) => (
              <div key={index} className="h-10 animate-pulse rounded bg-muted/40" />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <p className="mt-6 py-8 text-center text-sm text-muted-foreground">
            {entries.length === 0 ? "No audit entries." : "No entries match the filter."}
          </p>
        ) : (
          <div className="mt-6 overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-left text-xs">
              <thead className="bg-muted/40 text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 font-medium">Time</th>
                  <th className="px-3 py-2 font-medium">Tool</th>
                  <th className="px-3 py-2 font-medium">Risk</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 font-medium">Actor</th>
                  <th className="px-3 py-2 font-medium">Session</th>
                  <th className="px-3 py-2 font-medium">Arguments</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((entry) => (
                  <tr key={entry.id} className="border-t border-border/60 align-top">
                    <td className="px-3 py-2 text-muted-foreground whitespace-nowrap">
                      {new Date(entry.created_at).toLocaleString()}
                    </td>
                    <td className="px-3 py-2 font-mono text-foreground">{entry.tool}</td>
                    <td className="px-3 py-2">
                      {entry.risk && (
                        <Badge variant={riskVariant(entry.risk)} className="uppercase">
                          {entry.risk}
                        </Badge>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <Badge
                        variant={entry.status === "failed" ? "destructive" : "secondary"}
                      >
                        {entry.status}
                      </Badge>
                    </td>
                    <td className="px-3 py-2 text-muted-foreground">{entry.actor}</td>
                    <td className="px-3 py-2">
                      {entry.session_id ? (
                        <Link
                          href={`/?session=${encodeURIComponent(entry.session_id)}`}
                          className="font-mono text-primary hover:underline"
                        >
                          {entry.session_id.slice(0, 12)}…
                        </Link>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </td>
                    <td className="px-3 py-2 font-mono text-muted-foreground">
                      {argsPreview(entry.arguments)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="mt-4 flex items-center justify-between">
          <Button
            variant="outline"
            size="sm"
            disabled={!hasPrev || isFetching}
            onClick={() => setOffset((value) => Math.max(0, value - PAGE_SIZE))}
          >
            Prev
          </Button>
          <span className="text-xs text-muted-foreground">
            {total === 0 ? "0" : `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)}`} of {total}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={!hasNext || isFetching}
            onClick={() => setOffset((value) => value + PAGE_SIZE)}
          >
            Next
          </Button>
        </div>
      </div>
    </div>
  )
}
