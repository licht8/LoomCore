const TOKEN_KEY = "loomhub_admin_token"

/** Получить сохранённый административный bearer-токен. */
export function getAdminToken(): string {
  if (typeof window === "undefined") return ""
  return localStorage.getItem(TOKEN_KEY) || ""
}

/** Сохранить или очистить административный bearer-токен. */
export function setAdminToken(token: string): void {
  if (typeof window === "undefined") return
  const value = token.trim()
  if (value) localStorage.setItem(TOKEN_KEY, value)
  else localStorage.removeItem(TOKEN_KEY)
}

/** Добавить access_token к URL, если токен сохранён. */
export function withAdminToken(url: string): string {
  const token = getAdminToken()
  if (!token) return url
  const separator = url.includes("?") ? "&" : "?"
  return `${url}${separator}access_token=${encodeURIComponent(token)}`
}
