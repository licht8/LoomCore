"""Tests for real-client-IP resolution behind a trusted reverse proxy.

Every HTTP test here goes through ``ASGITransport``, whose default peer is
``127.0.0.1`` (a trusted proxy by default), so ``X-Forwarded-For`` is honoured
exactly as it is behind the bundled nginx.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request as StarletteRequest

from src.config import settings
from src.core.client_ip import _is_trusted, _trusted_networks, client_ip

LOOPBACK = "127.0.0.1,::1"


@pytest.fixture(autouse=True)
def _reset_trusted(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxies", LOOPBACK)
    _trusted_networks.cache_clear()
    yield
    _trusted_networks.cache_clear()


def make_request(peer: str | None, headers: dict[str, str] | None = None) -> StarletteRequest:
    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": raw_headers,
        "client": (peer, 12345) if peer else None,
    }
    return StarletteRequest(scope)


# === client_ip ===


def test_direct_peer_used_when_not_trusted():
    request = make_request("203.0.113.5", {"X-Forwarded-For": "1.2.3.4"})
    assert client_ip(request) == "203.0.113.5"


def test_forwarded_for_used_when_peer_is_trusted():
    request = make_request("127.0.0.1", {"X-Forwarded-For": "203.0.113.7"})
    assert client_ip(request) == "203.0.113.7"


def test_rightmost_untrusted_hop_wins_over_spoofed_left_entry():
    # nginx appends the peer it actually saw; a client-supplied left entry is spoofing.
    request = make_request("127.0.0.1", {"X-Forwarded-For": "1.2.3.4, 203.0.113.8"})
    assert client_ip(request) == "203.0.113.8"


def test_x_real_ip_is_the_fallback():
    request = make_request("127.0.0.1", {"X-Real-IP": "203.0.113.9"})
    assert client_ip(request) == "203.0.113.9"


def test_forwarded_for_ignored_from_untrusted_peer(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxies", "10.0.0.0/8")
    _trusted_networks.cache_clear()
    request = make_request("127.0.0.1", {"X-Forwarded-For": "9.9.9.9"})
    assert client_ip(request) == "127.0.0.1"


def test_invalid_trusted_proxies_entry_is_ignored(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxies", "not-an-ip,10.0.0.0/8")
    _trusted_networks.cache_clear()
    request = make_request("10.1.2.3", {"X-Forwarded-For": "203.0.113.10"})
    assert client_ip(request) == "203.0.113.10"


def test_ipv4_mapped_loopback_is_trusted(monkeypatch):
    # A dual-stack listener can present the loopback proxy as ::ffff:127.0.0.1.
    assert _is_trusted("::ffff:127.0.0.1") is True
    request = make_request("::ffff:127.0.0.1", {"X-Forwarded-For": "203.0.113.11"})
    assert client_ip(request) == "203.0.113.11"


def test_malformed_forwarded_entries_are_skipped(caplog):
    request = make_request("127.0.0.1", {"X-Forwarded-For": "203.0.113.12, garbage"})
    with caplog.at_level(logging.WARNING, logger="loomcore.security"):
        assert client_ip(request) == "203.0.113.12"
    assert "Ignoring non-IP X-Forwarded-For entry 'garbage'" in caplog.text


def test_malformed_x_real_ip_falls_back_to_peer(caplog):
    request = make_request("127.0.0.1", {"X-Real-IP": "evil.example.com"})
    with caplog.at_level(logging.WARNING, logger="loomcore.security"):
        assert client_ip(request) == "127.0.0.1"
    assert "Ignoring non-IP X-Real-IP value 'evil.example.com'" in caplog.text


# === rate limiting by real client IP ===


def _handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(status_code=429, content={"detail": "slow down"})


def _app() -> FastAPI:
    limiter = Limiter(key_func=client_ip)
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _handler)

    @app.get("/mini")
    @limiter.limit("1 per 60 seconds")
    async def mini(request: Request):
        return {"ok": True}

    return app


@pytest.mark.asyncio
async def test_two_client_ips_get_separate_buckets():
    """Two clients behind the proxy must not share one bucket."""
    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://test"
    ) as client:
        first = await client.get("/mini", headers={"X-Forwarded-For": "203.0.113.1"})
        second = await client.get("/mini", headers={"X-Forwarded-For": "203.0.113.1"})
        other = await client.get("/mini", headers={"X-Forwarded-For": "203.0.113.2"})

    assert first.status_code == 200
    assert second.status_code == 429
    assert other.status_code == 200
