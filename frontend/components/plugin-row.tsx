"use client"

import { Loader2, RotateCw, TriangleAlert } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import type { PluginAction, PluginInfo } from "@/lib/plugins-api"
import { cn } from "@/lib/utils"

type RowStatus = "RUNNING" | "BUSY" | "STOPPED" | "DISABLED"

const dotTone: Record<RowStatus, string> = {
  RUNNING: "bg-emerald-500",
  BUSY: "bg-amber-500",
  STOPPED: "bg-muted-foreground/40",
  DISABLED: "bg-muted-foreground/40",
}

function rowStatus(plugin: PluginInfo): RowStatus {
  if (!plugin.enabled) return "DISABLED"
  if (!plugin.is_healthy) return "STOPPED"
  if (plugin.state === "busy") return "BUSY"
  return "RUNNING"
}

function metaLine(plugin: PluginInfo): string {
  const version = plugin.version ? `v${plugin.version}` : "no version"
  const tools = `${plugin.tools} ${plugin.tools === 1 ? "tool" : "tools"}`
  return `${version} · ${tools}`
}

export function PluginRow({
  plugin,
  pendingAction,
  index,
  onAction,
}: {
  plugin: PluginInfo
  pendingAction: PluginAction | null
  index: number
  onAction: (action: PluginAction) => void
}) {
  const pending = pendingAction !== null
  const status = rowStatus(plugin)
  const warnings = plugin.permission_warnings

  return (
    <div
      className="plugin-row-in flex min-h-16 items-center gap-3 py-3 transition-colors duration-150 hover:bg-muted/40"
      style={{ animationDelay: `${index < 8 ? index * 25 : 0}ms` }}
    >
      <span
        className={cn("size-2 shrink-0 rounded-full", dotTone[status])}
        aria-hidden
      />

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="truncate text-sm font-semibold text-foreground">
            {plugin.name}
          </span>
          {warnings.length > 0 && (
            <span
              className="flex shrink-0 text-amber-500"
              title={warnings.join("\n")}
              aria-label={`${warnings.length} permission warnings`}
            >
              <TriangleAlert className="size-3.5" />
            </span>
          )}
        </div>
        <p className="truncate font-mono text-xs text-muted-foreground">
          {metaLine(plugin)}
        </p>
      </div>

      <div className="flex w-40 shrink-0 items-center justify-end gap-2">
        <span
          className={cn(
            "font-mono text-[10px] tracking-wider uppercase text-muted-foreground",
            status === "BUSY" && "text-amber-500",
          )}
        >
          {status}
        </span>
        <Switch
          checked={plugin.enabled}
          disabled={pending}
          aria-label={
            plugin.enabled ? `Disable ${plugin.name}` : `Enable ${plugin.name}`
          }
          onCheckedChange={(checked) => onAction(checked ? "enable" : "disable")}
        />
        <Button
          variant="ghost"
          size="icon-sm"
          title="Reload"
          disabled={pending}
          onClick={() => onAction("reload")}
        >
          {pendingAction === "reload" ? (
            <Loader2 className="animate-spin" />
          ) : (
            <RotateCw />
          )}
          <span className="sr-only">Reload {plugin.name}</span>
        </Button>
      </div>
    </div>
  )
}
