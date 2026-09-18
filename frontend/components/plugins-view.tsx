"use client"

import { useState } from "react"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
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
  const queryClient = useQueryClient()
  const [actionError, setActionError] = useState<string | null>(null)

  const {
    data: plugins = [],
    isLoading,
    isFetching,
    error,
  } = useQuery({
    queryKey: ["plugins"],
    queryFn: fetchPlugins,
    refetchInterval: 5 * 60_000,
    refetchIntervalInBackground: false,
  })

  const mutation = useMutation({
    mutationFn: ({ name, action }: { name: string; action: PluginAction }) => {
      if (action === "enable") return enablePlugin(name)
      if (action === "disable") return disablePlugin(name)
      return reloadPlugin(name)
    },
    onSuccess: () => {
      setActionError(null)
      queryClient.invalidateQueries({ queryKey: ["plugins"] })
    },
    onError: (err) => {
      setActionError(err instanceof Error ? err.message : "Action failed")
    },
  })

  const running = plugins.filter(
    (plugin) => plugin.enabled && plugin.is_healthy,
  ).length
  const isEmpty = !isLoading && plugins.length === 0
  const pending = mutation.isPending ? mutation.variables : null

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
              onClick={() => {
                queryClient.invalidateQueries({ queryKey: ["plugins"] })
              }}
              disabled={isFetching}
            >
              <RotateCw className={cn(isFetching && "animate-spin")} />
              <span className="sr-only">Refresh</span>
            </Button>
          </div>
        </div>

        {error && (
          <div className="mt-4 flex items-center gap-2 text-xs text-destructive">
            <span className="truncate">
              {error instanceof Error ? error.message : "Failed to load plugins"}
            </span>
            <button
              type="button"
              onClick={() => {
                queryClient.invalidateQueries({ queryKey: ["plugins"] })
              }}
              className="shrink-0 font-medium underline underline-offset-4 hover:opacity-80"
            >
              Retry
            </button>
          </div>
        )}

        {actionError && (
          <div className="mt-4 flex items-center gap-2 text-xs text-destructive">
            <span className="truncate">{actionError}</span>
            <button
              type="button"
              onClick={() => setActionError(null)}
              className="shrink-0 font-medium underline underline-offset-4 hover:opacity-80"
            >
              Dismiss
            </button>
          </div>
        )}

        {isLoading ? (
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
                onAction={(action) => {
                  setActionError(null)
                  mutation.mutate({ name: plugin.name, action })
                }}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
