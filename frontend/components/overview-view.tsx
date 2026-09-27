"use client"

import type { ReactNode } from "react"

import { useQuery, useQueryClient } from "@tanstack/react-query"
import { RotateCw } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { ErrorBanner } from "@/components/error-banner"
import { Markdown } from "@/components/markdown"
import { fetchOverview, type ToolResult } from "@/lib/overview-api"
import { cn } from "@/lib/utils"

function toolText(result: ToolResult | null): string | null {
  const text = result?.content?.[0]?.text
  return typeof text === "string" && text.length > 0 ? text : null
}

function Card({
  title,
  children,
}: {
  title: string
  children: ReactNode
}) {
  return (
    <section className="rounded-2xl border border-border bg-card/40 p-4">
      <h2 className="mb-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
        {title}
      </h2>
      {children}
    </section>
  )
}

function Unavailable() {
  return <p className="text-sm text-muted-foreground">unavailable</p>
}

function ResultBody({ result }: { result: ToolResult | null }) {
  const text = toolText(result)
  if (!text) return <Unavailable />
  return (
    <div className="text-sm leading-relaxed text-card-foreground">
      <Markdown content={text} />
    </div>
  )
}

function pluginStateVariant(state?: string) {
  if (state === "running") return "secondary" as const
  return "outline" as const
}

export function OverviewView() {
  const queryClient = useQueryClient()
  const { data, isLoading, isFetching, error } = useQuery({
    queryKey: ["overview"],
    queryFn: fetchOverview,
    refetchInterval: 15_000,
  })

  return (
    <div className="flex-1 overflow-y-auto px-6 pt-24 pb-10">
      <div className="mx-auto w-full max-w-4xl">
        <div className="flex items-center justify-between gap-3">
          <h1 className="text-2xl font-bold tracking-tight text-foreground">Overview</h1>
          <Button
            variant="ghost"
            size="icon-sm"
            title="Refresh"
            onClick={() => queryClient.invalidateQueries({ queryKey: ["overview"] })}
            disabled={isFetching}
          >
            <RotateCw className={cn(isFetching && "animate-spin")} />
            <span className="sr-only">Refresh</span>
          </Button>
        </div>

        {error && (
          <ErrorBanner
            message={error instanceof Error ? error.message : "Failed to load the overview"}
            actionLabel="Retry"
            onAction={() => queryClient.invalidateQueries({ queryKey: ["overview"] })}
          />
        )}

        {isLoading ? (
          <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-2">
            {Array.from({ length: 6 }).map((_, index) => (
              <div key={index} className="h-32 animate-pulse rounded-2xl bg-muted/40" />
            ))}
          </div>
        ) : data ? (
          <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-2">
            <Card title="Core">
              <ResultBody result={data.core} />
            </Card>

            <Card title="Plugins">
              {data.plugins.length === 0 ? (
                <p className="text-sm text-muted-foreground">No plugins.</p>
              ) : (
                <ul className="flex flex-col gap-1.5">
                  {data.plugins.map((plugin) => (
                    <li
                      key={plugin.name}
                      className="flex items-center justify-between gap-2"
                    >
                      <span className="truncate text-sm text-card-foreground">
                        {plugin.name}
                        {plugin.version ? (
                          <span className="ml-1 text-xs text-muted-foreground">
                            v{plugin.version}
                          </span>
                        ) : null}
                      </span>
                      <Badge variant={pluginStateVariant(plugin.state)}>
                        {plugin.enabled ? plugin.state : "disabled"}
                      </Badge>
                    </li>
                  ))}
                </ul>
              )}
            </Card>

            <Card title="Disk">
              <ResultBody result={data.host?.disk ?? null} />
            </Card>

            <Card title="Metrics">
              <ResultBody result={data.host?.metrics ?? null} />
            </Card>

            <Card title="WireGuard">
              <ResultBody result={data.wireguard} />
            </Card>

            <Card title="Failed units">
              <ResultBody result={data.host?.failed_units ?? null} />
            </Card>
          </div>
        ) : null}
      </div>
    </div>
  )
}
