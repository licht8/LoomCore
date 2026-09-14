"""WebSocket route with authorization."""

import base64
import logging
import secrets

from fastapi import APIRouter, WebSocket

from src.auth import SESSION_COOKIE, decode_jwt_token
from src.config import settings
from src.interfaces.websocket import websocket_endpoint

logger = logging.getLogger("loomcore.api.websocket")

router = APIRouter()

_AUTH_SUBPROTOCOL_PREFIX = "loomcore.auth."


def _static_token_from_subprotocol(websocket: WebSocket) -> str | None:
    """Read a base64url-encoded static token from the WS subprotocol header.

    Browsers cannot set an Authorization header on a WebSocket handshake, so
    the static token travels as a subprotocol (``loomcore.auth.<b64url>``)
    instead of the query string, which would leak into access logs and history.
    """
    header = websocket.headers.get("sec-websocket-protocol", "")
    for offer in header.split(","):
        value = offer.strip()
        if not value.startswith(_AUTH_SUBPROTOCOL_PREFIX):
            continue
        encoded = value[len(_AUTH_SUBPROTOCOL_PREFIX) :]
        try:
            return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
        except (ValueError, UnicodeDecodeError):
            return None
    return None


@router.websocket("/ws/{session_id}")
async def ws_proxy(websocket: WebSocket, session_id: str):
    """Proxy route: verify access, then hand off to the chat interface.

    Accepts either the JWT session cookie or the static admin token supplied
    via the ``loomcore.auth.<base64url>`` subprotocol. Query-string tokens are
    intentionally NOT accepted (they leak into access logs and history).
    """
    cookie = websocket.cookies.get(SESSION_COOKIE)
    jwt_ok = decode_jwt_token(cookie, settings.session_secret) is not None
    token = _static_token_from_subprotocol(websocket)
    token_ok = bool(
        settings.admin_token
        and token
        and secrets.compare_digest(token, settings.admin_token)
    )
    if not (jwt_ok or token_ok):
        await websocket.close(code=1008, reason="Admin token required")
        return
    await websocket_endpoint(websocket, session_id)
