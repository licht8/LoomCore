import type { CSSProperties, ReactNode } from "react"

import { DashboardHeader } from "@/components/dashboard-header"
import { DashboardSidebar } from "@/components/dashboard-sidebar"
import { LlmSetupBanner } from "@/components/llm-setup-banner"

export function DashboardShell({ children }: { children: ReactNode }) {
  return (
    <div
      className="flex h-screen gap-4 overflow-hidden p-4"
      style={{ "--header-h": "81px", "--banner-h": "2rem" } as CSSProperties}
    >
      <DashboardSidebar />
      <main className="glass relative flex flex-1 flex-col overflow-hidden rounded-3xl">
        <DashboardHeader />
        <LlmSetupBanner />
        {children}
      </main>
    </div>
  )
}
