"use client"

import { FormEvent, useEffect, useState, type ReactNode } from "react"
import { authHeaders, getAdminToken, setAdminToken } from "@/lib/admin-token"
import { getApiBase } from "@/lib/chat-api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

async function validateToken(token: string): Promise<boolean> {
  try {
    const response = await fetch(`${getApiBase()}/api/plugins/status`, {
      credentials: "include",
      headers: authHeaders(token),
    })
    return response.ok
  } catch {
    return false
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
    const savedToken = getAdminToken()
    if (savedToken) {
      validateToken(savedToken).then((ok) => {
        if (!ok) setAdminToken("")
        setAuthenticated(ok)
      })
      return
    }
    fetch(`${getApiBase()}/api/auth/session`, { credentials: "include" })
      .then((response) => setAuthenticated(response.ok))
      .catch(() => setAuthenticated(false))
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
    setAuthenticated(true)
  }

  async function loginWithToken(event: FormEvent) {
    event.preventDefault()
    const value = token.trim()
    if (!value) {
      setError("Enter the admin token")
      return
    }
    if (!(await validateToken(value))) {
      setError("Invalid admin token")
      return
    }
    setAdminToken(value)
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
