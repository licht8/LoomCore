const TOKEN_KEY = "loomcore_admin_token"

/**
 * Send the static admin token as an Authorization header rather than in the
 * URL: a token in the query string leaks through access logs, browser history
 * and Referer headers.
 */
const TOKEN_SUBPROTOCOL_PREFIX = "loomcore.auth."

/**
 * Static token storage is per-tab (sessionStorage), never localStorage: a
 * long-lived admin token must not survive the browser session or be readable
 * from disk. Password + TOTP (the httpOnly session cookie) is the preferred
 * login path; the static token is for API/automation use.
 */
function storage(): Storage | null {
  if (typeof window === "undefined") return null
  return window.sessionStorage
}

/** Get the stored admin bearer token. */
export function getAdminToken(): string {
  return storage()?.getItem(TOKEN_KEY) || ""
}

/** Save or clear the admin bearer token. */
export function setAdminToken(token: string): void {
  const store = storage()
  if (!store) return
  const value = token.trim()
  if (value) store.setItem(TOKEN_KEY, value)
  else store.removeItem(TOKEN_KEY)
}

/** Authorization headers for REST calls (empty when no static token is set). */
export function authHeaders(token = getAdminToken()): Record<string, string> {
  if (!token) return {}
  return { Authorization: `Bearer ${token}` }
}

/** Base64url-encode a token for use as a WebSocket subprotocol value. */
function toBase64Url(value: string): string {
  const bytes = new TextEncoder().encode(value)
  let binary = ""
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte)
  })
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "")
}

/**
 * WebSocket subprotocols carrying the static token. Browsers cannot set an
 * Authorization header on a handshake, so the token travels as a subprotocol
 * instead of the URL.
 */
export function wsProtocols(token = getAdminToken()): string[] {
  if (!token) return []
  return [`${TOKEN_SUBPROTOCOL_PREFIX}${toBase64Url(token)}`]
}
