import type { ReactNode } from "react"

import { AdminGate } from "@/components/admin-gate"
import { DashboardShell } from "@/components/dashboard-shell"

/**
 * Shared shell for every dashboard route. Mounting AdminGate and DashboardShell
 * once here (instead of per page) keeps the verified auth state and the WebSocket
 * alive across tab switches; only the page content below swaps.
 */
export default function DashboardLayout({ children }: { children: ReactNode }) {
  return (
    <AdminGate>
      <DashboardShell>{children}</DashboardShell>
    </AdminGate>
  )
}
