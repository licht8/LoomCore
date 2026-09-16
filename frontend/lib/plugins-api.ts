import { authHeaders } from "@/lib/admin-token"
import { getApiBase } from "@/lib/chat-api"

export type PluginAction = "enable" | "disable" | "reload"

export type PluginInfo = {
  name: string
  state: string
  pid: number | null
  restart_attempt: number
  is_healthy: boolean
  enabled: boolean
  version: string
  description: string
  tools: number
  permission_warnings: string[]
}

async function readError(response: Response): Promise<string> {
  try {
    const data = await response.json()
    const message = data?.detail ?? data?.error
    if (typeof message === "string" && message) return message
  } catch {
    // response had no JSON body
  }
  return `${response.status} ${response.statusText}`.trim()
}

/** Load every discovered plugin with its lifecycle metadata. */
export async function fetchPlugins(): Promise<PluginInfo[]> {
  const response = await fetch(`${getApiBase()}/api/plugins/status`, {
    credentials: "include",
    headers: authHeaders(),
  })
  if (!response.ok) {
    throw new Error(`Failed to load plugins (${await readError(response)})`)
  }
  const data = await response.json()
  const plugins = Object.values(data.plugins ?? {}) as PluginInfo[]
  return plugins.sort((a, b) => a.name.localeCompare(b.name))
}

async function mutatePlugin(id: string, action: string): Promise<void> {
  const response = await fetch(
    `${getApiBase()}/api/plugins/${encodeURIComponent(id)}/${action}`,
    {
      method: "POST",
      credentials: "include",
      headers: authHeaders(),
    },
  )
  if (!response.ok) {
    throw new Error(`Could not ${action} ${id} (${await readError(response)})`)
  }
}

/** Enable a disabled plugin. */
export function enablePlugin(id: string): Promise<void> {
  return mutatePlugin(id, "enable")
}

/** Disable a running plugin. */
export function disablePlugin(id: string): Promise<void> {
  return mutatePlugin(id, "disable")
}

/** Restart a running plugin. */
export function reloadPlugin(id: string): Promise<void> {
  return mutatePlugin(id, "reload")
}
