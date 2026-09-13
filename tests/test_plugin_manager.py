"""Unit tests for PluginManager: tool separation, status and resilience."""

import asyncio
import json
import time

import pytest

from src.plugins.manager import ManagedPlugin, PluginManager, PluginState


class _FakeStdin:
    def __init__(self, proc):
        self._proc = proc

    def write(self, data):
        if self._proc.fail_write:
            raise BrokenPipeError("broken pipe")
        self._proc.writes.append(data)

    def flush(self):
        return None


class _FakeProc:
    def __init__(self, *, alive=True, pid=4242):
        self.alive = alive
        self.pid = pid
        self.killed = False
        self.fail_write = False
        self.writes = []
        self.stdin = _FakeStdin(self)

    def poll(self):
        return None if self.alive else 1

    def kill(self):
        self.killed = True
        self.alive = False

    def terminate(self):
        self.alive = False

    def wait(self, timeout=None):
        return 0


def _add_plugin(manager, name="demo", *, state=PluginState.IDLE, proc=None, **extra):
    options = {"restart_delay_base": 0.0, "restart_delay_max": 0.0, **extra}
    plugin = ManagedPlugin(
        name=name,
        command=["python"],
        working_dir=None,
        proc=proc or _FakeProc(),
        state=state,
        **options,
    )
    manager._plugins[name] = plugin
    return plugin


async def _false(*args, **kwargs):
    return False


async def _true(*args, **kwargs):
    return True


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


@pytest.mark.asyncio
async def test_execute_tool_cancelled_restarts_plugin(monkeypatch):
    manager = PluginManager()

    class FakeProc:
        def __init__(self):
            self.killed = False

        def poll(self):
            return None

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            return 0

    proc = FakeProc()
    manager._plugins["demo"] = ManagedPlugin(
        name="demo", proc=proc, state=PluginState.IDLE
    )

    async def blocking_write(plugin_name, request):
        await asyncio.Event().wait()

    monkeypatch.setattr(manager, "_write_request", blocking_write)

    task = asyncio.create_task(manager.execute_tool("demo", "wg_interface_status"))
    await asyncio.sleep(0.01)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert proc.killed is True
    assert manager._plugins["demo"].state == PluginState.STOPPED


# === discovery ===

def test_discover_plugins_launches_only_valid_dirs(monkeypatch, tmp_path):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "alpha" / "main.py").write_text("", encoding="utf-8")
    (tmp_path / "beta").mkdir()
    (tmp_path / "not-a-dir.txt").write_text("", encoding="utf-8")

    manager = PluginManager()
    registered: list[str] = []
    monkeypatch.setattr(
        manager, "register", lambda name, command, working_dir=None: registered.append(name)
    )

    discovered = manager.discover_plugins(str(tmp_path))

    assert discovered == ["alpha"]
    assert registered == ["alpha"]


def test_discover_plugins_skips_already_registered(monkeypatch, tmp_path):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "alpha" / "main.py").write_text("", encoding="utf-8")

    manager = PluginManager()
    manager._plugins["alpha"] = ManagedPlugin(name="alpha")
    monkeypatch.setattr(manager, "register", lambda *a, **k: pytest.fail("should not register"))

    assert manager.discover_plugins(str(tmp_path)) == []


# === health & status ===

def test_is_healthy_missing_and_stopped():
    manager = PluginManager()
    _add_plugin(manager, "stopped", state=PluginState.STOPPED)

    assert manager.is_healthy("missing") is False
    assert manager.is_healthy("stopped") is False


def test_is_healthy_marks_dead_process_stopped():
    manager = PluginManager()
    plugin = _add_plugin(manager, proc=_FakeProc(alive=False))

    assert manager.is_healthy("demo") is False
    assert plugin.state == PluginState.STOPPED


def test_is_healthy_true_for_alive():
    manager = PluginManager()
    _add_plugin(manager)

    assert manager.is_healthy("demo") is True


def test_get_plugin_status_missing_and_present():
    manager = PluginManager()
    _add_plugin(manager, restart_attempt=2)

    missing = manager.get_plugin_status("nope")
    present = manager.get_plugin_status("demo")

    assert missing == {
        "name": "nope",
        "state": "stopped",
        "pid": None,
        "restart_attempt": 0,
        "is_healthy": False,
    }
    assert present["state"] == PluginState.IDLE.value
    assert present["pid"] == 4242
    assert present["restart_attempt"] == 2
    assert present["is_healthy"] is True


# === restart ===

@pytest.mark.asyncio
async def test_restart_unknown_plugin_returns_false():
    manager = PluginManager()
    assert await manager.restart_plugin("ghost") is False


@pytest.mark.asyncio
async def test_restart_exhausts_attempts():
    manager = PluginManager()
    plugin = _add_plugin(manager, restart_attempt=3, max_restarts=3)

    assert await manager._restart_plugin("demo") is False
    assert plugin.state == PluginState.STOPPED


@pytest.mark.asyncio
async def test_restart_success(monkeypatch):
    manager = PluginManager()
    plugin = _add_plugin(manager, state=PluginState.STOPPED)
    rebuilt: list = []

    monkeypatch.setattr(manager, "_do_register_inplace", lambda *a, **k: None)
    monkeypatch.setattr(manager, "_initialize_plugin", _true)
    monkeypatch.setattr(manager, "get_tools", _true)
    monkeypatch.setattr(manager, "_rebuild_tool_cache", lambda name, tools: rebuilt.append(name))

    assert await manager._restart_plugin("demo") is True
    assert plugin.state == PluginState.IDLE
    assert plugin.restart_attempt == 1
    assert rebuilt == ["demo"]


@pytest.mark.asyncio
async def test_restart_failure_marks_stopped(monkeypatch):
    manager = PluginManager()
    plugin = _add_plugin(manager, state=PluginState.STOPPED)

    monkeypatch.setattr(manager, "_do_register_inplace", lambda *a, **k: None)
    monkeypatch.setattr(manager, "_initialize_plugin", _true)
    monkeypatch.setattr(manager, "get_tools", _true)

    def boom(*args, **kwargs):
        raise RuntimeError("init failed")

    monkeypatch.setattr(manager, "_rebuild_tool_cache", boom)

    assert await manager._restart_plugin("demo") is False
    assert plugin.state == PluginState.STOPPED


# === _write_request ===

@pytest.mark.asyncio
async def test_write_request_unknown_plugin_raises():
    manager = PluginManager()
    with pytest.raises(ValueError):
        await manager._write_request("ghost", {"method": "x"})


@pytest.mark.asyncio
async def test_write_request_busy_raises():
    manager = PluginManager()
    _add_plugin(manager, state=PluginState.BUSY)

    with pytest.raises(RuntimeError, match="busy"):
        await manager._write_request("demo", {"method": "x"})


@pytest.mark.asyncio
async def test_write_request_dead_process_raises():
    manager = PluginManager()
    plugin = _add_plugin(manager, proc=_FakeProc(alive=False))

    with pytest.raises(RuntimeError, match="unavailable"):
        await manager._write_request("demo", {"method": "x"})
    assert plugin.state == PluginState.STOPPED


@pytest.mark.asyncio
async def test_write_request_retries_after_broken_pipe(monkeypatch):
    manager = PluginManager()
    proc = _FakeProc()
    proc.fail_write = True
    _add_plugin(manager, proc=proc)

    async def fake_restart(name):
        proc.fail_write = False
        return True

    monkeypatch.setattr(manager, "_restart_plugin", fake_restart)

    request_id = await manager._write_request("demo", {"method": "x"})

    assert request_id
    assert any("x" in line for line in proc.writes)


@pytest.mark.asyncio
async def test_write_request_raises_when_restart_fails(monkeypatch):
    manager = PluginManager()
    proc = _FakeProc()
    proc.fail_write = True
    _add_plugin(manager, proc=proc)
    monkeypatch.setattr(manager, "_restart_plugin", _false)

    with pytest.raises(RuntimeError, match="restart failed"):
        await manager._write_request("demo", {"method": "x"})


@pytest.mark.asyncio
async def test_write_request_raises_when_retry_fails(monkeypatch):
    manager = PluginManager()
    proc = _FakeProc()
    proc.fail_write = True
    _add_plugin(manager, proc=proc)
    monkeypatch.setattr(manager, "_restart_plugin", _true)

    with pytest.raises(RuntimeError, match="broken input stream"):
        await manager._write_request("demo", {"method": "x"})


# === execute_tool ===

@pytest.mark.asyncio
async def test_execute_tool_success(monkeypatch):
    manager = PluginManager()
    _add_plugin(manager)
    monkeypatch.setattr(manager, "_write_request", _true)
    monkeypatch.setattr(manager, "_read_response", lambda name: {"result": {"ok": True}})

    result = await manager.execute_tool("demo", "wg_interface_status")

    assert result == {"ok": True}
    assert manager._plugins["demo"].state == PluginState.IDLE


@pytest.mark.asyncio
async def test_execute_tool_error_response(monkeypatch):
    manager = PluginManager()
    _add_plugin(manager)
    monkeypatch.setattr(manager, "_write_request", _true)
    monkeypatch.setattr(
        manager, "_read_response", lambda name: {"error": {"message": "boom"}}
    )

    with pytest.raises(RuntimeError, match="boom"):
        await manager.execute_tool("demo", "wg_interface_status")


@pytest.mark.asyncio
async def test_execute_tool_missing_result(monkeypatch):
    manager = PluginManager()
    _add_plugin(manager)
    monkeypatch.setattr(manager, "_write_request", _true)
    monkeypatch.setattr(manager, "_read_response", lambda name: {"result": None})

    with pytest.raises(RuntimeError, match="no result"):
        await manager.execute_tool("demo", "wg_interface_status")


@pytest.mark.asyncio
async def test_execute_tool_timeout_kills_process(monkeypatch):
    manager = PluginManager()
    proc = _FakeProc()
    _add_plugin(manager, proc=proc)
    monkeypatch.setattr(manager, "_write_request", _true)

    async def fake_wait_for(coro, **kwargs):
        coro.close()
        raise TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)

    with pytest.raises(TimeoutError):
        await manager.execute_tool("demo", "wg_interface_status")

    assert proc.killed is True
    assert manager._plugins["demo"].state == PluginState.STOPPED


@pytest.mark.asyncio
async def test_execute_tool_rate_limit(monkeypatch):
    manager = PluginManager()
    _add_plugin(manager)
    manager._tool_calls["wg_interface_status"] = [time.time()] * manager._max_tool_calls_per_minute
    monkeypatch.setattr(manager, "_write_request", _true)

    with pytest.raises(RuntimeError, match="Too many calls"):
        await manager.execute_tool("demo", "wg_interface_status")


@pytest.mark.asyncio
async def test_execute_internal_tool_skips_rate_limit(monkeypatch):
    manager = PluginManager()
    _add_plugin(manager)
    manager._internal_tools["wg_get_client_artifact"] = "demo"
    manager._tool_calls["wg_get_client_artifact"] = [time.time()] * 50
    monkeypatch.setattr(manager, "_write_request", _true)
    monkeypatch.setattr(manager, "_read_response", lambda name: {"result": {"ok": True}})

    result = await manager.execute_tool("demo", "wg_get_client_artifact")

    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_execute_tool_by_name_routes(monkeypatch):
    manager = PluginManager()
    manager._tool_plugins["wg_x"] = "demo"
    captured: dict = {}

    async def fake_execute(plugin_name, tool_name, arguments):
        captured.update(plugin=plugin_name, tool=tool_name, args=arguments)
        return {"ok": True}

    monkeypatch.setattr(manager, "execute_tool", fake_execute)

    assert await manager.execute_tool_by_name("wg_x", {"a": 1}) == {"ok": True}
    assert captured == {"plugin": "demo", "tool": "wg_x", "args": {"a": 1}}


def test_plugin_for_tool_unknown_raises():
    manager = PluginManager()
    with pytest.raises(ValueError):
        manager.plugin_for_tool("wg_missing")


# === get_all_tools ===

@pytest.mark.asyncio
async def test_get_all_tools_skips_failing_plugin(monkeypatch):
    manager = PluginManager()
    _add_plugin(manager, "good")
    _add_plugin(manager, "bad")

    async def fake_get_tools(plugin_name):
        if plugin_name == "bad":
            raise RuntimeError("nope")
        return [{"name": "wg_ok"}, {"name": "wg_internal", "internal": True}]

    monkeypatch.setattr(manager, "get_tools", fake_get_tools)
    monkeypatch.setattr(manager, "_restart_plugin", _false)

    tools = await manager.get_all_tools()

    assert [tool["name"] for tool in tools] == ["wg_ok"]
    assert manager._internal_tools["wg_internal"] == "good"


# === manifest-aware discovery ===


def _manifest(**overrides) -> dict:
    data = {
        "schema_version": 1,
        "id": "demo",
        "name": "Demo",
        "version": "1.0.0",
        "description": "demo",
        "entry": "main.py",
        "protocol": "2024-11-05",
        "platforms": ["linux"],
    }
    data.update(overrides)
    return data


def _add_plugin_dir(tmp_path, name, manifest, *, main=True):
    plugin_dir = tmp_path / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    if main:
        (plugin_dir / "main.py").write_text("", encoding="utf-8")
    if manifest is not None:
        (plugin_dir / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
    return plugin_dir


def test_discover_skips_invalid_manifest(monkeypatch, tmp_path):
    _add_plugin_dir(tmp_path, "demo", _manifest(version="1.0"))
    manager = PluginManager()
    monkeypatch.setattr(manager, "register", lambda *a, **k: pytest.fail("no launch"))

    assert manager.discover_plugins(str(tmp_path)) == []
    assert manager.plugin_names() == []


def test_discover_honours_default_enabled_false(monkeypatch, tmp_path):
    _add_plugin_dir(tmp_path, "demo", _manifest(default_enabled=False))
    manager = PluginManager()
    monkeypatch.setattr(manager, "register", lambda *a, **k: pytest.fail("no launch"))

    assert manager.discover_plugins(str(tmp_path)) == ["demo"]
    assert manager.is_enabled("demo") is False
    assert manager._plugins["demo"].proc is None
    assert manager._plugins["demo"].version == "1.0.0"


def test_discover_records_permission_warnings(monkeypatch, tmp_path):
    _add_plugin_dir(tmp_path, "demo", _manifest())
    manager = PluginManager()

    def fake_register(name, command, working_dir=None):
        manager._plugins[name] = ManagedPlugin(name=name, command=command)

    monkeypatch.setattr(manager, "register", fake_register)
    monkeypatch.setattr(
        "src.plugins.manager.check_permissions", lambda manifest: ["fake warning"]
    )

    assert manager.discover_plugins(str(tmp_path)) == ["demo"]
    plugin = manager._plugins["demo"]
    assert plugin.permission_warnings == ["fake warning"]
    assert plugin.version == "1.0.0"


def test_discover_respects_persisted_disabled(tmp_path):
    _add_plugin_dir(tmp_path, "demo", _manifest())
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"disabled": ["demo"]}), encoding="utf-8")

    manager = PluginManager(state_path=state_path)

    assert manager.discover_plugins(str(tmp_path)) == ["demo"]
    assert manager.is_enabled("demo") is False
    assert manager._plugins["demo"].proc is None


# === enable / disable / reload ===


@pytest.mark.asyncio
async def test_disable_plugin_stops_drops_tools_and_persists(tmp_path):
    state_path = tmp_path / "state.json"
    manager = PluginManager(state_path=state_path)
    plugin = _add_plugin(manager)
    manager._tool_plugins["demo_tool"] = "demo"
    manager._internal_tools["demo_internal"] = "demo"

    assert await manager.disable_plugin("demo") is True

    assert plugin.enabled is False
    assert plugin.state == PluginState.STOPPED
    assert manager._tool_plugins == {}
    assert manager._internal_tools == {}
    assert manager.is_enabled("demo") is False
    assert manager.is_healthy("demo") is False
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"disabled": ["demo"]}


@pytest.mark.asyncio
async def test_disable_unknown_plugin_returns_false():
    manager = PluginManager()
    assert await manager.disable_plugin("ghost") is False


@pytest.mark.asyncio
async def test_enable_plugin_spawns_and_registers_tools(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    manager = PluginManager(state_path=state_path)
    plugin = _add_plugin(manager)
    plugin.enabled = False
    plugin.state = PluginState.STOPPED
    calls: list[str] = []

    monkeypatch.setattr(manager, "_do_register_inplace", lambda *a, **k: calls.append("spawn"))

    async def fake_init(name):
        calls.append("init")

    async def fake_tools(name):
        return [{"name": "demo_ok"}]

    monkeypatch.setattr(manager, "_initialize_plugin", fake_init)
    monkeypatch.setattr(manager, "get_tools", fake_tools)

    assert await manager.enable_plugin("demo") is True

    assert plugin.enabled is True
    assert plugin.state == PluginState.IDLE
    assert manager._tool_plugins == {"demo_ok": "demo"}
    assert calls == ["spawn", "init"]
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"disabled": []}


@pytest.mark.asyncio
async def test_enable_plugin_failure_reverts(monkeypatch, tmp_path):
    manager = PluginManager(state_path=tmp_path / "state.json")
    plugin = _add_plugin(manager)
    plugin.enabled = False
    monkeypatch.setattr(manager, "_do_register_inplace", lambda *a, **k: None)

    async def boom(name):
        raise RuntimeError("nope")

    monkeypatch.setattr(manager, "_initialize_plugin", boom)

    assert await manager.enable_plugin("demo") is False
    assert plugin.enabled is False
    assert plugin.state == PluginState.STOPPED


@pytest.mark.asyncio
async def test_reload_plugin_refreshes_enabled(monkeypatch):
    manager = PluginManager()
    plugin = _add_plugin(manager)
    monkeypatch.setattr(manager, "_do_register_inplace", lambda *a, **k: None)

    async def fake_init(name):
        return None

    async def fake_tools(name):
        return []

    monkeypatch.setattr(manager, "_initialize_plugin", fake_init)
    monkeypatch.setattr(manager, "get_tools", fake_tools)

    assert await manager.reload_plugin("demo") is True
    assert plugin.state == PluginState.IDLE
    assert await manager.reload_plugin("ghost") is False


@pytest.mark.asyncio
async def test_reload_disabled_plugin_returns_false():
    manager = PluginManager()
    plugin = _add_plugin(manager)
    plugin.enabled = False

    assert await manager.reload_plugin("demo") is False


# === disabled behaviour ===


@pytest.mark.asyncio
async def test_restart_disabled_plugin_returns_false():
    manager = PluginManager()
    plugin = _add_plugin(manager)
    plugin.enabled = False

    assert await manager._restart_plugin("demo") is False


@pytest.mark.asyncio
async def test_get_tools_and_execute_tool_raise_when_disabled():
    manager = PluginManager()
    plugin = _add_plugin(manager)
    plugin.enabled = False

    with pytest.raises(RuntimeError, match="disabled"):
        await manager.get_tools("demo")
    with pytest.raises(RuntimeError, match="disabled"):
        await manager.execute_tool("demo", "demo_tool")


@pytest.mark.asyncio
async def test_get_all_tools_skips_disabled(monkeypatch):
    manager = PluginManager()
    _add_plugin(manager, "on")
    off = _add_plugin(manager, "off")
    off.enabled = False

    async def fake_get_tools(name):
        return [{"name": f"{name}_tool"}]

    monkeypatch.setattr(manager, "get_tools", fake_get_tools)

    tools = await manager.get_all_tools()

    assert [tool["name"] for tool in tools] == ["on_tool"]


def test_get_plugin_status_disabled_has_no_pid():
    manager = PluginManager()
    plugin = _add_plugin(manager)
    plugin.enabled = False
    plugin.proc = None

    status = manager.get_plugin_status("demo")

    assert status["pid"] is None
    assert status["is_healthy"] is False

