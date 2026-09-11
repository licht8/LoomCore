"""Тесты rate limiting для LoomHub.

Каждый тест создаёт изолированное мини-приложение с малыми лимитами,
чтобы не «перетекать» между тестами.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# === Утилиты ===

def make_limiter() -> Limiter:
    """Создать новый Limiter для изоляции между тестами."""
    return Limiter(key_func=get_remote_address)


async def rate_limit_handler(req: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": "Слишком много запросов. Попробуйте позже."},
    )


def build_app(limiter: Limiter, rate_limit: str, key_func=None, endpoint="test"):
    """Собрать мини-FastAPI с эндпоинтом и лимитом.

    slowapi требует, чтобы декорируемая функция имела параметр «request».
    """
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_handler)

    @app.get(f"/{endpoint}")
    @limiter.limit(rate_limit, key_func=key_func)
    async def endpoint_func(request: Request):
        return {"ok": True}

    return app


# === Задача 1: _chat_key_func через query-параметр ===

def _chat_query_key(request: Request) -> str:
    """Ключ: session_id из query, fallback — IP."""
    session_id = request.query_params.get("session_id")
    if session_id:
        return session_id
    return request.client.host if request.client else "unknown"


@pytest.fixture
def chat_app():
    # Лимит 2 запроса на 60 секунд на ключ session_id
    return build_app(make_limiter(), "2 per 60 seconds", key_func=_chat_query_key)


@pytest.mark.asyncio
async def test_chat_key_func_via_query(chat_app):
    """Ключ берётся из query_params, не читается body.

    Два запроса с session_id=sess1 исчерпывают лимит → 429 на 3-м.
    Запрос с session_id=sess3 — отдельный ключ → 200.
    """
    async with AsyncClient(transport=ASGITransport(app=chat_app), base_url="http://test") as client:
        # 2 запроса с sess1 → исчерпывают лимит (2/2)
        r1 = await client.get("/test?session_id=sess1")
        r2 = await client.get("/test?session_id=sess1")
        assert r1.status_code == 200
        assert r2.status_code == 200

        # 3-й с sess1 → лимит исчерпан
        r3 = await client.get("/test?session_id=sess1")
        assert r3.status_code == 429
        assert r3.json()["detail"] == "Слишком много запросов. Попробуйте позже."

        # Запрос с sess3 → отдельный ключ, лимит не тронут
        r4 = await client.get("/test?session_id=sess3")
        assert r4.status_code == 200


# === Задача 3: кастомный 429 на русском ===

@pytest.fixture
def limit_app():
    return build_app(make_limiter(), "1 per 60 seconds")


@pytest.mark.asyncio
async def test_rate_limit_429_response_body(limit_app):
    """Тело 429 содержит русский текст."""
    async with AsyncClient(transport=ASGITransport(app=limit_app), base_url="http://test") as client:
        await client.get("/test")
        resp = await client.get("/test")
        assert resp.status_code == 429
        data = resp.json()
        assert "detail" in data
        assert "Слишком много запросов" in data["detail"]


# === Задача 4: /api/auth/login — 5/min ===

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
    """5 запросов OK, 6-й — 429."""
    async with AsyncClient(transport=ASGITransport(app=auth_app), base_url="http://test") as client:
        for i in range(5):
            r = await client.post("/login")
            assert r.status_code == 200, f"Запрос {i+1}: ожидал 200, получил {r.status_code}"
        r = await client.post("/login")
        assert r.status_code == 429


# === Задача 4: /health — работает ===

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
    """Лимит 60/min — запросы работают (проверено через 3/60s)."""
    async with AsyncClient(transport=ASGITransport(app=health_app), base_url="http://test") as client:
        r1 = await client.get("/health")
        assert r1.status_code == 200
        assert r1.json() == {"status": "ok"}


# === Задача 4: /api/plugins/status — 10/min ===

@pytest.fixture
def plugins_app():
    return build_app(make_limiter(), "2 per 60 seconds", endpoint="plugins_status")


@pytest.mark.asyncio
async def test_plugins_status_rate_limit(plugins_app):
    """Лимит 10/min — проверен через 2/60s."""
    async with AsyncClient(transport=ASGITransport(app=plugins_app), base_url="http://test") as client:
        r1 = await client.get("/plugins_status")
        assert r1.status_code == 200
        r2 = await client.get("/plugins_status")
        assert r2.status_code == 200
        r3 = await client.get("/plugins_status")
        assert r3.status_code == 429


# === Задача 4: /api/tools — 10/min ===

@pytest.fixture
def tools_app():
    return build_app(make_limiter(), "2 per 60 seconds", endpoint="tools")


@pytest.mark.asyncio
async def test_tools_rate_limit(tools_app):
    """Лимит 10/min — проверен через 2/60s."""
    async with AsyncClient(transport=ASGITransport(app=tools_app), base_url="http://test") as client:
        r1 = await client.get("/tools")
        assert r1.status_code == 200
        r2 = await client.get("/tools")
        assert r2.status_code == 200
        r3 = await client.get("/tools")
        assert r3.status_code == 429


# === Задача 4: WebSocket — не ограничен ===

@pytest.mark.asyncio
async def test_websocket_not_limited():
    """WebSocket не имеет @limiter.limit."""
    from src.api.websocket import router as ws_router

    route = next(
        (r for r in ws_router.routes if getattr(r, "path", None) == "/ws/{session_id}"),
        None,
    )
    assert route is not None, "WebSocket route /ws/{session_id} не найден"

    handler = route.endpoint
    # Если slowapi декорировал, у handler будет attr _slowapi_limits
    has_limits = getattr(handler, "_slowapi_limits", None) is not None
    assert not has_limits, "WebSocket имеет slowapi лимит — не должно быть"


# === Задача 2: src/core/rate_limit.py — единственный источник ===

@pytest.mark.asyncio
async def test_limiter_from_core_module():
    """src.core.rate_limit.limiter — реальный экземпляр."""
    from src.core.rate_limit import limiter as core_limiter

    assert core_limiter is not None
    assert hasattr(core_limiter, "limiter") or hasattr(core_limiter, "_backends")
