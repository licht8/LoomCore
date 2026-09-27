import { requestJson } from "@/lib/api-client"

export type AuditEntry = {
  id: string
  created_at: string
  actor: string
  session_id: string | null
  plan_id: string | null
  revision: number | null
  tool: string
  risk: string | null
  arguments: Record<string, unknown> | null
  status: string
  error: string | null
}

export type AuditPage = {
  entries: AuditEntry[]
  total: number
}

export type AuditVerify = {
  valid: boolean
  count: number
  head: string | null
}

/** Load a page of audit entries (newest first) plus the total count. */
export function fetchAudit(params: {
  limit: number
  offset: number
}): Promise<AuditPage> {
  return requestJson<AuditPage>(
    `/api/audit?limit=${params.limit}&offset=${params.offset}`,
    {},
    "Failed to load the audit log",
  )
}

/** Verify the whole hash chain (tamper-evidence). */
export function verifyAudit(): Promise<AuditVerify> {
  return requestJson<AuditVerify>(
    "/api/audit/verify",
    {},
    "Failed to verify the audit chain",
  )
}
