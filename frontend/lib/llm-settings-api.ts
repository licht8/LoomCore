import { authHeaders } from "@/lib/admin-token"
import { getApiBase } from "@/lib/chat-api"

export type ProviderPreset = {
  id: string
  label: string
  default_base_url: string
  default_model: string
}

export type ApiKeyStatus = {
  configured: boolean
  preview: string
}

export type LlmSettings = {
  provider: string
  base_url: string
  model: string
  summary_model: string
  enable_thinking: boolean
  thinking_template: boolean
  connect_timeout: number
  read_timeout: number
  write_timeout: number
  pool_timeout: number
  max_retries: number
  configured: boolean
  api_key: ApiKeyStatus
}

export type LlmSettingsUpdate = {
  provider?: string
  base_url?: string
  model?: string
  summary_model?: string
  api_key?: string | null
  enable_thinking?: boolean
  thinking_template?: boolean
  connect_timeout?: number
  read_timeout?: number
  write_timeout?: number
  pool_timeout?: number
  max_retries?: number
}

export type LlmTestResult = {
  ok: boolean
  status?: number
  models_count?: number
  error?: string
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

/** Load the provider presets offered by the backend. */
export async function fetchLlmProviders(): Promise<ProviderPreset[]> {
  const response = await fetch(`${getApiBase()}/api/llm/providers`, {
    credentials: "include",
    headers: authHeaders(),
  })
  if (!response.ok) {
    throw new Error(`Failed to load providers (${await readError(response)})`)
  }
  const data = await response.json()
  return (data.providers ?? []) as ProviderPreset[]
}

/** Load the active LLM configuration (the API key is never returned). */
export async function fetchLlmSettings(): Promise<LlmSettings> {
  const response = await fetch(`${getApiBase()}/api/llm/settings`, {
    credentials: "include",
    headers: authHeaders(),
  })
  if (!response.ok) {
    throw new Error(`Failed to load LLM settings (${await readError(response)})`)
  }
  return (await response.json()) as LlmSettings
}

/** Persist a new LLM configuration. */
export async function updateLlmSettings(
  update: LlmSettingsUpdate,
): Promise<LlmSettings> {
  const response = await fetch(`${getApiBase()}/api/llm/settings`, {
    method: "PUT",
    credentials: "include",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(update),
  })
  if (!response.ok) {
    throw new Error(`Failed to save LLM settings (${await readError(response)})`)
  }
  return (await response.json()) as LlmSettings
}

/** Probe the active provider's model endpoint. */
export async function testLlmConnection(): Promise<LlmTestResult> {
  const response = await fetch(`${getApiBase()}/api/llm/test`, {
    method: "POST",
    credentials: "include",
    headers: authHeaders(),
  })
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(
      (data?.detail ?? data?.error) ?? `Connection test failed (${await readError(response)})`,
    )
  }
  return data as LlmTestResult
}
