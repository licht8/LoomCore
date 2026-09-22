import { authHeaders } from "@/lib/admin-token"
import { getApiBase } from "@/lib/chat-api"
import { readError } from "@/lib/http-error"

export function requestHeaders(init?: HeadersInit): Headers {
  const headers = new Headers(authHeaders())
  new Headers(init).forEach((value, key) => headers.set(key, value))
  return headers
}

export async function requestJson<T>(
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
