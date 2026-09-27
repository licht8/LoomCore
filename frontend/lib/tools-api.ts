import { requestJson } from "@/lib/api-client"

export type ToolInfo = {
  name: string
  description?: string
  inputSchema?: {
    type?: string
    properties?: Record<string, { type?: string; description?: string }>
    required?: string[]
  }
  risk?: string
  report?: boolean
  internal?: boolean
}

/** Load the full tool catalog (core + plugin schemas). */
export async function fetchTools(): Promise<ToolInfo[]> {
  const data = await requestJson<{ tools: ToolInfo[] }>(
    "/api/tools",
    {},
    "Failed to load tools",
  )
  return data.tools ?? []
}
