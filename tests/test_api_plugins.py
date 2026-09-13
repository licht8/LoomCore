"""Tests for the plugin status and tools routes."""

import pytest
from httpx import ASGITransport, AsyncClient

from src.api import plugins as plugins_api
from src.config import settings
from src.core.core_tools import CORE_TOOLS_SCHEMA
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

    tools = response.json()["tools"]
    assert response.status_code == 200
    assert tools[: len(CORE_TOOLS_SCHEMA)] == CORE_TOOLS_SCHEMA
    assert tools[-1] == {"name": "wg_interface_status"}


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


def _patch_lifecycle(monkeypatch, plugin_id="LoomOps", *, ok=True):
    monkeypatch.setattr(plugins_api.plugin_manager, "plugin_names", lambda: [plugin_id])
    calls: list[str] = []

    def _make(action):
        async def _call(name):
            calls.append(f"{action}:{name}")
            return ok

        return _call

    monkeypatch.setattr(plugins_api.plugin_manager, "enable_plugin", _make("enable"))
    monkeypatch.setattr(plugins_api.plugin_manager, "disable_plugin", _make("disable"))
    monkeypatch.setattr(plugins_api.plugin_manager, "reload_plugin", _make("reload"))
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "enabled"),
    [("enable", True), ("disable", False), ("reload", True)],
)
async def test_plugin_lifecycle_routes(monkeypatch, action, enabled):
    _auth(monkeypatch)
    calls = _patch_lifecycle(monkeypatch)

    async with _client() as client:
        response = await client.post(
            f"/api/plugins/LoomOps/{action}?access_token=secret"
        )

    assert response.status_code == 200
    assert response.json() == {"plugin": "LoomOps", "enabled": enabled}
    assert calls == [f"{action}:LoomOps"]


@pytest.mark.asyncio
async def test_plugin_lifecycle_unknown_plugin_404(monkeypatch):
    _auth(monkeypatch)
    _patch_lifecycle(monkeypatch, plugin_id="Other")

    async with _client() as client:
        response = await client.post("/api/plugins/LoomOps/enable?access_token=secret")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_plugin_lifecycle_requires_admin(monkeypatch):
    monkeypatch.setattr(settings, "admin_password_hash", "")
    monkeypatch.setattr(settings, "admin_totp_secret", "")
    monkeypatch.setattr(settings, "admin_token", "secret")

    async with _client() as client:
        response = await client.post("/api/plugins/LoomOps/enable?access_token=wrong")

    assert response.status_code == 401
