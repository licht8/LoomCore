"use client"

import { FormEvent, useEffect, useState, type ReactNode } from "react"
import { getAdminToken, setAdminToken } from "@/lib/admin-token"

const STATUS_URL = "/api/plugins/status"

async function validateToken(token: string): Promise<boolean> {
  try {
    const response = await fetch(
      `${STATUS_URL}?access_token=${encodeURIComponent(token)}`,
      { credentials: "include" },
    )
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
    fetch("/api/auth/session", { credentials: "include" })
      .then((response) => setAuthenticated(response.ok))
      .catch(() => setAuthenticated(false))
  }, [])

  async function login(event: FormEvent) {
    event.preventDefault()
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ username, password, totp }),
    })
    if (!response.ok) {
      setError("Неверный логин, пароль или TOTP-код")
      return
    }
    setAuthenticated(true)
  }

  async function loginWithToken(event: FormEvent) {
    event.preventDefault()
    const value = token.trim()
    if (!value) {
      setError("Введите административный токен")
      return
    }
    if (!(await validateToken(value))) {
      setError("Неверный административный токен")
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
          <button
            className={`flex-1 rounded-md px-3 py-1.5 ${mode === "password" ? "bg-primary text-primary-foreground" : "text-muted-foreground"}`}
            onClick={() => { setMode("password"); setError("") }}
            type="button"
          >
            Логин и TOTP
          </button>
          <button
            className={`flex-1 rounded-md px-3 py-1.5 ${mode === "token" ? "bg-primary text-primary-foreground" : "text-muted-foreground"}`}
            onClick={() => { setMode("token"); setError("") }}
            type="button"
          >
            Токен
          </button>
        </div>

        {mode === "password" ? (
          <form onSubmit={login} className="space-y-4 rounded-2xl border border-border bg-card p-6 shadow-xl">
            <div>
              <h1 className="text-xl font-semibold">LoomHub Admin</h1>
              <p className="mt-1 text-sm text-muted-foreground">Войдите для управления сервером</p>
            </div>
            <input className="w-full rounded-lg border bg-background px-3 py-2" value={username} onChange={(event) => setUsername(event.target.value)} placeholder="Логин" autoComplete="username" />
            <input className="w-full rounded-lg border bg-background px-3 py-2" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Пароль" type="password" autoComplete="current-password" />
            <input className="w-full rounded-lg border bg-background px-3 py-2" value={totp} onChange={(event) => setTotp(event.target.value)} placeholder="Код из приложения-аутентификатора" inputMode="numeric" />
            {error && <p className="text-sm text-destructive">{error}</p>}
            <button className="w-full rounded-lg bg-primary px-3 py-2 text-primary-foreground" type="submit">Войти</button>
          </form>
        ) : (
          <form onSubmit={loginWithToken} className="space-y-4 rounded-2xl border border-border bg-card p-6 shadow-xl">
            <div>
              <h1 className="text-xl font-semibold">LoomHub Admin</h1>
              <p className="mt-1 text-sm text-muted-foreground">Введите административный токен</p>
            </div>
            <input className="w-full rounded-lg border bg-background px-3 py-2" value={token} onChange={(event) => setToken(event.target.value)} placeholder="ADMIN_TOKEN" type="password" autoComplete="off" />
            {error && <p className="text-sm text-destructive">{error}</p>}
            <button className="w-full rounded-lg bg-primary px-3 py-2 text-primary-foreground" type="submit">Войти</button>
          </form>
        )}
      </div>
    </main>
  )
}
