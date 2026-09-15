"""Tests for the WebSocket proxy authentication gate."""

import asyncio
import base64
import logging

import pytest

from src.api import websocket as ws_api
from src.auth import SESSION_COOKIE, create_jwt_token, revoke_jwt_token
from src.config import settings

LONG_SECRET = "test-session-secret-long-enough-for-hs256-123456"


def _auth_header(token: str) -> dict:
    encoded = base64.urlsafe_b64encode(token.encode()).decode().rstrip("=")
    return {"sec-websocket-protocol": f"loomcore.auth.{encoded}"}


class _FakeWebSocket:
    def __init__(self, cookies=None, headers=None):
        self.cookies = cookies or {}
        self.headers = headers or {}
        self.closed = None

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)


@pytest.fixture
def captured(monkeypatch):
    calls: list = []

    async def fake_endpoint(websocket, session_id):
        calls.append((websocket, session_id))

    monkeypatch.setattr(ws_api, "websocket_endpoint", fake_endpoint)
    return calls


def _token_mode(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "")
    monkeypatch.setattr(settings, "admin_password_hash", "")
    monkeypatch.setattr(settings, "admin_totp_secret", "")
    monkeypatch.setattr(settings, "admin_token", "secret")


def _jwt_mode(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "")
    monkeypatch.setattr(settings, "admin_password_hash", "hash")
    monkeypatch.setattr(settings, "admin_totp_secret", "totp")
    monkeypatch.setattr(settings, "session_secret", LONG_SECRET)
    monkeypatch.setattr(settings, "admin_token", "")


@pytest.mark.asyncio
async def test_proxy_token_mode_authorized(monkeypatch, captured):
    _token_mode(monkeypatch)
    websocket = _FakeWebSocket(headers=_auth_header("secret"))

    await ws_api.ws_proxy(websocket, "sess-1")

    assert captured == [(websocket, "sess-1")]
    assert websocket.closed is None


@pytest.mark.asyncio
async def test_proxy_token_mode_rejects_wrong_token(monkeypatch, captured):
    _token_mode(monkeypatch)
    websocket = _FakeWebSocket(headers=_auth_header("wrong"))

    await ws_api.ws_proxy(websocket, "sess-1")

    assert captured == []
    assert websocket.closed == (1008, "Admin token required")


@pytest.mark.asyncio
async def test_proxy_jwt_mode_authorized(monkeypatch, captured):
    _jwt_mode(monkeypatch)
    token = create_jwt_token({"username": "admin"}, LONG_SECRET)
    websocket = _FakeWebSocket(cookies={SESSION_COOKIE: token})

    await ws_api.ws_proxy(websocket, "sess-2")

    assert captured == [(websocket, "sess-2")]


@pytest.mark.asyncio
async def test_proxy_both_mode_accepts_static_token(monkeypatch, captured):
    _jwt_mode(monkeypatch)
    monkeypatch.setattr(settings, "auth_mode", "both")
    monkeypatch.setattr(settings, "admin_token", "shared-token")
    websocket = _FakeWebSocket(headers=_auth_header("shared-token"))

    await ws_api.ws_proxy(websocket, "sess-3")

    assert captured == [(websocket, "sess-3")]
    assert websocket.closed is None


@pytest.mark.asyncio
async def test_proxy_jwt_mode_rejects_static_token(monkeypatch, captured):
    _jwt_mode(monkeypatch)
    monkeypatch.setattr(settings, "admin_token", "shared-token")
    websocket = _FakeWebSocket(headers=_auth_header("shared-token"))

    await ws_api.ws_proxy(websocket, "sess-3")

    assert captured == []
    assert websocket.closed == (1008, "Admin token required")


@pytest.mark.asyncio
async def test_proxy_jwt_mode_rejects_wrong_static_token(monkeypatch, captured):
    _jwt_mode(monkeypatch)
    monkeypatch.setattr(settings, "admin_token", "shared-token")
    websocket = _FakeWebSocket(headers=_auth_header("wrong"))

    await ws_api.ws_proxy(websocket, "sess-3")

    assert captured == []
    assert websocket.closed == (1008, "Admin token required")


@pytest.mark.asyncio
async def test_proxy_jwt_mode_rejects_missing_cookie(monkeypatch, captured):
    _jwt_mode(monkeypatch)
    websocket = _FakeWebSocket()

    await ws_api.ws_proxy(websocket, "sess-2")

    assert captured == []
    assert websocket.closed == (1008, "Admin token required")


@pytest.mark.asyncio
async def test_proxy_ignores_query_token(monkeypatch, captured):
    # A token in the URL used to be accepted; it must now be ignored.
    _jwt_mode(monkeypatch)
    monkeypatch.setattr(settings, "admin_token", "shared-token")
    websocket = _FakeWebSocket()
    websocket.query_params = {"access_token": "shared-token"}

    await ws_api.ws_proxy(websocket, "sess-4")

    assert captured == []
    assert websocket.closed == (1008, "Admin token required")


@pytest.mark.asyncio
async def test_watch_auth_closes_socket_when_credential_revoked(monkeypatch):
    _jwt_mode(monkeypatch)
    monkeypatch.setattr(ws_api, "_AUTH_RECHECK_SECONDS", 0)
    token = create_jwt_token({"username": "admin", "jti": "ws-live"}, LONG_SECRET)
    websocket = _FakeWebSocket(cookies={SESSION_COOKIE: token})
    watchdog = asyncio.create_task(ws_api._watch_auth(websocket, token, None))

    await asyncio.sleep(0.01)
    revoke_jwt_token("ws-live")
    await asyncio.wait_for(watchdog, timeout=1)

    assert websocket.closed == (1008, "Session expired")


@pytest.mark.asyncio
async def test_proxy_logs_ws_auth_failure(monkeypatch, captured, caplog):
    _token_mode(monkeypatch)
    websocket = _FakeWebSocket(headers=_auth_header("wrong"))

    with caplog.at_level(logging.WARNING, logger="loomcore.security"):
        await ws_api.ws_proxy(websocket, "sess-1")

    assert websocket.closed == (1008, "Admin token required")
    assert any("ws_auth_failure" in m for m in caplog.messages)
