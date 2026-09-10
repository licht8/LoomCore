import { NextRequest, NextResponse } from 'next/server'

const BACKEND_WS_URL = 'ws://127.0.0.1:8083/ws'

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ sessionId: string }> }
) {
  const { sessionId } = await params
  
  // Получаем URL бэкенда для этой сессии
  const backendWsUrl = `${BACKEND_WS_URL}/${sessionId}`
  
  return new NextResponse('WebSocket proxy endpoint', {
    status: 200,
    headers: {
      'Upgrade': 'websocket',
      'Connection': 'Upgrade',
      'Sec-WebSocket-Accept': 'dummy',
    },
  })
}
