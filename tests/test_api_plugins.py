"""Tests for the plugin status and tools routes."""

import pytest
from httpx import ASGITransport, AsyncClient

from src.api import plugins as plugins_api
from src.config import settings
from src.main import app


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _auth(monkeypatch):
    monkeypatch.setattr(settings, "admin_password_hash", "")
    monkeypatch.setattr(settings, "admin_totp_secret", "")
    monkeypatch.setattr(settings, "admin_token", "secret")


@pytest.mark.asyncio
async def test_plugins_status_shape(monkeypatch):
    _auth(monkeypatch)
    monkeypatch.setattr(plugins_api.plugin_manager, "plugin_names", lambda: ["LoomWG"])
    monkeypatch.setattr(
        plugins_api.plugin_manager,
        "get_plugin_status",
        lambda name: {"name": name, "state": "idle"},
    )

    async with _client() as client:
        response = await client.get("/api/plugins/status?access_token=secret")

    assert response.status_code == 200
    assert response.json() == {"plugins": {"LoomWG": {"name": "LoomWG", "state": "idle"}}}


@pytest.mark.asyncio
async def test_tools_endpoint_returns_tools(monkeypatch):
    _auth(monkeypatch)

    async def fake_tools():
        return [{"name": "wg_interface_status"}]

    monkeypatch.setattr(plugins_api.plugin_manager, "get_all_tools", fake_tools)

    async with _client() as client:
        response = await client.get("/api/tools?access_token=secret")

    assert response.status_code == 200
    assert response.json() == {"tools": [{"name": "wg_interface_status"}]}


@pytest.mark.asyncio
async def test_tools_endpoint_reports_error(monkeypatch):
    _auth(monkeypatch)

    async def boom():
        raise RuntimeError("plugin down")

    monkeypatch.setattr(plugins_api.plugin_manager, "get_all_tools", boom)

    async with _client() as client:
        response = await client.get("/api/tools?access_token=secret")

    assert response.status_code == 200
    assert response.json() == {"error": "plugin down"}
