"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"

import { AlertTriangle } from "lucide-react"

import { useLlmSettings } from "@/lib/use-llm-settings"

export function LlmSetupBanner() {
  const { data } = useLlmSettings()
  const pathname = usePathname()

  if (!data || data.configured || pathname.startsWith("/settings"))
    return null

  return (
    <>
      <div aria-hidden className="h-[var(--banner-h)] shrink-0" />
      <div className="absolute inset-x-0 top-[var(--header-h)] z-10 px-6 pt-3">
        <div
          role="status"
          className="mx-auto flex w-full max-w-3xl items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300"
        >
          <AlertTriangle className="size-4 shrink-0" />
          <span className="truncate">
            No LLM provider is configured yet. Chat replies with a setup hint
            until one is chosen.
          </span>
          <Link
            href="/settings"
            className="shrink-0 font-medium underline underline-offset-4 hover:opacity-80"
          >
            Open Settings
          </Link>
        </div>
      </div>
    </>
  )
}
