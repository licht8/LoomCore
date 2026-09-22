"use client"

import { useState } from "react"

import { useQuery } from "@tanstack/react-query"
import { RotateCw } from "lucide-react"

import { ErrorBanner } from "@/components/error-banner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { fetchLlmUsage, type LlmUsageSummary } from "@/lib/llm-usage-api"
import { cn } from "@/lib/utils"

const RANGES = [
  { value: "1", label: "Last 24 hours" },
  { value: "7", label: "Last 7 days" },
  { value: "30", label: "Last 30 days" },
]

function formatCount(value: number | null): string {
  if (value === null) return "—"
  return value.toLocaleString()
}

function formatLatency(value: number | null): string {
  if (value === null) return "—"
  return `${value.toLocaleString()} ms`
}

function budgetText(budget: LlmUsageSummary["budget"]): string {
  if (budget.limit <= 0) return `${budget.used} used`
  return `${budget.used} / ${budget.limit}`
}

function TotalsCard({ label, value }: { label: string; value: string }) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle className="text-muted-foreground">{label}</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="font-mono text-2xl font-semibold tracking-tight text-foreground">
          {value}
        </p>
      </CardContent>
    </Card>
  )
}

function UsageContent({ data }: { data: LlmUsageSummary }) {
  const { totals, by_model, by_day, budget } = data

  return (
    <>
      <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <TotalsCard label="Calls" value={formatCount(totals.calls)} />
        <TotalsCard label="Tokens" value={formatCount(totals.total_tokens)} />
        <TotalsCard
          label="Avg latency"
          value={formatLatency(totals.avg_latency_ms)}
        />
        <TotalsCard label="Today's budget" value={budgetText(budget)} />
      </div>

      {totals.calls === 0 ? (
        <p className="mt-8 py-8 text-center text-sm text-muted-foreground">
          No LLM calls recorded in this period.
        </p>
      ) : (
        <>
          <section className="mt-8">
            <h2 className="text-sm font-medium text-foreground">By model</h2>
            <div className="mt-3 overflow-x-auto rounded-xl ring-1 ring-foreground/10">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-xs text-muted-foreground">
                    <th className="px-4 py-2 font-medium">Model</th>
                    <th className="px-4 py-2 text-right font-medium">Calls</th>
                    <th className="px-4 py-2 text-right font-medium">Prompt</th>
                    <th className="px-4 py-2 text-right font-medium">
                      Completion
                    </th>
                    <th className="px-4 py-2 text-right font-medium">Total</th>
                    <th className="px-4 py-2 text-right font-medium">
                      Avg latency
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {by_model.map((row) => (
                    <tr key={`${row.provider}/${row.model}`}>
                      <td className="px-4 py-2">
                        <span className="font-mono text-foreground">
                          {row.model || "—"}
                        </span>
                        {row.provider && (
                          <Badge variant="secondary" className="ml-2">
                            {row.provider}
                          </Badge>
                        )}
                      </td>
                      <td className="px-4 py-2 text-right font-mono">
                        {formatCount(row.calls)}
                      </td>
                      <td className="px-4 py-2 text-right font-mono">
                        {formatCount(row.prompt_tokens)}
                      </td>
                      <td className="px-4 py-2 text-right font-mono">
                        {formatCount(row.completion_tokens)}
                      </td>
                      <td className="px-4 py-2 text-right font-mono">
                        {formatCount(row.total_tokens)}
                      </td>
                      <td className="px-4 py-2 text-right font-mono">
                        {formatLatency(row.avg_latency_ms)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="mt-8">
            <h2 className="text-sm font-medium text-foreground">By day</h2>
            <div className="mt-3 divide-y divide-border border-t border-border">
              {by_day.map((row) => (
                <div
                  key={row.day}
                  className="flex items-center justify-between py-2 text-sm"
                >
                  <span className="font-mono text-foreground">{row.day}</span>
                  <span className="font-mono text-muted-foreground">
                    {formatCount(row.calls)} calls ·{" "}
                    {formatCount(row.total_tokens)} tokens
                  </span>
                </div>
              ))}
            </div>
          </section>
        </>
      )}
    </>
  )
}

export function UsageView() {
  const [days, setDays] = useState(7)

  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: ["llm-usage", days],
    queryFn: () => fetchLlmUsage(days),
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
  })

  return (
    <div className="flex-1 overflow-y-auto px-6 pt-24 pb-10">
      <div className="mx-auto w-full max-w-3xl">
        <div className="flex items-center justify-between gap-3">
          <h1 className="text-2xl font-bold tracking-tight text-foreground">
            Usage
          </h1>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="icon-sm"
              title="Refresh"
              onClick={() => {
                refetch()
              }}
              disabled={isFetching}
            >
              <RotateCw className={cn(isFetching && "animate-spin")} />
              <span className="sr-only">Refresh</span>
            </Button>
            <Select
              items={RANGES}
              value={String(days)}
              onValueChange={(value) => {
                if (value) {
                  setDays(Number(value))
                }
              }}
            >
              <SelectTrigger aria-label="Time range" className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {RANGES.map((range) => (
                  <SelectItem key={range.value} value={range.value}>
                    {range.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        {error && (
          <ErrorBanner
            message={
              error instanceof Error
                ? error.message
                : "Failed to load LLM usage"
            }
            actionLabel="Retry"
            onAction={() => {
              refetch()
            }}
          />
        )}

        {isLoading ? (
          <div className="mt-6 space-y-3">
            {Array.from({ length: 3 }).map((_, index) => (
              <div key={index} className="h-24 animate-pulse bg-muted/40" />
            ))}
          </div>
        ) : data ? (
          <UsageContent data={data} />
        ) : null}
      </div>
    </div>
  )
}
