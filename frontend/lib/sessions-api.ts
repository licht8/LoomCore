import { requestJson } from "@/lib/api-client"
import type { SessionSummary } from "@/lib/chat-types"

export type { SessionSummary }

/** Load chat sessions with title/preview/count (newest first). */
export async function fetchSessions(limit = 100): Promise<SessionSummary[]> {
  const data = await requestJson<{ sessions: SessionSummary[] }>(
    `/api/sessions?limit=${limit}`,
    {},
    "Failed to load sessions",
  )
  return data.sessions ?? []
}
