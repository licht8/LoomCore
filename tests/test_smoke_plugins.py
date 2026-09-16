"""Plugin-less smoke: discovery is a no-op and the catalog is core-only."""

import pytest
from httpx import ASGITransport, AsyncClient

from src.api import plugins as plugins_api
from src.config import settings
from src.core.core_tools import CORE_TOOLS_SCHEMA
from src.main import app
from src.plugins.manager import PluginManager

_AUTH = {"Authorization": "Bearer secret"}


def _auth(monkeypatch) -> None:
    monkeypatch.setattr(settings, "admin_password_hash", "")
    monkeypatch.setattr(settings, "admin_totp_secret", "")
    monkeypatch.setattr(settings, "admin_token", "secret")


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def test_discover_empty_dir_is_a_noop(tmp_path):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    manager = PluginManager(state_path=tmp_path / "state.json")

    assert manager.discover_plugins(plugins_dir) == []
    assert manager.plugin_names() == []


def test_discover_missing_dir_is_unsupported(tmp_path):
    """A missing directory is not the supported "no plugins" configuration."""
    manager = PluginManager(state_path=tmp_path / "state.json")

    with pytest.raises(FileNotFoundError):
        manager.discover_plugins(tmp_path / "missing")


@pytest.mark.asyncio
async def test_lifespan_primes_plugin_tool_cache(monkeypatch, tmp_path):
    """Startup must index plugin tools so status shows real counts immediately."""
    from src import app as app_module

    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    primed = []

    def fake_discover(_dir):
        return ["demo"]

    async def fake_get_all_tools():
        primed.append(True)
        return [{"name": "demo_tool"}]

    monkeypatch.setattr(settings, "plugins_dir", str(plugins_dir))
    monkeypatch.setattr(app_module.plugin_manager, "discover_plugins", fake_discover)
    monkeypatch.setattr(app_module.plugin_manager, "get_all_tools", fake_get_all_tools)

    async with app_module.lifespan(app):
        pass

    assert primed == [True]


@pytest.mark.asyncio
async def test_tools_endpoint_lists_only_core_without_plugins(monkeypatch):
    _auth(monkeypatch)

    async def no_plugins():
        return []

    monkeypatch.setattr(plugins_api.plugin_manager, "get_all_tools", no_plugins)

    async with _client() as client:
        response = await client.get("/api/tools", headers=_AUTH)

    assert response.status_code == 200
    assert response.json() == {"tools": list(CORE_TOOLS_SCHEMA)}


@pytest.mark.asyncio
async def test_plugins_status_is_empty_without_plugins(monkeypatch):
    _auth(monkeypatch)
    monkeypatch.setattr(plugins_api.plugin_manager, "plugin_names", lambda: [])

    async with _client() as client:
        response = await client.get("/api/plugins/status", headers=_AUTH)

    assert response.status_code == 200
    assert response.json() == {"plugins": {}}
