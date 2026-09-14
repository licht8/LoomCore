import type { SessionMessage } from "@/lib/chat-types"
import { authHeaders } from "@/lib/admin-token"

const SESSION_STORAGE_KEY = "loomcore_session_id"

/** Generate a random session ID. */
function generateSessionId(): string {
  return `sess_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`
}

/** Get or create the session ID (from localStorage). */
export function getSessionId(): string {
  if (typeof window === "undefined") return generateSessionId()
  let sid = localStorage.getItem(SESSION_STORAGE_KEY)
  if (!sid) {
    sid = generateSessionId()
    localStorage.setItem(SESSION_STORAGE_KEY, sid)
  }
  return sid
}

/** Base URL for REST calls (empty string means same-origin via nginx). */
export function getApiBase(): string {
  if (typeof window === "undefined") return ""
  const configured = process.env.NEXT_PUBLIC_API_URL
  if (configured) return configured.replace(/\/$/, "")
  const protocol = window.location.protocol === "https:" ? "https" : "http"
  // Next dev runs on :3000 while the backend listens on :8000.
  return window.location.port === "3000"
    ? `${protocol}://${window.location.hostname}:8000`
    : ""
}

function sessionUrl(sessionId: string, suffix = ""): string {
  return `${getApiBase()}/api/sessions/${encodeURIComponent(sessionId)}/messages${suffix}`
}

/** Load a session's stored history, or null if the request failed. */
export async function fetchSessionHistory(
  sessionId: string,
): Promise<SessionMessage[] | null> {
  try {
    const response = await fetch(sessionUrl(sessionId, "?limit=200"), {
      credentials: "include",
      headers: authHeaders(),
    })
    if (!response.ok) return null
    const data = await response.json()
    return Array.isArray(data.messages) ? (data.messages as SessionMessage[]) : null
  } catch (err) {
    console.error("Failed to load chat history:", err)
    return null
  }
}

/** Delete a session's stored history (resets the assistant's memory). */
export async function clearSessionHistory(sessionId: string): Promise<void> {
  try {
    await fetch(sessionUrl(sessionId), {
      method: "DELETE",
      credentials: "include",
      headers: authHeaders(),
    })
  } catch (err) {
    console.error("Failed to clear conversation:", err)
  }
}
