import { AdminGate } from "@/components/admin-gate"
import { ChatRoom } from "@/components/chat-room"
import { DashboardShell } from "@/components/dashboard-shell"

export default function Page() {
  return (
    <AdminGate>
      <DashboardShell>
        <ChatRoom />
      </DashboardShell>
    </AdminGate>
  )
}
