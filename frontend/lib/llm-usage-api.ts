import { authHeaders } from "@/lib/admin-token"
import { getApiBase } from "@/lib/chat-api"
import { readError } from "@/lib/http-error"

export type LlmUsageTotals = {
  calls: number
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  avg_latency_ms: number | null
}

export type LlmUsageModelRow = {
  provider: string
  model: string
  calls: number
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  avg_latency_ms: number | null
}

export type LlmUsageDayRow = {
  day: string
  calls: number
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
}

export type LlmBudgetStatus = {
  day: string
  used: number
  limit: number
  remaining: number | null
}

export type LlmUsageSummary = {
  days: number
  since: string
  until: string
  totals: LlmUsageTotals
  by_model: LlmUsageModelRow[]
  by_day: LlmUsageDayRow[]
  budget: LlmBudgetStatus
}

function requestHeaders(init?: HeadersInit): Headers {
  const headers = new Headers(authHeaders())
  new Headers(init).forEach((value, key) => headers.set(key, value))
  return headers
}

async function requestJson<T>(
  path: string,
  init: RequestInit = {},
  errorPrefix = "Request failed",
): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${getApiBase()}${path}`, {
      ...init,
      credentials: "include",
      headers: requestHeaders(init.headers),
    })
  } catch (err) {
    const reason = err instanceof Error ? err.message : "network error"
    throw new Error(`${errorPrefix} (${reason})`)
  }
  if (!response.ok) {
    throw new Error(`${errorPrefix} (${await readError(response)})`)
  }
  const text = await response.text()
  if (!text) {
    throw new Error(`${errorPrefix} (empty response)`)
  }
  try {
    return JSON.parse(text) as T
  } catch {
    throw new Error(`${errorPrefix} (invalid JSON response)`)
  }
}

/** Load aggregated LLM usage for the last ``days`` days. */
export function fetchLlmUsage(days: number): Promise<LlmUsageSummary> {
  return requestJson<LlmUsageSummary>(
    `/api/llm/usage?days=${days}`,
    {},
    "Failed to load LLM usage",
  )
}
