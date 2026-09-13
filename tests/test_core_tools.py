"""Tests for the core meta-tools (health, plugins, capabilities, lifecycle)."""

import pytest

from src.core import core_tools


class _FakeManager:
    def __init__(self, *, enabled=True, healthy=True, names=("LoomOps",)):
        self.names = list(names)
        self.enabled = enabled
        self.healthy = healthy
        self.calls: list[tuple[str, str]] = []

    def plugin_names(self):
        return list(self.names)

    def is_enabled(self, name):
        return self.enabled

    def is_healthy(self, name):
        return self.healthy

    def get_plugin_status(self, name):
        return {
            "name": name,
            "state": "idle",
            "pid": 4321,
            "restart_attempt": 0,
            "is_healthy": self.healthy,
        }

    def plugin_version(self, name):
        return "0.1.0"

    def plugin_manifest(self, name):
        return {"id": name, "permissions": {"network_mode": "host"}}

    def permission_warnings(self, name):
        return ["fake warning"]

    async def get_tools(self, name):
        return [{"name": "ops_disk_usage", "description": "df"}]

    async def enable_plugin(self, name):
        self.calls.append(("enable", name))
        return True

    async def disable_plugin(self, name):
        self.calls.append(("disable", name))
        return True

    async def reload_plugin(self, name):
        self.calls.append(("reload", name))
        return True


@pytest.fixture
def fake_manager(monkeypatch):
    manager = _FakeManager()
    monkeypatch.setattr(core_tools, "plugin_manager", manager)
    return manager


def test_schema_and_names():
    names = [tool["name"] for tool in core_tools.CORE_TOOLS_SCHEMA]
    assert len(names) == len(set(names)) == 8
    assert frozenset(names) == core_tools.CORE_TOOL_NAMES
    for tool in core_tools.CORE_TOOLS_SCHEMA:
        assert tool["description"]
        assert tool["inputSchema"]["type"] == "object"


@pytest.mark.asyncio
async def test_dispatch_unknown_tool_raises(fake_manager):
    with pytest.raises(ValueError):
        await core_tools.dispatch_core_tool("core_nope", {})


@pytest.mark.asyncio
async def test_core_health_shape(fake_manager):
    result = await core_tools.dispatch_core_tool("core_health")
    assert result["content"][0]["type"] == "text"
    data = result["data"]
    assert data["status"] == "ok"
    assert data["uptime_seconds"] >= 0
    assert data["plugin_counts"] == {"total": 1, "enabled": 1, "healthy": 1}
    assert "Plugins enabled: LoomOps." in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_core_list_plugins_passes_question(fake_manager):
    result = await core_tools.dispatch_core_tool("core_list_plugins", {})
    assert result["data"]["plugins"] == [
        {
            "id": "LoomOps",
            "version": "0.1.0",
            "enabled": True,
            "state": "idle",
            "healthy": True,
        }
    ]
    text = result["content"][0]["text"]
    assert "1 plugins discovered:" in text
    assert "- LoomOps v0.1.0 — enabled, healthy (idle)" in text


@pytest.mark.asyncio
async def test_core_plugin_status_includes_warnings(fake_manager):
    result = await core_tools.dispatch_core_tool(
        "core_plugin_status", {"plugin": "LoomOps"}
    )
    data = result["data"]
    assert data["plugin"] == "LoomOps"
    assert data["version"] == "0.1.0"
    assert data["warnings"] == ["fake warning"]
    assert data["pid"] == 4321


@pytest.mark.asyncio
async def test_core_plugin_status_requires_plugin(fake_manager):
    with pytest.raises(ValueError):
        await core_tools.dispatch_core_tool("core_plugin_status", {})
    with pytest.raises(ValueError):
        await core_tools.dispatch_core_tool("core_plugin_status", {"plugin": "Nope"})


@pytest.mark.asyncio
async def test_core_list_tools_includes_core_and_plugin(fake_manager):
    result = await core_tools.dispatch_core_tool("core_list_tools", {})
    data = result["data"]
    assert len(data["core_tools"]) == 8
    assert data["plugin_tools"] == [
        {"name": "ops_disk_usage", "plugin": "LoomOps"}
    ]
    text = result["content"][0]["text"]
    assert text.startswith("8 core tools: core_health, core_list_plugins")
    assert "LoomOps (1): ops_disk_usage" in text


@pytest.mark.asyncio
async def test_core_list_tools_skips_disabled(monkeypatch):
    manager = _FakeManager(enabled=False)
    monkeypatch.setattr(core_tools, "plugin_manager", manager)
    result = await core_tools.dispatch_core_tool("core_list_tools", {})
    assert result["data"]["plugin_tools"] == []
    assert "LoomOps (disabled)" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_core_capabilities_reports_runtime(monkeypatch, fake_manager):
    monkeypatch.setattr(core_tools, "effective_capabilities", lambda: 1 << 12)
    monkeypatch.setattr(core_tools, "host_network_looks_active", lambda: True)

    result = await core_tools.dispatch_core_tool("core_capabilities", {})

    data = result["data"]
    assert data["capabilities"] == {
        "NET_ADMIN": True,
        "NET_RAW": False,
        "SYS_MODULE": False,
        "SYSLOG": False,
    }
    assert data["host_networked"] is True
    assert data["plugins"][0]["id"] == "LoomOps"
    assert data["plugins"][0]["warnings"] == ["fake warning"]


@pytest.mark.asyncio
async def test_core_lifecycle_tools_call_manager(fake_manager):
    enabled = await core_tools.dispatch_core_tool(
        "core_enable_plugin", {"plugin": "LoomOps"}
    )
    disabled = await core_tools.dispatch_core_tool(
        "core_disable_plugin", {"plugin": "LoomOps"}
    )
    reloaded = await core_tools.dispatch_core_tool(
        "core_reload_plugin", {"plugin": "LoomOps"}
    )

    assert enabled["data"]["enabled"] is True
    assert disabled["data"]["enabled"] is False
    assert reloaded["data"]["enabled"] is True
    assert fake_manager.calls == [
        ("enable", "LoomOps"),
        ("disable", "LoomOps"),
        ("reload", "LoomOps"),
    ]


@pytest.mark.asyncio
async def test_core_lifecycle_failure_raises(monkeypatch, fake_manager):
    async def boom(name):
        return False

    monkeypatch.setattr(fake_manager, "enable_plugin", boom)
    with pytest.raises(RuntimeError):
        await core_tools.dispatch_core_tool(
            "core_enable_plugin", {"plugin": "LoomOps"}
        )
