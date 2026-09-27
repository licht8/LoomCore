"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import {
  BarChart3,
  Inbox,
  LayoutDashboard,
  MessageSquare,
  Puzzle,
  ScrollText,
  Settings,
  Wrench,
} from "lucide-react"
import type { LucideIcon } from "lucide-react"
import { motion, useReducedMotion } from "motion/react"

import { cn } from "@/lib/utils"

const tapTransition = {
  type: "spring",
  stiffness: 400,
  damping: 25,
} as const

type NavItem = {
  label: string
  href: string
  icon: LucideIcon
}

const navItems: NavItem[] = [
  { label: "AI Assistant", href: "/", icon: MessageSquare },
  { label: "Overview", href: "/overview", icon: LayoutDashboard },
  { label: "Tools", href: "/tools", icon: Wrench },
  { label: "Approvals", href: "/approvals", icon: Inbox },
  { label: "Audit log", href: "/audit", icon: ScrollText },
  { label: "Plugins", href: "/plugins", icon: Puzzle },
  { label: "Usage", href: "/usage", icon: BarChart3 },
]

const settingsItem: NavItem = {
  label: "Settings",
  href: "/settings",
  icon: Settings,
}

function itemClass(active: boolean) {
  return cn(
    "group relative flex h-10 items-center gap-2 rounded-md px-3 text-sm transition-colors duration-150",
    active
      ? "font-medium text-foreground"
      : "font-normal text-foreground/70 hover:bg-foreground/[0.03] hover:text-foreground",
  )
}

function iconClass(active: boolean) {
  return cn(
    "size-5 transition-colors",
    active
      ? "text-foreground"
      : "text-muted-foreground/70 group-hover:text-muted-foreground",
  )
}

function NavLink({ item, active }: { item: NavItem; active: boolean }) {
  const Icon = item.icon
  const reduceMotion = useReducedMotion()
  return (
    <motion.div
      whileTap={reduceMotion ? undefined : { scale: 0.98 }}
      transition={tapTransition}
    >
      <Link
        href={item.href}
        aria-current={active ? "page" : undefined}
        className={itemClass(active)}
      >
        {active && (
          <span
            aria-hidden
            className="absolute top-1 bottom-1 left-0 w-0.5 rounded-full bg-foreground/60"
          />
        )}
        <Icon className={iconClass(active)} strokeWidth={1.75} />
        {item.label}
      </Link>
    </motion.div>
  )
}

export function DashboardSidebar() {
  const pathname = usePathname()
  const isActive = (href: string) => pathname === href

  return (
    <aside className="glass-sidebar flex w-64 shrink-0 flex-col rounded-2xl px-3 pb-5">
      <div className="flex h-[var(--header-h)] items-center px-3 text-lg font-semibold tracking-tight text-foreground">
        Loom Panel
      </div>
      <div className="mx-3 border-t border-border/50" />

      <nav className="flex flex-col gap-1.5 py-4">
        {navItems.map((item) => (
          <NavLink key={item.href} item={item} active={isActive(item.href)} />
        ))}
      </nav>

      <div className="mt-auto flex flex-col py-4">
        <div className="mx-3 border-t border-border/50" />
        <div className="flex flex-col gap-1.5 pt-4">
          <NavLink item={settingsItem} active={isActive(settingsItem.href)} />
        </div>
      </div>
    </aside>
  )
}
