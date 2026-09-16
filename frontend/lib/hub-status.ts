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

/** Short-lived cache so several components mounting together share one snapshot. */
const SNAPSHOT_TTL_MS = 5_000
let snapshotCache: { at: number; value: HubSnapshot } | null = null
let snapshotInflight: Promise<HubSnapshot> | null = null

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

/**
 * Load the hub snapshot, sharing one network request across concurrent callers
 * and reusing a recent result. Both guards keep the UI from bursting the
 * rate-limited plugin/tool endpoints when several components mount at once.
 */
export function fetchHubSnapshot(): Promise<HubSnapshot> {
  if (snapshotCache && Date.now() - snapshotCache.at < SNAPSHOT_TTL_MS) {
    return Promise.resolve(snapshotCache.value)
  }
  if (snapshotInflight) return snapshotInflight

  snapshotInflight = loadHubSnapshot()
    .then((value) => {
      snapshotCache = { at: Date.now(), value }
      return value
    })
    .finally(() => {
      snapshotInflight = null
    })

  return snapshotInflight
}

async function loadHubSnapshot(): Promise<HubSnapshot> {
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
