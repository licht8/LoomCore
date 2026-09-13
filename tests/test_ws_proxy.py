"""Tests for the WebSocket proxy authentication gate."""

import pytest

from src.api import websocket as ws_api
from src.auth import SESSION_COOKIE, create_jwt_token
from src.config import settings

LONG_SECRET = "test-session-secret-long-enough-for-hs256-123456"


class _FakeWebSocket:
    def __init__(self, cookies=None):
        self.cookies = cookies or {}
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
    monkeypatch.setattr(settings, "admin_password_hash", "")
    monkeypatch.setattr(settings, "admin_totp_secret", "")
    monkeypatch.setattr(settings, "admin_token", "secret")


def _jwt_mode(monkeypatch):
    monkeypatch.setattr(settings, "admin_password_hash", "hash")
    monkeypatch.setattr(settings, "admin_totp_secret", "totp")
    monkeypatch.setattr(settings, "session_secret", LONG_SECRET)


@pytest.mark.asyncio
async def test_proxy_token_mode_authorized(monkeypatch, captured):
    _token_mode(monkeypatch)
    websocket = _FakeWebSocket()

    await ws_api.ws_proxy(websocket, "sess-1", access_token="secret")

    assert captured == [(websocket, "sess-1")]
    assert websocket.closed is None


@pytest.mark.asyncio
async def test_proxy_token_mode_rejects_wrong_token(monkeypatch, captured):
    _token_mode(monkeypatch)
    websocket = _FakeWebSocket()

    await ws_api.ws_proxy(websocket, "sess-1", access_token="wrong")

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
async def test_proxy_jwt_mode_rejects_missing_cookie(monkeypatch, captured):
    _jwt_mode(monkeypatch)
    websocket = _FakeWebSocket()

    await ws_api.ws_proxy(websocket, "sess-2")

    assert captured == []
    assert websocket.closed == (1008, "Admin token required")
