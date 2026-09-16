"use client"

import { useCallback, useEffect, useState } from "react"
import { RotateCw } from "lucide-react"

import { Button } from "@/components/ui/button"
import { PluginRow } from "@/components/plugin-row"
import {
  disablePlugin,
  enablePlugin,
  fetchPlugins,
  reloadPlugin,
  type PluginAction,
  type PluginInfo,
} from "@/lib/plugins-api"
import { cn } from "@/lib/utils"

export function PluginsView() {
  const [plugins, setPlugins] = useState<PluginInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [pending, setPending] = useState<{
    name: string
    action: PluginAction
  } | null>(null)

  const load = useCallback(async (showSpinner = false) => {
    if (showSpinner) setLoading(true)
    try {
      const data = await fetchPlugins()
      setPlugins(data)
      setError("")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load plugins")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()

    const timer = setInterval(() => {
      if (document.visibilityState === "visible") load()
    }, 5 * 60 * 1000)

    const onVisible = () => {
      if (document.visibilityState === "visible") load()
    }
    document.addEventListener("visibilitychange", onVisible)

    return () => {
      clearInterval(timer)
      document.removeEventListener("visibilitychange", onVisible)
    }
  }, [load])

  const runAction = useCallback(
    async (plugin: PluginInfo, action: PluginAction) => {
      setPending({ name: plugin.name, action })
      setError("")
      try {
        if (action === "enable") await enablePlugin(plugin.name)
        else if (action === "disable") await disablePlugin(plugin.name)
        else await reloadPlugin(plugin.name)
        await load()
      } catch (err) {
        setError(err instanceof Error ? err.message : "Action failed")
      } finally {
        setPending(null)
      }
    },
    [load],
  )

  const running = plugins.filter(
    (plugin) => plugin.enabled && plugin.is_healthy,
  ).length
  const isEmpty = !loading && plugins.length === 0

  return (
    <div className="flex-1 overflow-y-auto px-6 pt-24 pb-10">
      <div className="mx-auto w-full max-w-3xl">
        <div className="flex items-center justify-between gap-3">
          <h1 className="text-2xl font-bold tracking-tight text-foreground">
            Plugins
          </h1>
          <div className="flex items-center gap-3">
            {plugins.length > 0 && (
              <span className="font-mono text-xs text-muted-foreground">
                {plugins.length} plugins · {running} running
              </span>
            )}
            <Button
              variant="ghost"
              size="icon-sm"
              title="Refresh"
              onClick={() => load(true)}
              disabled={loading}
            >
              <RotateCw className={cn(loading && "animate-spin")} />
              <span className="sr-only">Refresh</span>
            </Button>
          </div>
        </div>

        {error && (
          <div className="mt-4 flex items-center gap-2 text-xs text-destructive">
            <span className="truncate">{error}</span>
            <button
              type="button"
              onClick={() => load(true)}
              className="shrink-0 font-medium underline underline-offset-4 hover:opacity-80"
            >
              Retry
            </button>
          </div>
        )}

        {loading && plugins.length === 0 ? (
          <div className="mt-6 space-y-3">
            {Array.from({ length: 3 }).map((_, index) => (
              <div key={index} className="h-16 animate-pulse bg-muted/40" />
            ))}
          </div>
        ) : isEmpty ? (
          <p className="mt-6 py-8 text-center text-sm text-muted-foreground">
            No plugins discovered.
          </p>
        ) : (
          <div className="mt-6 divide-y divide-border border-t border-border">
            {plugins.map((plugin, index) => (
              <PluginRow
                key={plugin.name}
                plugin={plugin}
                index={index}
                pendingAction={
                  pending?.name === plugin.name ? pending.action : null
                }
                onAction={(action) => runAction(plugin, action)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
