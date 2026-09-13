"""Unit tests for PluginManager: internal vs public tool separation."""

import pytest

from src.plugins.manager import PluginManager


def test_tool_cache_splits_internal_tools():
    manager = PluginManager()

    manager._rebuild_tool_cache(
        "demo",
        [
            {"name": "wg_interface_status"},
            {"name": "wg_get_client_artifact", "internal": True},
        ],
    )

    assert manager._tool_plugins == {"wg_interface_status": "demo"}
    assert manager._internal_tools == {"wg_get_client_artifact": "demo"}


def test_rebuild_cache_clears_stale_entries():
    manager = PluginManager()
    manager._rebuild_tool_cache("demo", [{"name": "old"}])

    manager._rebuild_tool_cache("demo", [{"name": "new"}])

    assert manager._tool_plugins == {"new": "demo"}


@pytest.mark.asyncio
async def test_execute_internal_tool_routes_to_owner(monkeypatch):
    manager = PluginManager()
    manager._internal_tools["wg_get_client_artifact"] = "LoomWG"
    calls: dict = {}

    async def fake_execute(plugin_name, tool_name, arguments):
        calls.update(plugin=plugin_name, tool=tool_name, args=arguments)
        return {"ok": True}

    monkeypatch.setattr(manager, "execute_tool", fake_execute)

    result = await manager.execute_internal_tool(
        "wg_get_client_artifact", {"name": "peer-abc", "kind": "config"}
    )

    assert result == {"ok": True}
    assert calls == {
        "plugin": "LoomWG",
        "tool": "wg_get_client_artifact",
        "args": {"name": "peer-abc", "kind": "config"},
    }


@pytest.mark.asyncio
async def test_execute_internal_tool_unknown_raises():
    manager = PluginManager()

    async def fake_get_all_tools():
        return []

    manager.get_all_tools = fake_get_all_tools

    with pytest.raises(ValueError):
        await manager.execute_internal_tool("nope")
