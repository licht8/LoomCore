"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { RotateCw } from "lucide-react"
import { motion, useReducedMotion } from "motion/react"
import type { Transition, Variants } from "motion/react"

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { getAdminToken, setAdminToken } from "@/lib/admin-token"
import {
  fetchAdminSession,
  fetchHealth,
  fetchHubSnapshot,
  signOut,
  type HubSnapshot,
} from "@/lib/hub-status"
import { cn } from "@/lib/utils"

const labelClass =
  "font-mono text-[10px] tracking-wider text-muted-foreground uppercase"

const tapTransition: Transition = {
  type: "spring",
  stiffness: 400,
  damping: 25,
}

const panelVariants: Variants = {
  open: {
    opacity: 1,
    scale: 1,
    backdropFilter: "blur(20px) saturate(180%)",
    pointerEvents: "auto",
    transition: { type: "spring", stiffness: 300, damping: 30, mass: 0.8 },
  },
  closed: {
    opacity: 0,
    scale: 0.96,
    backdropFilter: "blur(0px) saturate(180%)",
    pointerEvents: "none",
    transition: { type: "spring", stiffness: 300, damping: 35, mass: 0.8 },
  },
}

const fadeVariants: Variants = {
  open: {
    opacity: 1,
    pointerEvents: "auto",
    transition: { duration: 0.18, ease: "easeOut" },
  },
  closed: {
    opacity: 0,
    pointerEvents: "none",
    transition: { duration: 0.15, ease: "easeIn" },
  },
}

export function StatusMenu() {
  const reduceMotion = useReducedMotion()
  const actionsRef = useRef<{
    unmount: () => void
    close: () => void
  } | null>(null)
  const hasOpened = useRef(false)
  const [open, setOpen] = useState(false)
  const [online, setOnline] = useState<boolean | null>(null)
  const [origin, setOrigin] = useState("")
  const [snapshot, setSnapshot] = useState<HubSnapshot | null>(null)
  const [hasToken, setHasToken] = useState(false)
  const [session, setSession] = useState<boolean | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    setOrigin(window.location.origin)
    let active = true
    const check = () => {
      fetchHealth().then((ok) => {
        if (active) setOnline(ok)
      })
    }
    check()
    const timer = setInterval(check, 20000)
    return () => {
      active = false
      clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    if (open) {
      hasOpened.current = true
      return
    }
    if (!hasOpened.current) return
    const timer = window.setTimeout(() => actionsRef.current?.unmount(), 600)
    return () => window.clearTimeout(timer)
  }, [open])

  const refresh = useCallback(async () => {
    setLoading(true)
    setHasToken(Boolean(getAdminToken()))
    const [sessionOk, snapshotData] = await Promise.all([
      fetchAdminSession(),
      fetchHubSnapshot().catch(() => null),
    ])
    setSession(sessionOk)
    if (snapshotData) setSnapshot(snapshotData)
    setLoading(false)
  }, [])

  useEffect(() => {
    if (open) refresh()
  }, [open, refresh])

  const plugins = snapshot?.plugins ?? []
  const statusLabel =
    online === null ? "CHECKING" : online ? "ONLINE" : "OFFLINE"
  const method = hasToken
    ? "static token"
    : session
      ? "password + totp"
      : "unknown"

  async function handleSignOut() {
    setLoading(true)
    await signOut()
    setAdminToken("")
    window.location.reload()
  }

  return (
    <Popover
      open={open}
      actionsRef={actionsRef}
      onOpenChange={(next, details) => {
        if (!next) details.preventUnmountOnClose()
        setOpen(next)
      }}
    >
      <PopoverTrigger
        className="group inline-flex items-center gap-1.5 rounded-md px-2 py-1.5 font-mono text-xs text-muted-foreground outline-none transition-colors hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-ring/50"
        render={
          <motion.button
            whileTap={reduceMotion ? undefined : { scale: 0.97 }}
            transition={tapTransition}
          />
        }
      >
        <span
          className={cn(
            "size-1.5 rounded-full",
            online ? "bg-emerald-500" : "bg-muted-foreground/40",
          )}
        />
        self-hosted
      </PopoverTrigger>

      <PopoverContent
        className="liquid-glass w-[280px] p-3"
        render={
          <motion.div
            variants={reduceMotion ? fadeVariants : panelVariants}
            initial="closed"
            animate={open ? "open" : "closed"}
            onAnimationComplete={(definition) => {
              if (definition === "closed") actionsRef.current?.unmount()
            }}
          />
        }
      >
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm font-semibold text-foreground">self-hosted hub</p>
          <span className="flex items-center gap-1.5">
            <span
              className={cn(
                "size-1.5 rounded-full",
                online ? "bg-emerald-500" : "bg-muted-foreground/40",
              )}
            />
            <span className={labelClass}>{statusLabel}</span>
          </span>
        </div>

        <div className="mt-3">
          <p className={labelClass}>Endpoint</p>
          <p
            className="mt-1 truncate font-mono text-xs text-foreground"
            title={origin}
          >
            {origin || "—"}
          </p>
        </div>

        <p className="mt-3 text-xs text-muted-foreground">
          {snapshot
            ? `${plugins.length} plugins · ${snapshot.toolCount} tools`
            : "…"}
        </p>

        <div className="mt-3 border-t border-border pt-3">
          <p className={labelClass}>Admin</p>
          <p className="mt-1 text-xs text-foreground">{method} · full scope</p>
        </div>

        <div className="mt-3 flex items-center gap-4 border-t border-border pt-3">
          <button
            type="button"
            onClick={refresh}
            disabled={loading}
            className="inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
          >
            <RotateCw className={cn("size-3", loading && "animate-spin")} />
            Refresh
          </button>
          <button
            type="button"
            onClick={handleSignOut}
            disabled={loading}
            className="text-xs text-destructive transition-colors hover:text-destructive/80 disabled:opacity-50"
          >
            Sign out
          </button>
        </div>
      </PopoverContent>
    </Popover>
  )
}
