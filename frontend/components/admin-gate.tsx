"use client"

import { FormEvent, useEffect, useState, type ReactNode } from "react"

export function AdminGate({ children }: { children: ReactNode }) {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null)
  const [username, setUsername] = useState("admin")
  const [password, setPassword] = useState("")
  const [totp, setTotp] = useState("")
  const [error, setError] = useState("")

  useEffect(() => {
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

  if (authenticated === true) return <>{children}</>
  if (authenticated === null) return null

  return (
    <main className="flex min-h-screen items-center justify-center bg-background p-6">
      <form onSubmit={login} className="w-full max-w-sm space-y-4 rounded-2xl border border-border bg-card p-6 shadow-xl">
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
    </main>
  )
}