"use client"

import { useMemo, useState } from "react"

import { useQuery, useQueryClient } from "@tanstack/react-query"
import { ChevronRight, RotateCw } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { ErrorBanner } from "@/components/error-banner"
import { fetchTools, type ToolInfo } from "@/lib/tools-api"
import { cn } from "@/lib/utils"

const RISKS = ["safe", "moderate", "dangerous"] as const
type RiskFilter = "all" | (typeof RISKS)[number]

function riskVariant(risk?: string) {
  if (risk === "dangerous") return "destructive" as const
  if (risk === "moderate") return "outline" as const
  return "secondary" as const
}

function groupOf(name: string): string {
  const prefix = name.split("_")[0]
  return prefix === "core" || prefix === "ops" || prefix === "wg" ? prefix : "other"
}

function ToolRow({ tool }: { tool: ToolInfo }) {
  const [open, setOpen] = useState(false)
  const properties = tool.inputSchema?.properties ?? {}
  const required = tool.inputSchema?.required ?? []
  const propNames = Object.keys(properties)

  return (
    <div className="border-b border-border last:border-b-0">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-start gap-3 px-1 py-3 text-left hover:bg-foreground/[0.02]"
        aria-expanded={open}
      >
        <ChevronRight
          className={cn(
            "mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-90",
          )}
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-sm font-medium text-foreground">
              {tool.name}
            </span>
            {tool.risk && (
              <Badge variant={riskVariant(tool.risk)} className="uppercase">
                {tool.risk}
              </Badge>
            )}
            {tool.report && <Badge variant="ghost">report</Badge>}
          </div>
          {tool.description && (
            <p className="mt-1 text-xs text-muted-foreground">{tool.description}</p>
          )}
        </div>
      </button>
      {open && (
        <div className="px-8 pb-4">
          {propNames.length === 0 ? (
            <p className="text-xs text-muted-foreground">No arguments.</p>
          ) : (
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="text-muted-foreground">
                  <th className="py-1 pr-4 font-medium">Property</th>
                  <th className="py-1 pr-4 font-medium">Type</th>
                  <th className="py-1 font-medium">Description</th>
                </tr>
              </thead>
              <tbody>
                {propNames.map((name) => (
                  <tr key={name} className="border-t border-border/50 align-top">
                    <td className="py-1.5 pr-4 font-mono text-foreground">
                      {name}
                      {required.includes(name) && (
                        <span className="ml-1 text-destructive">*</span>
                      )}
                    </td>
                    <td className="py-1.5 pr-4 font-mono text-muted-foreground">
                      {properties[name]?.type ?? "any"}
                    </td>
                    <td className="py-1.5 text-muted-foreground">
                      {properties[name]?.description ?? ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  )
}

export function ToolsView() {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState("")
  const [risk, setRisk] = useState<RiskFilter>("all")

  const { data: tools = [], isLoading, isFetching, error } = useQuery({
    queryKey: ["tools"],
    queryFn: fetchTools,
  })

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return tools.filter((tool) => {
      if (risk !== "all" && tool.risk !== risk) return false
      if (!needle) return true
      return (
        tool.name.toLowerCase().includes(needle) ||
        (tool.description ?? "").toLowerCase().includes(needle)
      )
    })
  }, [tools, search, risk])

  const groups = useMemo(() => {
    const order = ["core", "ops", "wg", "other"]
    const map = new Map<string, ToolInfo[]>()
    for (const tool of filtered) {
      const key = groupOf(tool.name)
      const list = map.get(key) ?? []
      list.push(tool)
      map.set(key, list)
    }
    return order
      .filter((key) => map.has(key))
      .map((key) => ({ key, tools: map.get(key) ?? [] }))
  }, [filtered])

  return (
    <div className="flex-1 overflow-y-auto px-6 pt-24 pb-10">
      <div className="mx-auto w-full max-w-3xl">
        <div className="flex items-center justify-between gap-3">
          <h1 className="text-2xl font-bold tracking-tight text-foreground">Tools</h1>
          <div className="flex items-center gap-3">
            {tools.length > 0 && (
              <span className="font-mono text-xs text-muted-foreground">
                {filtered.length} / {tools.length} tools
              </span>
            )}
            <Button
              variant="ghost"
              size="icon-sm"
              title="Refresh"
              onClick={() => queryClient.invalidateQueries({ queryKey: ["tools"] })}
              disabled={isFetching}
            >
              <RotateCw className={cn(isFetching && "animate-spin")} />
              <span className="sr-only">Refresh</span>
            </Button>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search tools…"
            className="max-w-xs"
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
        </div>

        {error && (
          <ErrorBanner
            message={error instanceof Error ? error.message : "Failed to load tools"}
            actionLabel="Retry"
            onAction={() => queryClient.invalidateQueries({ queryKey: ["tools"] })}
          />
        )}

        {isLoading ? (
          <div className="mt-6 space-y-3">
            {Array.from({ length: 5 }).map((_, index) => (
              <div key={index} className="h-14 animate-pulse bg-muted/40" />
            ))}
          </div>
        ) : groups.length === 0 ? (
          <p className="mt-6 py-8 text-center text-sm text-muted-foreground">
            No tools match the filter.
          </p>
        ) : (
          <div className="mt-6 flex flex-col gap-6">
            {groups.map((group) => (
              <section key={group.key}>
                <h2 className="mb-1 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                  {group.key} ({group.tools.length})
                </h2>
                <div className="rounded-lg border border-border px-1">
                  {group.tools.map((tool) => (
                    <ToolRow key={tool.name} tool={tool} />
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
