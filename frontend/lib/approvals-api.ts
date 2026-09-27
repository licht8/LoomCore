import { requestJson } from "@/lib/api-client"

export type PendingApproval = {
  plan_id: string
  session_id: string
  goal: string
  revision: number | null
  step_count: number
  risks: string[]
  created_at: string
}

/** Load plans awaiting an admin decision (read-only). */
export async function fetchApprovals(): Promise<PendingApproval[]> {
  const data = await requestJson<{ approvals: PendingApproval[] }>(
    "/api/approvals",
    {},
    "Failed to load approvals",
  )
  return data.approvals ?? []
}
