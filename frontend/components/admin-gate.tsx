"use client"

import { FormEvent, useEffect, useState, type ReactNode } from "react"
import { authHeaders, getAdminToken, setAdminToken } from "@/lib/admin-token"
import { getApiBase } from "@/lib/chat-api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

type TokenCheck = "ok" | "invalid" | "error"

/**
 * How long a verified session is trusted without re-checking. Tab switches no
 * longer remount the gate (it lives in the shared dashboard layout), but this
 * also absorbs React StrictMode's double-invocation in development.
 */
const VERIFIED_TTL_MS = 30_000

let lastVerifiedAt = 0

/**
 * Validate the stored static token against the session endpoint, which accepts
 * the Bearer header and is not rate-limited (the plugin status endpoint is).
 *
 * A transient failure (server error, network hiccup) must never be mistaken for
 * a bad credential — only 401/403 mean the token is truly rejected.
 */
async function checkToken(token: string): Promise<TokenCheck> {
  try {
    const response = await fetch(`${getApiBase()}/api/auth/session`, {
      credentials: "include",
      headers: authHeaders(token),
    })
    if (response.ok) return "ok"
    if (response.status === 401 || response.status === 403) return "invalid"
    return "error"
  } catch {
    return "error"
  }
}

/**
 * Check the httpOnly session cookie. Only an explicit 401/403 means the session
 * is gone; a 429/5xx/network error is transient and must not log the user out.
 */
async function checkSessionCookie(signal: AbortSignal): Promise<TokenCheck> {
  try {
    const response = await fetch(`${getApiBase()}/api/auth/session`, {
      credentials: "include",
      signal,
    })
    if (response.ok) return "ok"
    if (response.status === 401 || response.status === 403) return "invalid"
    return "error"
  } catch {
    return "error"
  }
}

export function AdminGate({ children }: { children: ReactNode }) {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null)
  const [mode, setMode] = useState<"password" | "token">("password")
  const [username, setUsername] = useState("admin")
  const [password, setPassword] = useState("")
  const [totp, setTotp] = useState("")
  const [token, setToken] = useState("")
  const [error, setError] = useState("")

  useEffect(() => {
    const controller = new AbortController()
    const { signal } = controller

    const settle = (result: TokenCheck) => {
      if (signal.aborted) return
      if (result === "ok") {
        lastVerifiedAt = Date.now()
        setAuthenticated(true)
        return
      }
      if (result === "invalid") {
        lastVerifiedAt = 0
        setAuthenticated(false)
        return
      }
      // Transient failure: trust a recent verification, else fail closed.
      setAuthenticated(Date.now() - lastVerifiedAt < VERIFIED_TTL_MS)
    }

    const savedToken = getAdminToken()
    if (savedToken) {
      checkToken(savedToken).then((result) => {
        if (signal.aborted) return
        if (result === "invalid") setAdminToken("")
        settle(result)
      })
      return () => controller.abort()
    }

    checkSessionCookie(signal).then(settle)
    return () => controller.abort()
  }, [])

  async function login(event: FormEvent) {
    event.preventDefault()
    const response = await fetch(`${getApiBase()}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ username, password, totp }),
    })
    if (!response.ok) {
      setError("Invalid username, password, or TOTP code")
      return
    }
    lastVerifiedAt = Date.now()
    setAuthenticated(true)
  }

  async function loginWithToken(event: FormEvent) {
    event.preventDefault()
    const value = token.trim()
    if (!value) {
      setError("Enter the admin token")
      return
    }
    if ((await checkToken(value)) !== "ok") {
      setError("Invalid admin token")
      return
    }
    setAdminToken(value)
    lastVerifiedAt = Date.now()
    setAuthenticated(true)
  }

  if (authenticated === true) return <>{children}</>
  if (authenticated === null) return null

  return (
    <main className="flex min-h-screen items-center justify-center bg-background p-6">
      <div className="w-full max-w-sm space-y-4">
        <div className="flex rounded-lg border border-border p-1 text-sm">
          <Button
            variant={mode === "password" ? "default" : "ghost"}
            className="flex-1"
            onClick={() => { setMode("password"); setError("") }}
            type="button"
          >
            Login and TOTP
          </Button>
          <Button
            variant={mode === "token" ? "default" : "ghost"}
            className="flex-1"
            onClick={() => { setMode("token"); setError("") }}
            type="button"
          >
            Token
          </Button>
        </div>

        {mode === "password" ? (
          <form onSubmit={login} className="space-y-4 rounded-2xl border border-border bg-card p-6 shadow-xl">
            <div>
              <h1 className="text-xl font-semibold">LoomCore Admin</h1>
              <p className="mt-1 text-sm text-muted-foreground">Sign in to manage the server</p>
            </div>
            <Input value={username} onChange={(event) => setUsername(event.target.value)} placeholder="Username" autoComplete="username" />
            <Input value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Password" type="password" autoComplete="current-password" />
            <Input value={totp} onChange={(event) => setTotp(event.target.value)} placeholder="Authenticator app code" inputMode="numeric" />
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button className="w-full" type="submit">Sign in</Button>
          </form>
        ) : (
          <form onSubmit={loginWithToken} className="space-y-4 rounded-2xl border border-border bg-card p-6 shadow-xl">
            <div>
              <h1 className="text-xl font-semibold">LoomCore Admin</h1>
              <p className="mt-1 text-sm text-muted-foreground">Enter the admin token</p>
            </div>
            <Input value={token} onChange={(event) => setToken(event.target.value)} placeholder="ADMIN_TOKEN" type="password" autoComplete="off" />
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button className="w-full" type="submit">Sign in</Button>
          </form>
        )}
      </div>
    </main>
  )
}
