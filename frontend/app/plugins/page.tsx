import { AdminGate } from "@/components/admin-gate"
import { DashboardShell } from "@/components/dashboard-shell"
import { PluginsView } from "@/components/plugins-view"

export default function PluginsPage() {
  return (
    <AdminGate>
      <DashboardShell>
        <PluginsView />
      </DashboardShell>
    </AdminGate>
  )
}
