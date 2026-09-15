import type { LucideIcon } from "lucide-react"

import { cn } from "@/lib/utils"

export function InfoRow({
  icon: Icon,
  label,
  value,
  className,
}: {
  icon: LucideIcon
  label: string
  value: string
  className?: string
}) {
  return (
    <div className={cn("flex items-center justify-between gap-3", className)}>
      <span className="flex items-center gap-2 text-xs text-muted-foreground">
        <Icon className="size-3.5" />
        {label}
      </span>
      <span
        className="max-w-[60%] truncate text-xs font-medium text-foreground"
        title={value}
      >
        {value}
      </span>
    </div>
  )
}
