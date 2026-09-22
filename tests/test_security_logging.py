"""Tests for ``loomcore.security`` event logging."""

import base64
import logging

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from src.api.deps import rate_limit_exceeded_handler
from src.auth import generate_password_hash
from src.config import settings
from src.core.security import require_admin_token
from src.core.security_log import log_security_event
from src.main import app

TOTP_SECRET = base64.b32encode(b"12345678901234567890").decode()


def test_log_security_event_drops_none(caplog):
    with caplog.at_level(logging.WARNING, logger="loomcore.security"):
        log_security_event("demo_event", client="1.2.3.4", extra=None)

    records = [r for r in caplog.records if r.name == "loomcore.security"]
    assert records, "expected a loomcore.security record"
    message = records[0].getMessage()
    assert "demo_event" in message
    assert "1.2.3.4" in message
    assert "extra" not in message


def test_require_admin_token_logs_auth_failure(monkeypatch, caplog):
    monkeypatch.setattr(settings, "admin_token", "expected")
    monkeypatch.setattr(settings, "admin_password_hash", "")
    monkeypatch.setattr(settings, "admin_totp_secret", "")

    with (
        caplog.at_level(logging.WARNING, logger="loomcore.security"),
        pytest.raises(HTTPException),
    ):
        require_admin_token("wrong")

    assert any("auth_failure" in m for m in caplog.messages)


@pytest.mark.asyncio
async def test_login_failure_is_logged(monkeypatch, caplog):
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "admin_password_hash", generate_password_hash("pw"))
    monkeypatch.setattr(settings, "admin_totp_secret", TOTP_SECRET)
    monkeypatch.setattr(settings, "session_secret", "x" * 40)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with caplog.at_level(logging.WARNING, logger="loomcore.security"):
            response = await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "wrong", "totp": "000000"},
                headers={"X-Forwarded-For": "198.51.100.99"},
            )

    assert response.status_code == 401
    assert any("login_failure" in m for m in caplog.messages)


@pytest.mark.asyncio
async def test_rate_limit_exceeded_is_logged(caplog):
    limiter = Limiter(key_func=get_remote_address)
    mini = FastAPI()
    mini.state.limiter = limiter
    mini.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

    @mini.get("/mini")
    @limiter.limit("1 per 60 seconds")
    async def endpoint(request: Request):
        return JSONResponse({"ok": True})

    async with AsyncClient(transport=ASGITransport(app=mini), base_url="http://test") as client:
        with caplog.at_level(logging.WARNING, logger="loomcore.security"):
            await client.get("/mini")
            response = await client.get("/mini")

    assert response.status_code == 429
    assert any("rate_limit_exceeded" in m for m in caplog.messages)


class _BadRepr:
    def __repr__(self):
        raise RuntimeError("no repr available")


def test_log_security_event_sanitizes_event_name(caplog):
    with caplog.at_level(logging.WARNING, logger="loomcore.security"):
        log_security_event("bad\ninjected\r")

    records = [r for r in caplog.records if r.name == "loomcore.security"]
    assert records, "expected a loomcore.security record"
    message = records[0].getMessage()
    assert "bad\\ninjected\\r" in message
    assert "\n" not in message
    assert "\r" not in message


def test_log_security_event_survives_bad_repr(caplog):
    with caplog.at_level(logging.WARNING, logger="loomcore.security"):
        log_security_event("demo_event", payload=_BadRepr())

    records = [r for r in caplog.records if r.name == "loomcore.security"]
    assert records, "expected a loomcore.security record"
    assert "<unrepresentable>" in records[0].getMessage()


def test_log_security_event_redacts_sensitive_fields(caplog):
    with caplog.at_level(logging.WARNING, logger="loomcore.security"):
        log_security_event(
            "demo_event",
            password="hunter2",
            token="secret-token",
            secret="shh",
            authorization="Bearer abc",
            client="1.2.3.4",
        )

    records = [r for r in caplog.records if r.name == "loomcore.security"]
    assert records, "expected a loomcore.security record"
    message = records[0].getMessage()
    assert "hunter2" not in message
    assert "secret-token" not in message
    assert "shh" not in message
    assert "Bearer abc" not in message
    assert "***" in message
    assert "1.2.3.4" in message


def test_log_security_event_sanitizes_field_keys(caplog):
    with caplog.at_level(logging.WARNING, logger="loomcore.security"):
        log_security_event("demo_event", **{"bad\nkey": "value"})

    records = [r for r in caplog.records if r.name == "loomcore.security"]
    assert records, "expected a loomcore.security record"
    message = records[0].getMessage()
    assert "bad\\nkey='value'" in message
    assert "\n" not in message


def test_log_security_event_redacts_nested_secrets(caplog):
    with caplog.at_level(logging.WARNING, logger="loomcore.security"):
        log_security_event(
            "demo_event",
            payload={"token": "nested-token", "client": "1.2.3.4"},
        )

    records = [r for r in caplog.records if r.name == "loomcore.security"]
    assert records, "expected a loomcore.security record"
    message = records[0].getMessage()
    assert "nested-token" not in message
    assert "***" in message
    assert "1.2.3.4" in message
