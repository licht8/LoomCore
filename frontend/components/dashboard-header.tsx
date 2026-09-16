import { StatusMenu } from "@/components/status-menu"

export function DashboardHeader() {
  return (
    <header className="absolute inset-x-0 top-0 z-20 flex h-[var(--header-h)] items-center justify-between bg-card/60 px-6 py-4 shadow-[inset_0_1px_0_0_oklch(1_0_0/10%)] backdrop-blur-xl after:absolute after:bottom-[-1px] after:left-0 after:right-0 after:h-px after:bg-border/50 after:content-['']">
      <div>
        <h1 className="text-xl font-bold tracking-tight text-primary text-balance">LoomCore Assistant</h1>
        <p className="text-sm text-muted-foreground">Conversational control for your AI infrastructure</p>
      </div>
      <div className="flex items-center gap-3">
        <div className="hidden items-center sm:flex">
          <StatusMenu />
        </div>
      </div>
    </header>
  )
}
