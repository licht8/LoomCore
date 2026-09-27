import { ChatRoom } from "@/components/chat-room"

export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ session?: string }>
}) {
  const params = await searchParams
  return <ChatRoom initialSessionId={params.session || undefined} />
}
