import { authHeaders } from "@/lib/admin-token"
import { getApiBase } from "@/lib/chat-api"

export type PluginStatus = {
  name: string
  state: string
  pid: number | null
  restart_attempt: number
  is_healthy: boolean
}

export type HubSnapshot = {
  plugins: PluginStatus[]
  toolCount: number
}

export async function fetchHealth(): Promise<boolean> {
  try {
    const response = await fetch(`${getApiBase()}/health`, {
      credentials: "include",
    })
    return response.ok
  } catch {
    return false
  }
}

export async function fetchHubSnapshot(): Promise<HubSnapshot> {
  const [pluginsResponse, toolsResponse] = await Promise.all([
    fetch(`${getApiBase()}/api/plugins/status`, {
      credentials: "include",
      headers: authHeaders(),
    }),
    fetch(`${getApiBase()}/api/tools`, {
      credentials: "include",
      headers: authHeaders(),
    }),
  ])

  if (!pluginsResponse.ok) {
    throw new Error(`Plugin status request failed (${pluginsResponse.status})`)
  }

  const pluginsData = await pluginsResponse.json()
  const toolsData = toolsResponse.ok ? await toolsResponse.json() : {}
  const plugins = Object.values(pluginsData.plugins ?? {}) as PluginStatus[]

  return {
    plugins,
    toolCount: Array.isArray(toolsData.tools) ? toolsData.tools.length : 0,
  }
}

export async function fetchAdminSession(): Promise<boolean> {
  try {
    const response = await fetch(`${getApiBase()}/api/auth/session`, {
      credentials: "include",
      headers: authHeaders(),
    })
    return response.ok
  } catch {
    return false
  }
}

export async function signOut(): Promise<void> {
  try {
    await fetch(`${getApiBase()}/api/auth/logout`, {
      method: "POST",
      credentials: "include",
      headers: authHeaders(),
    })
  } catch {}
}
