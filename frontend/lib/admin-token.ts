const TOKEN_KEY = "loomcore_admin_token"

/** Get the stored admin bearer token. */
export function getAdminToken(): string {
  if (typeof window === "undefined") return ""
  return localStorage.getItem(TOKEN_KEY) || ""
}

/** Save or clear the admin bearer token. */
export function setAdminToken(token: string): void {
  if (typeof window === "undefined") return
  const value = token.trim()
  if (value) localStorage.setItem(TOKEN_KEY, value)
  else localStorage.removeItem(TOKEN_KEY)
}

/** Append access_token to the URL if a token is given or stored. */
export function withAdminToken(url: string, token = getAdminToken()): string {
  if (!token) return url
  const separator = url.includes("?") ? "&" : "?"
  return `${url}${separator}access_token=${encodeURIComponent(token)}`
}
