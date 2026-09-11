"""WebSocket-роут с авторизацией."""

import logging
import secrets

from fastapi import APIRouter, Query, WebSocket

from src.auth import SESSION_COOKIE, decode_jwt_token
from src.config import settings
from src.interfaces.websocket import websocket_endpoint

logger = logging.getLogger("loomhub.api.websocket")

router = APIRouter()


@router.websocket("/ws/{session_id}")
async def ws_proxy(
    websocket: WebSocket,
    session_id: str,
    access_token: str | None = Query(default=None),
):
    """Прокси-роут: проверка доступа и передача управления в интерфейс чата."""
    cookie = websocket.cookies.get(SESSION_COOKIE)
    authorized = (
        decode_jwt_token(cookie, settings.session_secret) is not None
        if settings.admin_password_hash and settings.admin_totp_secret
        else bool(
            settings.admin_token
            and access_token
            and secrets.compare_digest(access_token, settings.admin_token)
        )
    )
    if not authorized:
        await websocket.close(code=1008, reason="Требуется административный токен")
        return
    await websocket_endpoint(websocket, session_id)
