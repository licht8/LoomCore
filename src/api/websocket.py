"""WebSocket route with authorization."""

import asyncio
import base64
import contextlib
import logging
import secrets

from fastapi import APIRouter, WebSocket

from src.auth import SESSION_COOKIE, decode_jwt_token
from src.config import settings
from src.core.client_ip import client_ip
from src.core.security import auth_mode, is_https
from src.core.security_log import log_security_event
from src.interfaces.websocket import websocket_endpoint

logger = logging.getLogger("loomcore.api.websocket")

router = APIRouter()

_AUTH_SUBPROTOCOL_PREFIX = "loomcore.auth."

# How often a live socket re-checks that its credential is still valid. A JWT
# can expire or be revoked after the handshake; a socket must not outlive it.
_AUTH_RECHECK_SECONDS = 60


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


def _jwt_valid(cookie: str | None) -> bool:
    if auth_mode() not in {"jwt", "both"}:
        return False
    return decode_jwt_token(cookie, settings.session_secret) is not None


def _static_token_valid(token: str | None) -> bool:
    if auth_mode() not in {"token", "both"}:
        return False
    return bool(
        settings.admin_token
        and token
        and secrets.compare_digest(token, settings.admin_token)
    )


async def _watch_auth(
    websocket: WebSocket, cookie: str | None, token: str | None
) -> None:
    """Close an open socket when its credential expires or is revoked."""
    try:
        while True:
            await asyncio.sleep(_AUTH_RECHECK_SECONDS)
            if not (_jwt_valid(cookie) or _static_token_valid(token)):
                log_security_event("ws_auth_revoked", client=client_ip(websocket))
                logger.warning("WebSocket credential is no longer valid; closing")
                await websocket.close(code=1008, reason="Session expired")
                return
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # pragma: no cover - socket already gone
        logger.debug("WebSocket auth watchdog stopped: %s", exc)


@router.websocket("/ws/{session_id}")
async def ws_proxy(websocket: WebSocket, session_id: str):
    """Proxy route: verify access, then hand off to the chat interface.

    Accepts either the JWT session cookie or the static admin token supplied
    via the ``loomcore.auth.<base64url>`` subprotocol. Query-string tokens are
    intentionally NOT accepted (they leak into access logs and history). Auth
    is re-checked periodically while the connection is open, so a revoked or
    expired credential cannot keep a live socket.
    """
    if settings.require_tls and not is_https(websocket):
        log_security_event(
            "ws_auth_failure",
            reason="tls_required",
            client=client_ip(websocket),
            session_id=session_id,
        )
        await websocket.close(code=1008, reason="TLS required")
        return
    cookie = websocket.cookies.get(SESSION_COOKIE)
    token = _static_token_from_subprotocol(websocket)
    if not (_jwt_valid(cookie) or _static_token_valid(token)):
        log_security_event(
            "ws_auth_failure", client=client_ip(websocket), session_id=session_id
        )
        await websocket.close(code=1008, reason="Admin token required")
        return
    watchdog = asyncio.create_task(_watch_auth(websocket, cookie, token))
    try:
        await websocket_endpoint(websocket, session_id)
    finally:
        watchdog.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watchdog
