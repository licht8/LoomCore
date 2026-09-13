"""WebSocket route with authorization."""

import logging
import secrets

from fastapi import APIRouter, Query, WebSocket

from src.auth import SESSION_COOKIE, decode_jwt_token
from src.config import settings
from src.interfaces.websocket import websocket_endpoint

logger = logging.getLogger("loomcore.api.websocket")

router = APIRouter()


@router.websocket("/ws/{session_id}")
async def ws_proxy(
    websocket: WebSocket,
    session_id: str,
    access_token: str | None = Query(default=None),
):
    """Proxy route: verify access, then hand off to the chat interface."""
    cookie = websocket.cookies.get(SESSION_COOKIE)
    jwt_ok = decode_jwt_token(cookie, settings.session_secret) is not None
    token_ok = bool(
        settings.admin_token
        and access_token
        and secrets.compare_digest(access_token, settings.admin_token)
    )
    if not (jwt_ok or token_ok):
        await websocket.close(code=1008, reason="Admin token required")
        return
    await websocket_endpoint(websocket, session_id)
