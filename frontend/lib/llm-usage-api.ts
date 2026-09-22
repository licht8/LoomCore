import { requestJson } from "@/lib/api-client"

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

/** Load aggregated LLM usage for the last ``days`` days. */
export function fetchLlmUsage(days: number): Promise<LlmUsageSummary> {
  return requestJson<LlmUsageSummary>(
    `/api/llm/usage?days=${days}`,
    {},
    "Failed to load LLM usage",
  )
}
