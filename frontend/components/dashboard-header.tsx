import { Activity, ShieldCheck } from "lucide-react"

export function DashboardHeader() {
  return (
    <header className="flex items-center justify-between border-b border-border bg-transparent px-6 py-4">
      <div>
        <h1 className="text-xl font-bold tracking-tight text-primary text-balance">LoomHub Assistant</h1>
        <p className="text-sm text-muted-foreground">Conversational control for your AI infrastructure</p>
      </div>
      <div className="flex items-center gap-3">
        <div className="hidden items-center gap-2 sm:flex">
          <div className="flex items-center gap-2 rounded-full border border-border bg-card/60 px-3 py-2">
            <Activity className="size-3.5 text-emerald-500" />
            <div>
              <p className="text-xs font-medium text-foreground">Self-hosted Hub</p>
              <p className="text-[11px] text-muted-foreground">Control plane online</p>
            </div>
          </div>
          <div className="flex items-center gap-2 rounded-full border border-border bg-card/60 px-3 py-2">
            <ShieldCheck className="size-3.5 text-primary" />
            <div>
              <p className="text-xs font-medium text-foreground">Admin session</p>
              <p className="text-[11px] text-muted-foreground">Protected access</p>
            </div>
          </div>
        </div>
      </div>
    </header>
  )
}
