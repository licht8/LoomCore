"""Tests for the REST chat endpoint error handling."""

import logging

import pytest
from httpx import ASGITransport, AsyncClient

from src.api import chat as chat_api
from src.config import settings
from src.llm.client import LlmError
from src.llm.exceptions import LlmBudgetExceeded
from src.main import app


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


_AUTH = {"Authorization": "Bearer secret"}


def _auth(monkeypatch):
    monkeypatch.setattr(settings, "admin_password_hash", "")
    monkeypatch.setattr(settings, "admin_totp_secret", "")
    monkeypatch.setattr(settings, "admin_token", "secret")


@pytest.mark.asyncio
async def test_chat_returns_router_result(monkeypatch):
    _auth(monkeypatch)

    async def fake_route(message, session_id):
        return {"type": "text", "content": f"echo:{message}"}

    monkeypatch.setattr(chat_api.airouter, "route", fake_route)

    async with _client() as client:
        response = await client.post(
            "/api/chat",
            json={"session_id": "s1", "message": "hi"},
            headers=_AUTH,
        )

    assert response.status_code == 200
    assert response.json() == {"type": "text", "content": "echo:hi"}


@pytest.mark.asyncio
async def test_chat_maps_llm_error_to_504(monkeypatch):
    _auth(monkeypatch)

    async def boom(message, session_id):
        raise LlmError("upstream timeout")

    monkeypatch.setattr(chat_api.airouter, "route", boom)

    async with _client() as client:
        response = await client.post(
            "/api/chat",
            json={"session_id": "s1", "message": "hi"},
            headers=_AUTH,
        )

    assert response.status_code == 504
    assert response.json() == {"error": "upstream timeout"}


@pytest.mark.asyncio
async def test_chat_maps_budget_exceeded_to_429(monkeypatch):
    _auth(monkeypatch)

    async def boom(message, session_id):
        raise LlmBudgetExceeded("daily LLM budget exhausted")

    monkeypatch.setattr(chat_api.airouter, "route", boom)

    async with _client() as client:
        response = await client.post(
            "/api/chat",
            json={"session_id": "s1", "message": "hi"},
            headers=_AUTH,
        )

    assert response.status_code == 429
    assert response.json() == {"error": "daily LLM budget exhausted"}


@pytest.mark.asyncio
async def test_chat_maps_unexpected_error_to_500(monkeypatch, caplog):
    _auth(monkeypatch)

    async def boom(message, session_id):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(chat_api.airouter, "route", boom)

    with caplog.at_level(logging.ERROR, logger="loomcore.api.chat"):
        async with _client() as client:
            response = await client.post(
                "/api/chat",
                json={"session_id": "s1", "message": "hi"},
                headers=_AUTH,
            )

    assert response.status_code == 500
    assert response.json() == {"error": "Internal server error"}
    assert "kaboom" not in response.text
    assert "chat request failed (session=s1)" in caplog.text
    assert "kaboom" in caplog.text


@pytest.mark.asyncio
async def test_chat_requires_auth(monkeypatch):
    _auth(monkeypatch)

    async with _client() as client:
        response = await client.post(
            "/api/chat",
            json={"session_id": "s1", "message": "hi"},
        )

    assert response.status_code == 401
