"""Tests for the admin auth routes (login, logout, session)."""

import base64
import hashlib
import hmac
import struct
import time

import pytest
from httpx import ASGITransport, AsyncClient

from src.auth import (
    SESSION_COOKIE,
    create_jwt_token,
    decode_jwt_token,
    generate_password_hash,
)
from src.config import settings
from src.main import app

TOTP_SECRET = base64.b32encode(b"12345678901234567890").decode()
LONG_SECRET = "test-session-secret-long-enough-for-hs256-123456"


def _totp_code(secret: str, counter: int) -> str:
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    value = (
        struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    ) % 1_000_000
    return f"{value:06d}"


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _valid_credentials(monkeypatch) -> str:
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password_hash", generate_password_hash("pw"))
    monkeypatch.setattr(settings, "admin_totp_secret", TOTP_SECRET)
    monkeypatch.setattr(settings, "session_secret", LONG_SECRET)
    return _totp_code(TOTP_SECRET, int(time.time()) // 30)


@pytest.mark.asyncio
async def test_login_success_sets_cookie(monkeypatch):
    code = _valid_credentials(monkeypatch)

    async with _client() as client:
        response = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "pw", "totp": code},
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert SESSION_COOKIE in response.cookies


@pytest.mark.asyncio
async def test_login_rejects_bad_credentials(monkeypatch):
    _valid_credentials(monkeypatch)

    async with _client() as client:
        response = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong", "totp": "000000"},
        )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_requires_session_secret(monkeypatch):
    code = _valid_credentials(monkeypatch)
    monkeypatch.setattr(settings, "session_secret", "")

    async with _client() as client:
        response = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "pw", "totp": code},
        )

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_session_requires_cookie(monkeypatch):
    monkeypatch.setattr(settings, "session_secret", LONG_SECRET)

    async with _client() as client:
        response = await client.get("/api/auth/session")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_session_accepts_valid_cookie(monkeypatch):
    monkeypatch.setattr(settings, "session_secret", LONG_SECRET)
    token = create_jwt_token({"username": "admin"}, LONG_SECRET)

    async with _client() as client:
        client.cookies.set(SESSION_COOKIE, token)
        response = await client.get("/api/auth/session")

    assert response.status_code == 200
    assert response.json() == {"authenticated": True}


@pytest.mark.asyncio
async def test_logout_revokes_token(monkeypatch):
    monkeypatch.setattr(settings, "session_secret", LONG_SECRET)
    token = create_jwt_token({"username": "admin", "jti": "logout-jti"}, LONG_SECRET)

    async with _client() as client:
        client.cookies.set(SESSION_COOKIE, token)
        response = await client.post("/api/auth/logout")

    assert response.status_code == 200
    assert decode_jwt_token(token, LONG_SECRET) is None
