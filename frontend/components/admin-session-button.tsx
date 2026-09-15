"use client"

import { useCallback, useEffect, useState } from "react"
import { ChevronDown, KeyRound, LogOut, RefreshCw, ShieldCheck } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { headerPillClass } from "@/components/header-pill"
import { InfoRow } from "@/components/info-row"
import {
  Popover,
  PopoverArrow,
  PopoverContent,
  PopoverDescription,
  PopoverTitle,
  PopoverTrigger,
} from "@/components/ui/popover"
import { getAdminToken, setAdminToken } from "@/lib/admin-token"
import { fetchAdminSession, signOut } from "@/lib/hub-status"
import { cn } from "@/lib/utils"

export function AdminSessionButton() {
  const [open, setOpen] = useState(false)
  const [hasToken, setHasToken] = useState(false)
  const [session, setSession] = useState<boolean | null>(null)
  const [loading, setLoading] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    setHasToken(Boolean(getAdminToken()))
    setSession(await fetchAdminSession())
    setLoading(false)
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  useEffect(() => {
    if (open) refresh()
  }, [open, refresh])

  const authenticated = hasToken || session === true
  const method = hasToken
    ? "Static admin token"
    : session
      ? "Password + TOTP session"
      : "Unknown"

  async function handleSignOut() {
    setLoading(true)
    await signOut()
    setAdminToken("")
    window.location.reload()
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger className={headerPillClass}>
        <ShieldCheck className="size-3.5 text-primary" />
        <div>
          <p className="text-xs font-medium text-foreground">Admin session</p>
          <p className="text-[11px] text-muted-foreground">
            {authenticated ? "Protected access" : "Not authenticated"}
          </p>
        </div>
        <ChevronDown className="size-3.5 text-muted-foreground transition-transform duration-300 group-data-[popup-open]:rotate-180" />
      </PopoverTrigger>

      <PopoverContent className="w-80">
        <PopoverArrow />
        <div className="flex items-start justify-between gap-3">
          <div>
            <PopoverTitle>Admin session</PopoverTitle>
            <PopoverDescription>Your current access to this hub</PopoverDescription>
          </div>
          <Badge
            className={cn(
              "gap-1.5 border-transparent",
              authenticated
                ? "bg-emerald-400/15 text-emerald-500"
                : "bg-destructive/10 text-destructive",
            )}
          >
            <span
              className={cn(
                "size-1.5 rounded-full",
                authenticated ? "bg-emerald-400" : "bg-destructive",
              )}
            />
            {authenticated ? "Active" : "Locked"}
          </Badge>
        </div>

        <div className="mt-4 space-y-2">
          <InfoRow icon={KeyRound} label="Method" value={method} />
          <InfoRow
            icon={ShieldCheck}
            label="Scope"
            value="Full admin · plugin lifecycle & approvals"
          />
        </div>

        <div className="mt-4 flex items-center justify-between gap-2 border-t border-border pt-3">
          <Button
            variant="ghost"
            size="sm"
            onClick={refresh}
            disabled={loading}
            className="text-muted-foreground"
          >
            <RefreshCw className={cn("size-3.5", loading && "animate-spin")} />
            Refresh
          </Button>
          <Button
            variant="destructive"
            size="sm"
            onClick={handleSignOut}
            disabled={loading}
          >
            <LogOut className="size-3.5" />
            Sign out
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  )
}
