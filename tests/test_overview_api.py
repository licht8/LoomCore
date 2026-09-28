"""Tests for the curated read-only overview aggregate (no DB, no auth)."""

import asyncio

import pytest

from src.api import overview as overview_api


@pytest.fixture(autouse=True)
def _disable_limiter(monkeypatch):
    from src.core.rate_limit import limiter

    monkeypatch.setattr(limiter, "enabled", False)


def _ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "data": {}}


class _FakeManager:
    def __init__(self, results: dict, fail: tuple = (), *, healthy: bool = True) -> None:
        self._results = results
        self._fail = set(fail)
        self._healthy = healthy
        self.calls: list[str] = []

    def plugin_names(self):
        return ["LoomOps"]

    def plugin_for_tool(self, tool_name):
        return "LoomOps"

    def is_healthy(self, plugin_name):
        return self._healthy

    def get_plugin_status(self, name):
        return {"name": name, "state": "running", "enabled": True}

    async def execute_tool_by_name(self, tool_name, arguments=None):
        self.calls.append(tool_name)
        if tool_name in self._fail:
            raise RuntimeError("tool unavailable")
        return self._results.get(tool_name, _ok(tool_name))


def _patch_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        overview_api, "require_admin_token_from_request", lambda *a, **k: None
    )


def _patch_tools(
    monkeypatch: pytest.MonkeyPatch,
    *,
    core: dict | None = None,
    core_raises: bool = False,
    manager_results: dict | None = None,
    manager_fail: tuple = (),
    healthy: bool = True,
) -> _FakeManager:
    async def fake_core(tool_name, arguments=None, *, approved=False):
        if core_raises:
            raise RuntimeError("core unavailable")
        return core or _ok("core ok")

    monkeypatch.setattr(overview_api, "dispatch_core_tool", fake_core)
    manager = _FakeManager(manager_results or {}, manager_fail, healthy=healthy)
    monkeypatch.setattr(overview_api, "plugin_manager", manager)
    return manager


@pytest.mark.asyncio
async def test_overview_populates_sections(monkeypatch):
    _patch_auth(monkeypatch)
    _patch_tools(
        monkeypatch,
        core=_ok("healthy"),
        manager_results={
            "ops_disk_usage": _ok("disk 42%"),
            "ops_system_metrics": _ok("cpu 3%"),
            "ops_failed_units": _ok("no failed units"),
            "wg_list_interfaces": _ok("wg0 up"),
        },
    )

    result = await overview_api.get_overview(request=None)

    assert result["core"]["content"][0]["text"] == "healthy"
    assert result["host"]["disk"]["content"][0]["text"] == "disk 42%"
    assert result["host"]["metrics"]["content"][0]["text"] == "cpu 3%"
    assert result["host"]["failed_units"]["content"][0]["text"] == "no failed units"
    assert result["wireguard"]["content"][0]["text"] == "wg0 up"
    assert result["plugins"][0]["name"] == "LoomOps"


@pytest.mark.asyncio
async def test_overview_core_failure_is_null(monkeypatch):
    _patch_auth(monkeypatch)
    _patch_tools(monkeypatch, core_raises=True)

    result = await overview_api.get_overview(request=None)

    assert result["core"] is None
    assert result["plugins"]  # plugins always present


@pytest.mark.asyncio
async def test_overview_tool_failure_is_null_without_raising(monkeypatch):
    _patch_auth(monkeypatch)
    _patch_tools(
        monkeypatch,
        manager_results={"ops_system_metrics": _ok("cpu 3%")},
        manager_fail=("ops_disk_usage", "wg_list_interfaces"),
    )

    result = await overview_api.get_overview(request=None)

    assert result["host"]["disk"] is None
    assert result["wireguard"] is None
    assert result["host"]["metrics"]["content"][0]["text"] == "cpu 3%"


@pytest.mark.asyncio
async def test_overview_times_out_a_hung_tool(monkeypatch):
    _patch_auth(monkeypatch)
    manager = _patch_tools(monkeypatch)
    monkeypatch.setattr(overview_api, "OVERVIEW_TOOL_TIMEOUT_SECONDS", 0.05)

    async def hang(tool_name, arguments=None):
        manager.calls.append(tool_name)
        await asyncio.sleep(1)
        return _ok(tool_name)

    monkeypatch.setattr(manager, "execute_tool_by_name", hang)

    result = await overview_api.get_overview(request=None)

    assert result["core"]["content"][0]["text"] == "core ok"
    assert result["host"]["disk"] is None
    assert result["host"]["metrics"] is None
    assert result["wireguard"] is None


@pytest.mark.asyncio
async def test_overview_skips_unhealthy_plugin(monkeypatch):
    _patch_auth(monkeypatch)
    manager = _patch_tools(monkeypatch, healthy=False)

    result = await overview_api.get_overview(request=None)

    assert manager.calls == []
    assert result["host"]["disk"] is None
    assert result["wireguard"] is None
    assert result["core"]["content"][0]["text"] == "core ok"


@pytest.mark.asyncio
async def test_overview_requires_admin(monkeypatch):
    def deny(request, authorization):
        raise PermissionError("nope")

    monkeypatch.setattr(overview_api, "require_admin_token_from_request", deny)

    with pytest.raises(PermissionError):
        await overview_api.get_overview(request=None)
