"""Core smoke test: the app responds without a DB or plugins."""

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app


@pytest.mark.asyncio
async def test_health_endpoint():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_health_endpoint_is_not_rate_limited():
    """Liveness probes must never receive a 429 (a probe is not abuse)."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        responses = [await client.get("/health") for _ in range(70)]

    assert {response.status_code for response in responses} == {200}


@pytest.mark.asyncio
async def test_admin_routes_require_auth():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/plugins/status")

    # 401 — token/JWT configured but not sent; 503 — access not configured
    assert response.status_code in {401, 503}
