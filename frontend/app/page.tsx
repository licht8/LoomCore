import { DashboardSidebar } from "@/components/dashboard-sidebar"
import { DashboardHeader } from "@/components/dashboard-header"
import { ChatRoom } from "@/components/chat-room"
import { AdminGate } from "@/components/admin-gate"

export default function Page() {
  return (
    <AdminGate>
      <div className="flex h-screen gap-4 overflow-hidden p-4">
        <DashboardSidebar />
        <main className="glass relative flex flex-1 flex-col overflow-hidden rounded-3xl">
          <DashboardHeader />
          <ChatRoom />
        </main>
      </div>
    </AdminGate>
  )
}
