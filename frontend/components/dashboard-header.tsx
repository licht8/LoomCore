import { AdminSessionButton } from "@/components/admin-session-button"
import { HubStatusButton } from "@/components/hub-status-button"

export function DashboardHeader() {
  return (
    <header className="absolute inset-x-0 top-0 z-20 flex items-center justify-between border-b border-border bg-card/60 px-6 py-4 shadow-[inset_0_1px_0_0_oklch(1_0_0/10%)] backdrop-blur-xl">
      <div>
        <h1 className="text-xl font-bold tracking-tight text-primary text-balance">LoomCore Assistant</h1>
        <p className="text-sm text-muted-foreground">Conversational control for your AI infrastructure</p>
      </div>
      <div className="flex items-center gap-3">
        <div className="hidden items-center gap-2 sm:flex">
          <HubStatusButton />
          <AdminSessionButton />
        </div>
      </div>
    </header>
  )
}
