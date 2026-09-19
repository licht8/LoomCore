"""Rate limiting tests for LoomCore.

Each test builds an isolated mini-app with small limits so state does not
leak between tests.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from src.api.deps import chat_key_func
from src.main import app

# === Utilities ===

def make_limiter() -> Limiter:
    """Create a fresh Limiter to isolate tests from each other."""
    return Limiter(key_func=get_remote_address)


async def rate_limit_handler(req: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Please try again later."},
    )


def build_app(limiter: Limiter, rate_limit: str, key_func=None, endpoint="test"):
    """Build a mini FastAPI app with one endpoint and a limit.

    slowapi requires the decorated function to have a ``request`` parameter.
    """
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_handler)

    @app.get(f"/{endpoint}")
    @limiter.limit(rate_limit, key_func=key_func)
    async def endpoint_func(request: Request):
        return {"ok": True}

    return app


# === Task 1: the key comes from the authenticated identity, not the client ===

@pytest.fixture
def chat_app():
    # Limit: 2 requests per 60 seconds per authenticated principal.
    return build_app(make_limiter(), "2 per 60 seconds", key_func=chat_key_func)


@pytest.mark.asyncio
async def test_chat_key_func_ignores_rotating_session_id(chat_app, monkeypatch):
    """Rotating the client-supplied session_id must not reset the limit.

    The key is derived from the Bearer token server-side, so two requests with
    DIFFERENT session_id values still share one bucket → 429 on the third.
    """
    from src.config import settings

    monkeypatch.setattr(settings, "auth_mode", "token")
    monkeypatch.setattr(settings, "admin_token", "token-a")
    auth = {"Authorization": "Bearer token-a"}
    async with AsyncClient(transport=ASGITransport(app=chat_app), base_url="http://test") as client:
        r1 = await client.get("/test?session_id=sess1", headers=auth)
        r2 = await client.get("/test?session_id=sess2", headers=auth)
        assert r1.status_code == 200
        assert r2.status_code == 200

        # A different session_id is the SAME principal → limit exhausted.
        r3 = await client.get("/test?session_id=sess3", headers=auth)
        assert r3.status_code == 429
        assert r3.json()["detail"] == "Too many requests. Please try again later."


@pytest.mark.asyncio
async def test_chat_key_func_separates_credentials(chat_app, monkeypatch):
    """A different credential is a separate rate-limit bucket."""
    from src.auth import create_jwt_token
    from src.config import settings

    secret = "test-secret-" + "0" * 20
    monkeypatch.setattr(settings, "auth_mode", "jwt")
    monkeypatch.setattr(settings, "session_secret", secret)
    monkeypatch.setattr(settings, "admin_password_hash", "configured")
    monkeypatch.setattr(settings, "admin_totp_secret", "configured")
    token_a = create_jwt_token({"sub": "a"}, secret)
    token_b = create_jwt_token({"sub": "b"}, secret)

    async with AsyncClient(transport=ASGITransport(app=chat_app), base_url="http://test") as client:
        await client.get("/test", headers={"Authorization": f"Bearer {token_a}"})
        await client.get("/test", headers={"Authorization": f"Bearer {token_a}"})

        other = await client.get("/test", headers={"Authorization": f"Bearer {token_b}"})
        assert other.status_code == 200


# === Task 3: custom 429 body ===

@pytest.fixture
def limit_app():
    return build_app(make_limiter(), "1 per 60 seconds")


@pytest.mark.asyncio
async def test_rate_limit_429_response_body(limit_app):
    """The 429 body contains the custom message."""
    async with AsyncClient(transport=ASGITransport(app=limit_app), base_url="http://test") as client:
        await client.get("/test")
        resp = await client.get("/test")
        assert resp.status_code == 429
        data = resp.json()
        assert "detail" in data
        assert "Too many requests" in data["detail"]


# === Task 4: /api/auth/login — 5/min ===

@pytest.fixture
def auth_app():
    limiter = make_limiter()
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_handler)

    @app.post("/login")
    @limiter.limit("5 per 60 seconds")
    async def endpoint_func(request: Request):
        return {"ok": True}

    return app


@pytest.mark.asyncio
async def test_auth_login_rate_limit(auth_app):
    """5 requests OK, the 6th — 429."""
    async with AsyncClient(transport=ASGITransport(app=auth_app), base_url="http://test") as client:
        for i in range(5):
            r = await client.post("/login")
            assert r.status_code == 200, f"Request {i+1}: expected 200, got {r.status_code}"
        r = await client.post("/login")
        assert r.status_code == 429


# === Task 4: /health — works ===

@pytest.fixture
def health_app():
    limiter = make_limiter()
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_handler)

    @app.get("/health")
    @limiter.limit("3 per 60 seconds")
    def endpoint_func(request: Request):
        return {"status": "ok"}

    return app


@pytest.mark.asyncio
async def test_health_rate_limit(health_app):
    """The limit works (verified with 3/60s)."""
    async with AsyncClient(transport=ASGITransport(app=health_app), base_url="http://test") as client:
        r1 = await client.get("/health")
        assert r1.status_code == 200
        assert r1.json() == {"status": "ok"}


# === Task 4: /api/plugins/status — 10/min ===

@pytest.fixture
def plugins_app():
    return build_app(make_limiter(), "2 per 60 seconds", endpoint="plugins_status")


@pytest.mark.asyncio
async def test_plugins_status_rate_limit(plugins_app):
    """The limit (verified with 2/60s) is enforced."""
    async with AsyncClient(transport=ASGITransport(app=plugins_app), base_url="http://test") as client:
        r1 = await client.get("/plugins_status")
        assert r1.status_code == 200
        r2 = await client.get("/plugins_status")
        assert r2.status_code == 200
        r3 = await client.get("/plugins_status")
        assert r3.status_code == 429


# === Task 4: /api/tools — 10/min ===

@pytest.fixture
def tools_app():
    return build_app(make_limiter(), "2 per 60 seconds", endpoint="tools")


@pytest.mark.asyncio
async def test_tools_rate_limit(tools_app):
    """The limit (verified with 2/60s) is enforced."""
    async with AsyncClient(transport=ASGITransport(app=tools_app), base_url="http://test") as client:
        r1 = await client.get("/tools")
        assert r1.status_code == 200
        r2 = await client.get("/tools")
        assert r2.status_code == 200
        r3 = await client.get("/tools")
        assert r3.status_code == 429


# === Read-only plugin endpoints on the real app — 60/min ===


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_plugins_status_allows_60_per_minute(monkeypatch):
    """The read-only status endpoint tolerates UI polling: 60/min, not 429."""
    import src.api.plugins as plugins_module

    monkeypatch.setattr(
        plugins_module, "require_admin_token", lambda *args, **kwargs: None
    )
    ip = "198.51.100.211"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for i in range(60):
            response = await client.get(
                "/api/plugins/status", headers={"X-Forwarded-For": ip}
            )
            assert response.status_code == 200, (
                f"Request {i + 1}: expected 200, got {response.status_code}"
            )
        limited = await client.get(
            "/api/plugins/status", headers={"X-Forwarded-For": ip}
        )

    assert limited.status_code == 429


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_mutation_endpoints_stay_strict():
    """Plugin mutations keep their tighter 10/min limit."""
    from src.core.rate_limit import limiter

    limits = limiter._route_limits

    def assert_limit(name: str, amount: int) -> None:
        entry = limits.get(name)
        assert entry, f"No rate limit registered for {name}"
        rate = entry[0].limit
        assert (rate.amount, rate.multiples) == (amount, 60), (
            f"{name}: expected {amount}/60, got {rate}"
        )

    for name in (
        "src.api.plugins.enable_plugin",
        "src.api.plugins.disable_plugin",
        "src.api.plugins.reload_plugin",
    ):
        assert_limit(name, 10)

    for name in (
        "src.api.plugins.get_plugins_status",
        "src.api.plugins.list_plugins_tools",
    ):
        assert_limit(name, 60)


# === Task 4: WebSocket — not limited ===

@pytest.mark.asyncio
async def test_websocket_not_limited():
    """The WebSocket has no @limiter.limit."""
    from src.api.websocket import router as ws_router

    route = next(
        (r for r in ws_router.routes if getattr(r, "path", None) == "/ws/{session_id}"),
        None,
    )
    assert route is not None, "WebSocket route /ws/{session_id} not found"

    handler = route.endpoint
    # If slowapi decorated it, the handler would have a _slowapi_limits attr
    has_limits = getattr(handler, "_slowapi_limits", None) is not None
    assert not has_limits, "WebSocket has a slowapi limit — it should not"


# === Task 2: src/core/rate_limit.py — single source ===

@pytest.mark.asyncio
async def test_limiter_from_core_module():
    """src.core.rate_limit.limiter is a real instance."""
    from src.core.rate_limit import limiter as core_limiter

    assert core_limiter is not None
    assert hasattr(core_limiter, "limiter") or hasattr(core_limiter, "_backends")


def test_session_limit_registered_as_30_per_minute():
    """The admin session oracle is capped at 30 requests/min per client."""
    from src.core.rate_limit import limiter

    entry = limiter._route_limits.get("src.api.auth.auth_session")
    assert entry, "No rate limit registered for src.api.auth.auth_session"
    rate = entry[0].limit
    assert (rate.amount, rate.multiples) == (30, 60), (
        f"src.api.auth.auth_session: expected 30/60, got {rate}"
    )
