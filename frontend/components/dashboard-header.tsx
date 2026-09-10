import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"

export function DashboardHeader() {
  return (
    <header className="flex items-center justify-between border-b border-border bg-transparent px-6 py-4">
      <div>
        <h1 className="text-xl font-bold tracking-tight text-primary text-balance">LoomHub Assistant</h1>
        <p className="text-sm text-muted-foreground">Conversational control for your AI infrastructure</p>
      </div>
      <div className="flex items-center gap-3">
        <div className="hidden text-right sm:block">
          <p className="text-sm font-medium text-foreground">Ada Okafor</p>
          <p className="text-xs text-muted-foreground">Platform Admin</p>
        </div>
        <Avatar className="size-10 ring-2 ring-accent">
          <AvatarImage src="/admin-avatar.png" alt="Ada Okafor" />
          <AvatarFallback className="bg-secondary text-secondary-foreground">AO</AvatarFallback>
        </Avatar>
      </div>
    </header>
  )
}
