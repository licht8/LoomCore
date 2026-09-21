"use client"

import Link from "next/link"

import { useQuery } from "@tanstack/react-query"
import { AlertTriangle } from "lucide-react"

import { fetchLlmSettings } from "@/lib/llm-settings-api"

export function LlmSetupBanner() {
  const { data } = useQuery({
    queryKey: ["llm-settings"],
    queryFn: fetchLlmSettings,
    refetchInterval: 60_000,
    refetchIntervalInBackground: false,
  })

  if (!data || data.configured) return null

  return (
    <div className="absolute inset-x-0 top-[var(--header-h)] z-10 px-6 pt-3">
      <div
        role="status"
        className="mx-auto flex w-full max-w-3xl items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300"
      >
        <AlertTriangle className="size-4 shrink-0" />
        <span className="truncate">
          No LLM provider is configured yet. Chat replies with a setup hint until
          one is chosen.
        </span>
        <Link
          href="/settings"
          className="shrink-0 font-medium underline underline-offset-4 hover:opacity-80"
        >
          Open Settings
        </Link>
      </div>
    </div>
  )
}
