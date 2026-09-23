import { requestJson } from "@/lib/api-client"

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
  planner_model: string
  enable_thinking: boolean
  thinking_template: boolean
  connect_timeout: number
  read_timeout: number
  write_timeout: number
  pool_timeout: number
  max_retries: number
  planner_max_tokens: number
  summary_max_tokens: number
  configured: boolean
  api_key: ApiKeyStatus
}

export type LlmSettingsUpdate = {
  provider?: string
  base_url?: string
  model?: string
  summary_model?: string
  planner_model?: string
  api_key?: string | null
  enable_thinking?: boolean
  thinking_template?: boolean
  connect_timeout?: number
  read_timeout?: number
  write_timeout?: number
  pool_timeout?: number
  max_retries?: number
  planner_max_tokens?: number
  summary_max_tokens?: number
}

export type LlmTestResult = {
  ok: boolean
  status?: number
  models_count?: number
  error?: string
}

/** Load the provider presets offered by the backend. */
export async function fetchLlmProviders(): Promise<ProviderPreset[]> {
  const data = await requestJson<{ providers?: ProviderPreset[] }>(
    "/api/llm/providers",
    {},
    "Failed to load providers",
  )
  return data.providers ?? []
}

/** Load the active LLM configuration (the API key is never returned). */
export function fetchLlmSettings(): Promise<LlmSettings> {
  return requestJson<LlmSettings>(
    "/api/llm/settings",
    {},
    "Failed to load LLM settings",
  )
}

/** Persist a new LLM configuration. */
export function updateLlmSettings(
  update: LlmSettingsUpdate,
): Promise<LlmSettings> {
  return requestJson<LlmSettings>(
    "/api/llm/settings",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(update),
    },
    "Failed to save LLM settings",
  )
}

/** Probe the active provider's model endpoint. */
export function testLlmConnection(): Promise<LlmTestResult> {
  return requestJson<LlmTestResult>(
    "/api/llm/test",
    { method: "POST" },
    "Connection test failed",
  )
}
