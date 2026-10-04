"""Tests for the admin auth routes (login, logout, session)."""

import base64
import hashlib
import hmac
import logging
import struct
import time

import pytest
from httpx import ASGITransport, AsyncClient

import src.api.auth as auth_module
import src.auth as auth_core
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
async def test_login_sets_secure_cookie_when_configured(monkeypatch):
    code = _valid_credentials(monkeypatch)
    monkeypatch.setattr(settings, "secure_cookies", True)

    async with _client() as client:
        response = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "pw", "totp": code},
        )

    assert "secure" in response.headers["set-cookie"].lower()


@pytest.mark.asyncio
async def test_login_cookie_not_secure_over_http(monkeypatch):
    code = _valid_credentials(monkeypatch)
    monkeypatch.setattr(settings, "secure_cookies", False)

    async with _client() as client:
        response = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "pw", "totp": code},
        )

    assert "secure" not in response.headers["set-cookie"].lower()


@pytest.mark.asyncio
async def test_login_sets_secure_cookie_for_forwarded_proto_list(monkeypatch):
    code = _valid_credentials(monkeypatch)
    monkeypatch.setattr(settings, "secure_cookies", False)

    async with _client() as client:
        response = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "pw", "totp": code},
            headers={"X-Forwarded-Proto": "https, http"},
        )

    assert response.status_code == 200
    assert "secure" in response.headers["set-cookie"].lower()


@pytest.mark.asyncio
async def test_login_sets_secure_cookie_for_uppercase_https(monkeypatch):
    code = _valid_credentials(monkeypatch)
    monkeypatch.setattr(settings, "secure_cookies", False)

    async with _client() as client:
        response = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "pw", "totp": code},
            headers={"X-Forwarded-Proto": "HTTPS"},
        )

    assert response.status_code == 200
    assert "secure" in response.headers["set-cookie"].lower()


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
async def test_login_verifies_password_and_totp_for_wrong_username(monkeypatch):
    code = _valid_credentials(monkeypatch)
    calls = {"password": 0, "totp": 0}

    def spy_password(password, encoded):
        calls["password"] += 1
        return auth_core.verify_password(password, encoded)

    def spy_totp(candidate, secret):
        calls["totp"] += 1
        return auth_core.verify_totp(candidate, secret)

    monkeypatch.setattr(auth_module, "verify_password", spy_password)
    monkeypatch.setattr(auth_module, "verify_totp", spy_totp)

    async with _client() as client:
        response = await client.post(
            "/api/auth/login",
            json={"username": "not-admin", "password": "pw", "totp": code},
            headers={"X-Forwarded-For": "198.51.100.40"},
        )

    assert response.status_code == 401
    assert calls == {"password": 1, "totp": 1}


@pytest.mark.asyncio
async def test_login_uses_dummy_hash_when_unconfigured(monkeypatch):
    code = _valid_credentials(monkeypatch)
    monkeypatch.setattr(settings, "admin_password_hash", "")
    monkeypatch.setattr(settings, "auth_mode", "jwt")
    seen = {}

    def spy_password(password, encoded):
        seen["encoded"] = encoded
        return auth_core.verify_password(password, encoded)

    monkeypatch.setattr(auth_module, "verify_password", spy_password)

    async with _client() as client:
        response = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "pw", "totp": code},
            headers={"X-Forwarded-For": "198.51.100.41"},
        )

    assert response.status_code == 401
    assert seen["encoded"]
    assert seen["encoded"].startswith("pbkdf2_sha256$")


@pytest.mark.asyncio
async def test_login_rejects_malformed_json(monkeypatch, caplog):
    _valid_credentials(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="loomcore.security"):
        async with _client() as client:
            response = await client.post(
                "/api/auth/login",
                content="{",
                headers={
                    "Content-Type": "application/json",
                    "X-Forwarded-For": "198.51.100.20",
                },
            )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid JSON body"}
    assert "invalid_json_body" in caplog.text


@pytest.mark.asyncio
async def test_login_rejects_non_object_json(monkeypatch, caplog):
    _valid_credentials(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="loomcore.security"):
        async with _client() as client:
            response = await client.post(
                "/api/auth/login",
                json=["admin"],
                headers={"X-Forwarded-For": "198.51.100.21"},
            )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid request body"}
    assert "invalid_body_shape" in caplog.text


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
async def test_login_requires_tls_when_configured(monkeypatch):
    code = _valid_credentials(monkeypatch)
    monkeypatch.setattr(settings, "require_tls", True)

    async with _client() as client:
        response = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "pw", "totp": code},
            headers={"X-Forwarded-For": "198.51.100.10"},
        )

    assert response.status_code == 403
    assert "TLS" in response.json()["detail"]


@pytest.mark.asyncio
async def test_login_allowed_over_https_when_tls_required(monkeypatch):
    code = _valid_credentials(monkeypatch)
    monkeypatch.setattr(settings, "require_tls", True)

    async with _client() as client:
        response = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "pw", "totp": code},
            headers={
                "X-Forwarded-For": "198.51.100.11",
                "X-Forwarded-Proto": "https",
            },
        )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_login_disabled_in_token_mode(monkeypatch):
    code = _valid_credentials(monkeypatch)
    monkeypatch.setattr(settings, "auth_mode", "token")
    monkeypatch.setattr(settings, "admin_token", "static")

    async with _client() as client:
        response = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "pw", "totp": code},
            headers={"X-Forwarded-For": "198.51.100.12"},
        )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_session_requires_cookie(monkeypatch):
    monkeypatch.setattr(settings, "session_secret", LONG_SECRET)

    async with _client() as client:
        response = await client.get("/api/auth/session")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_session_accepts_valid_cookie(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "jwt")
    monkeypatch.setattr(settings, "admin_password_hash", "hash")
    monkeypatch.setattr(settings, "admin_totp_secret", "totp")
    monkeypatch.setattr(settings, "session_secret", LONG_SECRET)
    token = create_jwt_token({"username": "admin"}, LONG_SECRET)

    async with _client() as client:
        client.cookies.set(SESSION_COOKIE, token)
        response = await client.get("/api/auth/session")

    assert response.status_code == 200
    assert response.json() == {"authenticated": True}


@pytest.mark.asyncio
async def test_session_cookie_requires_tls_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "jwt")
    monkeypatch.setattr(settings, "admin_password_hash", "hash")
    monkeypatch.setattr(settings, "admin_totp_secret", "totp")
    monkeypatch.setattr(settings, "session_secret", LONG_SECRET)
    monkeypatch.setattr(settings, "require_tls", True)
    token = create_jwt_token({"username": "admin"}, LONG_SECRET)

    async with _client() as client:
        client.cookies.set(SESSION_COOKIE, token)
        response = await client.get(
            "/api/auth/session",
            headers={"X-Forwarded-For": "198.51.100.33"},
        )

    assert response.status_code == 403
    assert "TLS" in response.json()["detail"]


@pytest.mark.asyncio
async def test_session_cookie_allowed_over_https_when_tls_required(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "jwt")
    monkeypatch.setattr(settings, "admin_password_hash", "hash")
    monkeypatch.setattr(settings, "admin_totp_secret", "totp")
    monkeypatch.setattr(settings, "session_secret", LONG_SECRET)
    monkeypatch.setattr(settings, "require_tls", True)
    token = create_jwt_token({"username": "admin"}, LONG_SECRET)

    async with _client() as client:
        client.cookies.set(SESSION_COOKIE, token)
        response = await client.get(
            "/api/auth/session",
            headers={"X-Forwarded-Proto": "https"},
        )

    assert response.status_code == 200
    assert response.json() == {"authenticated": True}


@pytest.mark.asyncio
async def test_session_bad_cookie_falls_through_to_bearer_over_https(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "token")
    monkeypatch.setattr(settings, "admin_token", "static-token")
    monkeypatch.setattr(settings, "require_tls", True)

    async with _client() as client:
        client.cookies.set(SESSION_COOKIE, "bogus")
        response = await client.get(
            "/api/auth/session",
            headers={
                "Authorization": "Bearer static-token",
                "X-Forwarded-Proto": "https",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"authenticated": True}


@pytest.mark.asyncio
async def test_session_rejects_invalid_cookie_without_token(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "token")
    monkeypatch.setattr(settings, "admin_token", "static-token")

    async with _client() as client:
        client.cookies.set(SESSION_COOKIE, "bogus")
        response = await client.get("/api/auth/session")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_session_rejects_cookie_when_jwt_disabled(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "token")
    monkeypatch.setattr(settings, "admin_token", "static-token")
    monkeypatch.setattr(settings, "admin_password_hash", "hash")
    monkeypatch.setattr(settings, "admin_totp_secret", "totp")
    monkeypatch.setattr(settings, "session_secret", LONG_SECRET)
    token = create_jwt_token({"username": "admin"}, LONG_SECRET)

    async with _client() as client:
        client.cookies.set(SESSION_COOKIE, token)
        response = await client.get("/api/auth/session")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_session_accepts_case_insensitive_bearer(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "token")
    monkeypatch.setattr(settings, "admin_token", "static-token")

    async with _client() as client:
        response = await client.get(
            "/api/auth/session",
            headers={"Authorization": "bearer static-token"},
        )

    assert response.status_code == 200
    assert response.json() == {"authenticated": True}


@pytest.mark.asyncio
async def test_session_accepts_static_bearer_token(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "token")
    monkeypatch.setattr(settings, "admin_token", "static-token")

    async with _client() as client:
        response = await client.get(
            "/api/auth/session",
            headers={"Authorization": "Bearer static-token"},
        )

    assert response.status_code == 200
    assert response.json() == {"authenticated": True}


@pytest.mark.asyncio
async def test_session_falls_through_to_bearer_when_cookie_is_stale(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "token")
    monkeypatch.setattr(settings, "admin_token", "static-token")

    async with _client() as client:
        client.cookies.set(SESSION_COOKIE, "bogus")
        response = await client.get(
            "/api/auth/session",
            headers={"Authorization": "Bearer static-token"},
        )

    assert response.status_code == 200
    assert response.json() == {"authenticated": True}


@pytest.mark.asyncio
async def test_session_rejects_wrong_bearer_token(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "token")
    monkeypatch.setattr(settings, "admin_token", "static-token")

    async with _client() as client:
        response = await client.get(
            "/api/auth/session",
            headers={"Authorization": "Bearer wrong-token"},
        )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_logout_revokes_token(monkeypatch):
    monkeypatch.setattr(settings, "session_secret", LONG_SECRET)
    token = create_jwt_token({"username": "admin", "jti": "logout-jti"}, LONG_SECRET)

    async with _client() as client:
        client.cookies.set(SESSION_COOKIE, token)
        response = await client.post("/api/auth/logout")

    assert response.status_code == 200
    assert decode_jwt_token(token, LONG_SECRET) is None


@pytest.mark.asyncio
async def test_protected_endpoint_requires_tls_for_bearer_token(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "token")
    monkeypatch.setattr(settings, "admin_token", "static-token")
    monkeypatch.setattr(settings, "require_tls", True)

    async with _client() as client:
        response = await client.get(
            "/api/plugins/status",
            headers={
                "Authorization": "Bearer static-token",
                "X-Forwarded-For": "198.51.100.30",
            },
        )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_protected_endpoint_requires_tls_for_cookie(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "")
    monkeypatch.setattr(settings, "admin_password_hash", "hash")
    monkeypatch.setattr(settings, "admin_totp_secret", "totp")
    monkeypatch.setattr(settings, "session_secret", LONG_SECRET)
    monkeypatch.setattr(settings, "require_tls", True)
    token = create_jwt_token({"username": "admin"}, LONG_SECRET)

    async with _client() as client:
        client.cookies.set(SESSION_COOKIE, token)
        response = await client.get(
            "/api/plugins/status",
            headers={"X-Forwarded-For": "198.51.100.31"},
        )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_protected_endpoint_allows_token_when_tls_not_required(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "token")
    monkeypatch.setattr(settings, "admin_token", "static-token")
    monkeypatch.setattr(settings, "require_tls", False)

    async with _client() as client:
        response = await client.get(
            "/api/plugins/status",
            headers={
                "Authorization": "Bearer static-token",
                "X-Forwarded-For": "198.51.100.32",
            },
        )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_session_rate_limited(monkeypatch):
    monkeypatch.setattr(settings, "session_secret", LONG_SECRET)
    headers = {"X-Forwarded-For": "203.0.113.250"}

    async with _client() as client:
        for _ in range(30):
            response = await client.get("/api/auth/session", headers=headers)
            assert response.status_code == 401
        limited = await client.get("/api/auth/session", headers=headers)

    assert limited.status_code == 429
