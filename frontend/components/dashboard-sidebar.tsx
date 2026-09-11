import { Activity, MessageSquareText, Sparkles } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

type NavItem = {
  label: string
  icon: "assistant" | "status"
  active?: boolean
}

const navItems: NavItem[] = [
  { label: "AI Assistant", icon: "assistant", active: true },
  { label: "System Status", icon: "status" },
]

const icons = {
  assistant: MessageSquareText,
  status: Activity,
}

export function DashboardSidebar() {
  return (
    <aside className="glass flex w-64 shrink-0 flex-col rounded-3xl bg-sidebar text-sidebar-foreground">
      <div className="flex items-center gap-3 px-6 py-6">
        <div className="flex size-9 items-center justify-center rounded-xl bg-sidebar-accent text-sidebar-accent-foreground">
          <Sparkles className="size-5" />
        </div>
        <div className="leading-tight">
          <p className="text-sm font-bold tracking-tight">LoomHub</p>
          <p className="text-xs text-sidebar-foreground/60">AI Infrastructure</p>
        </div>
      </div>

      <nav className="flex flex-col gap-1.5 px-3 py-2">
        {navItems.map((item) => {
          const Icon = icons[item.icon]
          return (
            <button
              key={item.label}
              className={cn(
                "group relative flex items-center gap-3 overflow-hidden rounded-xl px-3 py-2.5 text-sm font-medium transition-all duration-300",
                item.active
                  ? "bg-gradient-to-r from-sidebar-accent to-sidebar-accent/40 text-sidebar-accent-foreground shadow-lg shadow-sidebar-accent/20"
                  : "text-sidebar-foreground/70 hover:bg-white/5 hover:text-sidebar-foreground",
              )}
              aria-current={item.active ? "page" : undefined}
            >
              <span
                className={cn(
                  "absolute left-0 top-1/2 h-5 -translate-y-1/2 rounded-r-full bg-sidebar-accent-foreground transition-all duration-300",
                  item.active ? "w-1 opacity-100" : "w-0 opacity-0",
                )}
              />
              <span
                className={cn(
                  "flex size-8 items-center justify-center rounded-lg transition-all duration-300",
                  item.active
                    ? "bg-sidebar-accent-foreground/15 text-sidebar-accent-foreground"
                    : "bg-white/5 text-sidebar-foreground/60 group-hover:bg-white/10 group-hover:text-sidebar-foreground",
                )}
              >
                <Icon className="size-4" />
              </span>
              {item.label}
            </button>
          )
        })}
      </nav>

      <div className="mt-auto px-4 pb-6">
        <div className="rounded-2xl bg-white/5 p-4">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-sidebar-foreground/70">Plugin LoomWG</span>
            <Badge className="gap-1.5 border-transparent bg-emerald-400/15 text-emerald-300 hover:bg-emerald-400/15">
              <span className="size-1.5 rounded-full bg-emerald-400" />
              Active
            </Badge>
          </div>
          <p className="mt-2 text-xs leading-relaxed text-sidebar-foreground/50">
            Workload gateway connected and streaming telemetry.
          </p>
        </div>
      </div>
    </aside>
  )
}
