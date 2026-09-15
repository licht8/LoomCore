"use client"

import { useCallback, useEffect, useState } from "react"
import {
  Activity,
  Boxes,
  ChevronDown,
  RefreshCw,
  Server,
  Wrench,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { headerPillClass } from "@/components/header-pill"
import { InfoRow } from "@/components/info-row"
import {
  Popover,
  PopoverArrow,
  PopoverContent,
  PopoverDescription,
  PopoverTitle,
  PopoverTrigger,
} from "@/components/ui/popover"
import { fetchHealth, fetchHubSnapshot, type HubSnapshot } from "@/lib/hub-status"
import { cn } from "@/lib/utils"

type PluginTone = "emerald" | "amber" | "muted"

const toneBadge: Record<PluginTone, string> = {
  emerald: "gap-1.5 border-transparent bg-emerald-400/15 text-emerald-500",
  amber: "gap-1.5 border-transparent bg-amber-400/15 text-amber-500",
  muted: "gap-1.5 border-transparent bg-muted text-muted-foreground",
}

const toneDot: Record<PluginTone, string> = {
  emerald: "bg-emerald-400",
  amber: "bg-amber-400",
  muted: "bg-muted-foreground",
}

function pluginState(healthy: boolean, state: string): { label: string; tone: PluginTone } {
  if (healthy) return { label: "Running", tone: "emerald" }
  if (state === "busy") return { label: "Busy", tone: "amber" }
  if (state === "idle") return { label: "Idle", tone: "amber" }
  return { label: "Stopped", tone: "muted" }
}

export function HubStatusButton() {
  const [open, setOpen] = useState(false)
  const [online, setOnline] = useState<boolean | null>(null)
  const [origin, setOrigin] = useState("")
  const [snapshot, setSnapshot] = useState<HubSnapshot | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)

  useEffect(() => {
    setOrigin(window.location.origin)
    let active = true
    const check = () => {
      fetchHealth().then((ok) => {
        if (active) setOnline(ok)
      })
    }
    check()
    const timer = setInterval(check, 20000)
    return () => {
      active = false
      clearInterval(timer)
    }
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setError("")
    try {
      const data = await fetchHubSnapshot()
      setSnapshot(data)
      setUpdatedAt(new Date())
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load hub telemetry")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open) load()
  }, [open, load])

  const plugins = snapshot?.plugins ?? []
  const healthy = plugins.filter((plugin) => plugin.is_healthy).length
  const statusText =
    online === null
      ? "Checking control plane…"
      : online
        ? "Control plane online"
        : "Control plane offline"

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger className={headerPillClass}>
        <span className="relative flex size-3.5 items-center justify-center">
          {online && (
            <span className="absolute inline-flex size-3.5 animate-ping rounded-full bg-emerald-400/50" />
          )}
          <Activity
            className={cn(
              "relative size-3.5",
              online === false ? "text-destructive" : "text-emerald-500",
            )}
          />
        </span>
        <div>
          <p className="text-xs font-medium text-foreground">Self-hosted Hub</p>
          <p className="text-[11px] text-muted-foreground">{statusText}</p>
        </div>
        <ChevronDown className="size-3.5 text-muted-foreground transition-transform duration-300 group-data-[popup-open]:rotate-180" />
      </PopoverTrigger>

      <PopoverContent className="w-80">
        <PopoverArrow />
        <div className="flex items-start justify-between gap-3">
          <div>
            <PopoverTitle>Self-hosted Hub</PopoverTitle>
            <PopoverDescription>Live control-plane telemetry</PopoverDescription>
          </div>
          <Badge
            className={cn(
              "gap-1.5 border-transparent",
              online
                ? "bg-emerald-400/15 text-emerald-500"
                : "bg-destructive/10 text-destructive",
            )}
          >
            <span
              className={cn(
                "size-1.5 rounded-full",
                online ? "bg-emerald-400" : "bg-destructive",
              )}
            />
            {online ? "Online" : "Offline"}
          </Badge>
        </div>

        <div className="mt-4 space-y-2">
          <InfoRow icon={Server} label="Endpoint" value={origin || "—"} />
          <InfoRow
            icon={Boxes}
            label="Plugins"
            value={
              loading && !snapshot
                ? "…"
                : `${healthy}/${plugins.length} healthy`
            }
          />
          <InfoRow
            icon={Wrench}
            label="Tools available"
            value={loading && !snapshot ? "…" : `${snapshot?.toolCount ?? 0}`}
          />
        </div>

        <div className="mt-4 space-y-1.5">
          <p className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
            Plugins
          </p>
          {plugins.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              {loading ? "Loading plugins…" : "No plugins discovered"}
            </p>
          ) : (
            plugins.map((plugin) => {
              const status = pluginState(plugin.is_healthy, plugin.state)
              return (
                <div
                  key={plugin.name}
                  className="flex items-center justify-between gap-2 rounded-lg bg-muted/40 px-2.5 py-1.5"
                >
                  <span className="text-xs font-medium text-foreground">
                    {plugin.name}
                  </span>
                  <Badge className={toneBadge[status.tone]}>
                    <span
                      className={cn("size-1.5 rounded-full", toneDot[status.tone])}
                    />
                    {status.label}
                  </Badge>
                </div>
              )
            })
          )}
        </div>

        {error && <p className="mt-3 text-xs text-destructive">{error}</p>}

        <div className="mt-4 flex items-center justify-between gap-2 border-t border-border pt-3">
          <span className="text-[11px] text-muted-foreground">
            {updatedAt ? `Updated ${updatedAt.toLocaleTimeString()}` : "Not loaded"}
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={load}
            disabled={loading}
          >
            <RefreshCw className={cn("size-3.5", loading && "animate-spin")} />
            Refresh
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  )
}
