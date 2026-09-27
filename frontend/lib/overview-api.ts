import { requestJson } from "@/lib/api-client"
import type { PluginInfo } from "@/lib/plugins-api"

export type ToolResult = {
  content?: { type: string; text?: string }[]
  data?: Record<string, unknown>
}

export type Overview = {
  core: ToolResult | null
  plugins: PluginInfo[]
  host: {
    disk: ToolResult | null
    metrics: ToolResult | null
    failed_units: ToolResult | null
  }
  wireguard: ToolResult | null
}

/** Load the curated read-only host/plugin overview. */
export function fetchOverview(): Promise<Overview> {
  return requestJson<Overview>("/api/overview", {}, "Failed to load the overview")
}
