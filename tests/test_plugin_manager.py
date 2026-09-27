"""Unit tests for PluginManager: tool separation, status and resilience."""

import asyncio
import io
import json
import logging
import threading
import time

import pytest

from src.core.contracts import ToolContractError
from src.plugins.errors import PluginPolicyDenied
from src.plugins.manager import ManagedPlugin, PluginManager, PluginState


@pytest.fixture(autouse=True)
def _no_plugin_allowlist(monkeypatch):
    """Keep the plugin allowlist unset unless a test opts into it."""
    monkeypatch.delenv("LOOMCORE_PLUGINS_ALLOWED", raising=False)


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


# === tool schema + declared risk cache ===


def _schema(name="demo_tool", *, risk=None, required=("a",)):
    tool = {
        "name": name,
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "integer"}},
            "required": list(required),
        },
    }
    if risk is not None:
        tool["risk"] = risk
    return tool


def test_index_tools_caches_schema_and_risk():
    manager = PluginManager()
    manager._rebuild_tool_cache("demo", [_schema(risk="moderate")])

    assert manager.tool_schema("demo_tool")["required"] == ["a"]
    assert manager.tool_risk("demo_tool") == "moderate"


def test_tool_accessors_default_for_unknown():
    manager = PluginManager()
    assert manager.tool_schema("nope") == {}
    assert manager.tool_risk("nope") == ""
    assert manager.report_kind("nope") == ""


def test_report_kind_records_always_and_arg():
    manager = PluginManager()
    manager._rebuild_tool_cache(
        "demo",
        [
            {"name": "report_tool", "report": True},
            {"name": "cmd_tool", "report_arg": "command"},
            {"name": "plain_tool"},
        ],
    )

    assert manager.report_kind("report_tool") == "always"
    assert manager.report_kind("cmd_tool") == "command"
    assert manager.report_kind("plain_tool") == ""


def test_rebuild_cache_clears_stale_reports():
    manager = PluginManager()
    manager._rebuild_tool_cache("demo", [{"name": "report_tool", "report": True}])

    manager._rebuild_tool_cache("demo", [{"name": "new"}])

    assert manager.report_kind("report_tool") == ""
    assert manager._tool_report == {}


def test_index_tools_ignores_unknown_risk_values():
    manager = PluginManager()
    manager._rebuild_tool_cache("demo", [_schema(risk="scary")])

    assert manager.tool_risk("demo_tool") == ""


def test_rebuild_cache_clears_stale_schemas_and_risks():
    manager = PluginManager()
    manager._rebuild_tool_cache("demo", [_schema(risk="safe")])

    manager._rebuild_tool_cache("demo", [{"name": "new"}])

    assert manager.tool_schema("demo_tool") == {}
    assert manager.tool_risk("demo_tool") == ""
    assert manager._tool_schemas == {}
    assert manager._tool_risk == {}


def test_rebuild_cache_rejects_duplicate_tool_names(caplog):
    manager = PluginManager()
    manager._rebuild_tool_cache("alpha", [_schema(name="shared", risk="safe")])

    with caplog.at_level(logging.WARNING, logger="loomcore.plugins"):
        manager._rebuild_tool_cache(
            "beta", [_schema(name="shared", risk="moderate")]
        )

    assert manager._tool_plugins["shared"] == "alpha"
    assert manager.tool_risk("shared") == "safe"
    assert manager.tool_schema("shared")["required"] == ["a"]
    assert any("already" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_execute_tool_by_name_rejects_invalid_args(monkeypatch):
    manager = PluginManager()
    manager._tool_plugins["wg_x"] = "demo"
    manager._tool_schemas["wg_x"] = {
        "type": "object",
        "properties": {"a": {"type": "integer"}},
        "required": ["a"],
    }
    dispatched: list = []

    async def fake_execute(plugin_name, tool_name, arguments):
        dispatched.append(tool_name)
        return {"ok": True}

    monkeypatch.setattr(manager, "execute_tool", fake_execute)

    with pytest.raises(ToolContractError):
        await manager.execute_tool_by_name("wg_x", {"a": "not-an-int"})
    assert dispatched == []

    assert await manager.execute_tool_by_name("wg_x", {"a": 1}) == {"ok": True}
    assert dispatched == ["wg_x"]


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
async def test_execute_tool_cancelled_mid_call_restarts_plugin(monkeypatch):
    manager = PluginManager()
    proc = _FakeProc()
    _add_plugin(manager, proc=proc, state=PluginState.IDLE)

    async def fast_write(plugin_name, request):
        return request["id"]

    release = threading.Event()

    def blocking_read(plugin_name, request_id):
        release.wait(timeout=5)
        return {"id": request_id, "result": {}}

    monkeypatch.setattr(manager, "_write_request", fast_write)
    monkeypatch.setattr(manager, "_read_response", blocking_read)

    task = asyncio.create_task(manager.execute_tool("demo", "wg_interface_status"))
    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert proc.killed is True
    assert manager._plugins["demo"].state == PluginState.STOPPED
    release.set()


@pytest.mark.asyncio
async def test_cancelled_queued_caller_does_not_kill_plugin(monkeypatch):
    # A caller parked on the lock must not kill the process the lock holder is
    # currently reading from when it is cancelled.
    manager = PluginManager()
    proc = _FakeProc()
    _add_plugin(manager, proc=proc, state=PluginState.IDLE)

    async def fast_write(plugin_name, request):
        return request["id"]

    release = threading.Event()

    def blocking_read(plugin_name, request_id):
        release.wait(timeout=5)
        return {"id": request_id, "result": {}}

    monkeypatch.setattr(manager, "_write_request", fast_write)
    monkeypatch.setattr(manager, "_read_response", blocking_read)

    holder = asyncio.create_task(manager.execute_tool("demo", "wg_interface_status"))
    await asyncio.sleep(0.05)  # holder reaches the read and holds the lock

    waiter = asyncio.create_task(manager.execute_tool("demo", "wg_interface_status"))
    await asyncio.sleep(0.05)  # waiter queues on the lock
    waiter.cancel()

    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert proc.killed is False

    release.set()
    await asyncio.wait_for(holder, timeout=5)
    assert proc.killed is False


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
        "enabled": False,
        "version": "",
        "description": "",
        "tools": 0,
        "permission_warnings": [],
    }
    assert present["state"] == PluginState.IDLE.value
    assert present["pid"] == 4242
    assert present["restart_attempt"] == 2
    assert present["is_healthy"] is True


def test_get_plugin_status_reports_published_tool_count():
    manager = PluginManager()
    _add_plugin(manager)
    manager._rebuild_tool_cache(
        "demo",
        [
            {"name": "wg_interface_status"},
            {"name": "wg_get_client_artifact", "internal": True},
        ],
    )

    status = manager.get_plugin_status("demo")

    # All published tools count, external and internal alike.
    assert status["tools"] == 2


def test_get_plugin_status_tool_count_updates_on_rebuild():
    manager = PluginManager()
    _add_plugin(manager)
    manager._rebuild_tool_cache("demo", [{"name": "old"}, {"name": "old2"}])

    manager._rebuild_tool_cache("demo", [{"name": "new"}])

    assert manager.get_plugin_status("demo")["tools"] == 1


def test_index_tools_without_registered_plugin_does_not_raise():
    manager = PluginManager()

    external = manager._index_tools("ghost", [{"name": "tool_a"}])

    assert [tool["name"] for tool in external] == ["tool_a"]
    assert manager._tool_plugins == {"tool_a": "ghost"}


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
    assert plugin.restart_attempt == 0
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


@pytest.mark.asyncio
async def test_restart_failure_kills_spawned_child(monkeypatch):
    manager = PluginManager()
    _add_plugin(manager, state=PluginState.STOPPED)
    killed: list[str] = []

    monkeypatch.setattr(manager, "_do_register_inplace", lambda *a, **k: None)

    async def boom(name):
        raise RuntimeError("nope")

    monkeypatch.setattr(manager, "_initialize_plugin", boom)
    monkeypatch.setattr(manager, "_kill_and_stop", lambda name: killed.append(name))

    assert await manager._restart_plugin("demo") is False
    assert killed == ["demo"]


# === _write_request ===

@pytest.mark.asyncio
async def test_write_request_unknown_plugin_raises():
    manager = PluginManager()
    with pytest.raises(ValueError):
        await manager._write_request("ghost", {"method": "x"})


@pytest.mark.asyncio
async def test_write_request_does_not_reject_busy_state():
    # The BUSY flag is advisory; mutual exclusion now comes from the per-plugin
    # lock, so a stale BUSY state must not make the write fail.
    manager = PluginManager()
    _add_plugin(manager, state=PluginState.BUSY)

    request_id = await manager._write_request("demo", {"method": "x"})

    assert request_id


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


def test_read_response_skips_mismatched_ids():
    manager = PluginManager()
    proc = _FakeProc()
    proc.stdout = io.StringIO(
        json.dumps({"id": "stale", "result": {"wrong": True}})
        + "\n"
        + json.dumps({"method": "MCP notification"})
        + "\n"
        + json.dumps({"id": "want", "result": {"ok": True}})
        + "\n"
    )
    _add_plugin(manager, proc=proc)

    response = manager._read_response("demo", "want")

    assert response == {"id": "want", "result": {"ok": True}}


def test_read_response_skips_non_json_lines():
    manager = PluginManager()
    proc = _FakeProc()
    proc.stdout = io.StringIO(
        "plugin starting up\n"
        + json.dumps([1, 2, 3])
        + "\n"
        + json.dumps({"id": "want", "result": {"ok": True}})
        + "\n"
    )
    _add_plugin(manager, proc=proc)

    response = manager._read_response("demo", "want")

    assert response == {"id": "want", "result": {"ok": True}}


@pytest.mark.asyncio
async def test_get_tools_null_result_is_unavailable(monkeypatch):
    manager = PluginManager()
    _add_plugin(manager)

    async def fake_exchange(plugin_name, request, call_timeout):
        return {"result": None}

    monkeypatch.setattr(manager, "_exchange", fake_exchange)

    with pytest.raises(RuntimeError, match="returned no result"):
        await manager.get_tools("demo")


@pytest.mark.asyncio
async def test_restart_plugin_holds_the_plugin_lock(monkeypatch):
    manager = PluginManager()
    plugin = _add_plugin(manager, state=PluginState.STOPPED)
    seen: dict[str, bool] = {}

    monkeypatch.setattr(manager, "_do_register_inplace", lambda *a, **k: None)

    async def capture(name):
        seen["locked"] = plugin.lock._lock.locked()

    monkeypatch.setattr(manager, "_initialize_plugin", capture)
    monkeypatch.setattr(manager, "get_tools", _true)
    monkeypatch.setattr(manager, "_rebuild_tool_cache", lambda *a, **k: None)

    assert await manager._restart_plugin("demo") is True
    assert seen["locked"] is True


@pytest.mark.asyncio
async def test_exchange_serializes_overlapping_requests(monkeypatch):
    manager = PluginManager()
    _add_plugin(manager)
    events: list[str] = []

    async def slow_write(plugin_name, request):
        events.append(f"write:{request['id']}")
        await asyncio.sleep(0.02)

    def fake_read(plugin_name, request_id):
        events.append(f"read:{request_id}")
        return {"id": request_id, "result": {}}

    monkeypatch.setattr(manager, "_write_request", slow_write)
    monkeypatch.setattr(manager, "_read_response", fake_read)

    await asyncio.gather(
        manager._exchange("demo", {"method": "a"}, call_timeout=5),
        manager._exchange("demo", {"method": "b"}, call_timeout=5),
    )

    # Every write is immediately followed by its own read; the second request
    # cannot start writing until the first has read its response.
    assert events[0].startswith("write:")
    assert events[1] == "read:" + events[0].split(":", 1)[1]
    assert events[2].startswith("write:")
    assert events[3] == "read:" + events[2].split(":", 1)[1]


@pytest.mark.asyncio
async def test_exchange_lock_is_reentrant_for_restart(monkeypatch):
    # A broken write restarts the plugin (initialize + get_tools) while the
    # caller still holds the lock; the lock must be reentrant or this deadlocks.
    manager = PluginManager()
    _add_plugin(manager)
    calls: list[str] = []

    async def writing_that_restarts(plugin_name, request):
        if request.get("method") == "initialize":
            calls.append("initialize")
            return
        calls.append("outer")
        await manager._initialize_plugin(plugin_name)

    monkeypatch.setattr(manager, "_write_request", writing_that_restarts)
    monkeypatch.setattr(
        manager, "_read_response", lambda plugin_name, request_id: {"result": {}}
    )

    await asyncio.wait_for(
        manager._exchange("demo", {"method": "tools/call"}, call_timeout=5),
        timeout=2,
    )

    assert calls == ["outer", "initialize"]


@pytest.mark.asyncio
async def test_initialize_plugin_routes_through_exchange(monkeypatch):
    manager = PluginManager()
    _add_plugin(manager)
    captured: dict = {}

    async def fake_exchange(plugin_name, request, call_timeout):
        captured.update(plugin=plugin_name, request=request, timeout=call_timeout)
        return {"result": {}}

    monkeypatch.setattr(manager, "_exchange", fake_exchange)

    await manager._initialize_plugin("demo")

    assert captured["plugin"] == "demo"
    assert captured["request"]["method"] == "initialize"
    assert captured["timeout"] == 10


# === execute_tool ===

@pytest.mark.asyncio
async def test_execute_tool_success(monkeypatch):
    manager = PluginManager()
    _add_plugin(manager)
    monkeypatch.setattr(manager, "_write_request", _true)
    monkeypatch.setattr(manager, "_read_response", lambda *a, **k: {"result": {"ok": True}})

    result = await manager.execute_tool("demo", "wg_interface_status")

    assert result == {"ok": True}
    assert manager._plugins["demo"].state == PluginState.IDLE


@pytest.mark.asyncio
async def test_execute_tool_error_response(monkeypatch):
    manager = PluginManager()
    _add_plugin(manager)
    monkeypatch.setattr(manager, "_write_request", _true)
    monkeypatch.setattr(
        manager, "_read_response", lambda *a, **k: {"error": {"message": "boom"}}
    )

    with pytest.raises(RuntimeError, match="boom"):
        await manager.execute_tool("demo", "wg_interface_status")


@pytest.mark.asyncio
async def test_execute_tool_missing_result(monkeypatch):
    manager = PluginManager()
    _add_plugin(manager)
    monkeypatch.setattr(manager, "_write_request", _true)
    monkeypatch.setattr(manager, "_read_response", lambda *a, **k: {"result": None})

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
    monkeypatch.setattr(manager, "_read_response", lambda *a, **k: {"result": {"ok": True}})

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


@pytest.mark.asyncio
async def test_get_all_tools_drops_tools_a_plugin_stops_advertising(monkeypatch):
    manager = PluginManager()
    _add_plugin(manager, "demo")
    advertised = [[{"name": "wg_old"}], [{"name": "wg_new"}]]
    calls: list[int] = []

    async def fake_get_tools(plugin_name):
        index = min(len(calls), len(advertised) - 1)
        calls.append(1)
        return advertised[index]

    monkeypatch.setattr(manager, "get_tools", fake_get_tools)

    first = await manager.get_all_tools()
    second = await manager.get_all_tools()

    assert [tool["name"] for tool in first] == ["wg_old"]
    assert [tool["name"] for tool in second] == ["wg_new"]
    assert "wg_old" not in manager._tool_plugins
    assert manager._tool_plugins == {"wg_new": "demo"}


@pytest.mark.asyncio
async def test_get_all_tools_does_not_restart_busy_plugin(monkeypatch):
    manager = PluginManager()
    _add_plugin(manager, "busy", state=PluginState.BUSY)
    restarted: list[str] = []

    async def fake_get_tools(plugin_name):
        raise RuntimeError("busy")

    async def fake_restart(plugin_name):
        restarted.append(plugin_name)
        return True

    monkeypatch.setattr(manager, "get_tools", fake_get_tools)
    monkeypatch.setattr(manager, "_restart_plugin", fake_restart)

    tools = await manager.get_all_tools()

    assert tools == []
    assert restarted == []


# === indexed tool catalog (per-turn routing cache) ===


@pytest.mark.asyncio
async def test_get_all_tools_marks_indexed(monkeypatch):
    manager = PluginManager()
    _add_plugin(manager, "demo")

    async def fake_get_tools(plugin_name):
        return [{"name": "wg_ok"}, {"name": "wg_internal", "internal": True}]

    monkeypatch.setattr(manager, "get_tools", fake_get_tools)

    await manager.get_all_tools()

    assert manager.tools_indexed() is True
    assert [tool["name"] for tool in manager.get_cached_tools()] == ["wg_ok"]


def test_get_cached_tools_excludes_internal_and_disabled():
    manager = PluginManager()
    _add_plugin(manager, "on")
    off = _add_plugin(manager, "off")
    off.enabled = False
    manager._rebuild_tool_cache(
        "on",
        [
            {"name": "wg_ok"},
            {"name": "wg_internal", "internal": True},
        ],
    )
    manager._rebuild_tool_cache("off", [{"name": "off_tool"}])
    manager._tools_indexed = True

    cached = [tool["name"] for tool in manager.get_cached_tools()]

    assert cached == ["wg_ok"]


def test_get_cached_tools_excludes_unhealthy_plugin():
    manager = PluginManager()
    dead = _add_plugin(
        manager, "dead", state=PluginState.STOPPED, proc=_FakeProc(alive=False)
    )
    manager._rebuild_tool_cache("dead", [{"name": "dead_tool"}])
    manager._tools_indexed = True

    assert dead.tools

    assert manager.get_cached_tools() == []


def test_get_cached_tools_skips_malformed_entries():
    manager = PluginManager()
    plugin = _add_plugin(manager, "demo")
    manager._rebuild_tool_cache("demo", [{"name": "wg_ok"}])
    plugin.tools = ["oops", {"name": "wg_ok"}]
    manager._tools_indexed = True

    assert [tool["name"] for tool in manager.get_cached_tools()] == ["wg_ok"]


def test_get_cached_tools_empty_before_indexing():
    manager = PluginManager()
    _add_plugin(manager, "demo")
    manager._rebuild_tool_cache("demo", [{"name": "wg_ok"}])

    assert manager.tools_indexed() is False
    assert manager.get_cached_tools() == []


@pytest.mark.asyncio
async def test_get_tools_for_routing_uses_cache_when_indexed(monkeypatch):
    manager = PluginManager()
    _add_plugin(manager, "demo")

    async def fake_get_tools(plugin_name):
        return [{"name": "wg_ok"}]

    monkeypatch.setattr(manager, "get_tools", fake_get_tools)
    await manager.get_all_tools()

    async def boom():
        raise AssertionError("get_all_tools must not be called once indexed")

    monkeypatch.setattr(manager, "get_all_tools", boom)

    tools = await manager.get_tools_for_routing()

    assert [tool["name"] for tool in tools] == ["wg_ok"]


@pytest.mark.asyncio
async def test_get_tools_for_routing_fetches_when_not_indexed(monkeypatch):
    manager = PluginManager()
    _add_plugin(manager, "demo")

    async def fake_get_tools(plugin_name):
        return [{"name": "wg_ok"}]

    monkeypatch.setattr(manager, "get_tools", fake_get_tools)

    assert manager.tools_indexed() is False
    tools = await manager.get_tools_for_routing()

    assert [tool["name"] for tool in tools] == ["wg_ok"]
    assert manager.tools_indexed() is True


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


def test_discover_surfaces_incompatible_plugin_without_launch(monkeypatch, tmp_path):
    _add_plugin_dir(tmp_path, "demo", _manifest(requires_core=">=9.9.9"))
    manager = PluginManager()
    monkeypatch.setattr(manager, "register", lambda *a, **k: pytest.fail("no launch"))

    assert manager.discover_plugins(str(tmp_path)) == ["demo"]
    plugin = manager._plugins["demo"]
    assert plugin.enabled is False
    assert plugin.state == PluginState.STOPPED
    assert plugin.proc is None
    assert any("requires core >=9.9.9" in w for w in plugin.permission_warnings)


def test_discover_skips_unsupported_protocol(monkeypatch, tmp_path):
    _add_plugin_dir(tmp_path, "demo", _manifest(protocol="1999-01-01"))
    manager = PluginManager()
    monkeypatch.setattr(manager, "register", lambda *a, **k: pytest.fail("no launch"))

    assert manager.discover_plugins(str(tmp_path)) == ["demo"]
    plugin = manager._plugins["demo"]
    assert plugin.enabled is False
    assert any("unsupported protocol" in w for w in plugin.permission_warnings)


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
async def test_enable_failure_kills_spawned_child(monkeypatch, tmp_path):
    manager = PluginManager(state_path=tmp_path / "state.json")
    plugin = _add_plugin(manager)
    plugin.enabled = False
    killed: list[str] = []

    monkeypatch.setattr(manager, "_do_register_inplace", lambda *a, **k: None)

    async def boom(name):
        raise RuntimeError("nope")

    monkeypatch.setattr(manager, "_initialize_plugin", boom)
    monkeypatch.setattr(manager, "_kill_and_stop", lambda name: killed.append(name))

    assert await manager.enable_plugin("demo") is False
    assert killed == ["demo"]


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
async def test_reload_failure_kills_spawned_child(monkeypatch):
    manager = PluginManager()
    _add_plugin(manager)
    killed: list[str] = []

    monkeypatch.setattr(manager, "_do_register_inplace", lambda *a, **k: None)

    async def boom(name):
        raise RuntimeError("nope")

    monkeypatch.setattr(manager, "_initialize_plugin", boom)
    monkeypatch.setattr(manager, "_kill_and_stop", lambda name: killed.append(name))

    assert await manager.reload_plugin("demo") is False
    assert killed == ["demo", "demo"]


@pytest.mark.asyncio
async def test_reload_disabled_plugin_returns_false():
    manager = PluginManager()
    plugin = _add_plugin(manager)
    plugin.enabled = False

    assert await manager.reload_plugin("demo") is False


@pytest.mark.asyncio
async def test_lifecycle_mutations_are_serialized(monkeypatch):
    manager = PluginManager()
    plugin = _add_plugin(manager, state=PluginState.STOPPED, enabled=False)
    killed: list[str] = []
    init_started = asyncio.Event()
    release_init = asyncio.Event()

    monkeypatch.setattr(manager, "_do_register_inplace", lambda *a, **k: None)

    async def fake_init(name):
        init_started.set()
        await release_init.wait()

    async def fake_tools(name):
        return []

    monkeypatch.setattr(manager, "_initialize_plugin", fake_init)
    monkeypatch.setattr(manager, "get_tools", fake_tools)
    monkeypatch.setattr(manager, "_rebuild_tool_cache", lambda *a, **k: None)
    monkeypatch.setattr(manager, "_kill_and_stop", lambda name: killed.append(name))

    enable = asyncio.create_task(manager.enable_plugin("demo"))
    await init_started.wait()
    disable = asyncio.create_task(manager.disable_plugin("demo"))
    await asyncio.sleep(0)
    assert killed == []

    release_init.set()
    assert await asyncio.gather(enable, disable) == [True, True]
    assert killed == ["demo"]
    assert plugin.enabled is False


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
    assert status["enabled"] is False


def test_drain_stderr_logs_plugin_output(caplog):
    manager = PluginManager()

    class _ProcWithStderr:
        def __init__(self, text):
            self.stderr = io.StringIO(text)

    with caplog.at_level("INFO", logger="loomcore.plugins.demo"):
        manager._drain_stderr(
            "demo",
            _ProcWithStderr(
                "hello from plugin\n"
                "WARNING LoomWG.wg: command exited 1: wg: permission denied\n"
            ),
        )
        for _ in range(200):
            if len(caplog.records) >= 2:
                break
            time.sleep(0.01)

    messages = [record.getMessage() for record in caplog.records]
    assert "hello from plugin" in messages
    warning = next(r for r in caplog.records if "permission denied" in r.getMessage())
    assert warning.levelno == logging.WARNING


# === plugin allowlist (LOOMCORE_PLUGINS_ALLOWED) ===


def test_allowed_plugins_parses_commas_and_spaces(monkeypatch):
    from src.plugins.manager import allowed_plugins

    monkeypatch.setenv("LOOMCORE_PLUGINS_ALLOWED", "LoomWG, LoomOps")
    assert allowed_plugins() == {"LoomWG", "LoomOps"}

    monkeypatch.setenv("LOOMCORE_PLUGINS_ALLOWED", "LoomWG LoomOps")
    assert allowed_plugins() == {"LoomWG", "LoomOps"}

    monkeypatch.setenv("LOOMCORE_PLUGINS_ALLOWED", "")
    assert allowed_plugins() == set()


def test_discover_blocks_plugin_not_in_allowlist(monkeypatch, tmp_path):
    _add_plugin_dir(tmp_path, "demo", _manifest())
    monkeypatch.setenv("LOOMCORE_PLUGINS_ALLOWED", "LoomOps")
    manager = PluginManager()
    monkeypatch.setattr(manager, "register", lambda *a, **k: pytest.fail("no launch"))

    assert manager.discover_plugins(str(tmp_path)) == ["demo"]
    plugin = manager._plugins["demo"]
    assert plugin.enabled is False
    assert plugin.state == PluginState.STOPPED
    assert plugin.proc is None
    assert "plugin is not in LOOMCORE_PLUGINS_ALLOWED" in plugin.permission_warnings


def test_discover_launches_allowlisted_plugin(monkeypatch, tmp_path):
    _add_plugin_dir(tmp_path, "demo", _manifest())
    monkeypatch.setenv("LOOMCORE_PLUGINS_ALLOWED", "demo")
    manager = PluginManager()
    registered: list[str] = []
    monkeypatch.setattr(
        manager,
        "register",
        lambda name, command, working_dir=None: registered.append(name),
    )

    assert manager.discover_plugins(str(tmp_path)) == ["demo"]
    assert registered == ["demo"]


@pytest.mark.asyncio
async def test_enable_refuses_plugin_not_in_allowlist(monkeypatch):
    monkeypatch.setenv("LOOMCORE_PLUGINS_ALLOWED", "LoomOps")
    manager = PluginManager()
    plugin = _add_plugin(manager)
    plugin.enabled = False
    plugin.state = PluginState.STOPPED
    monkeypatch.setattr(
        manager, "_do_register_inplace", lambda *a, **k: pytest.fail("no spawn")
    )

    with pytest.raises(PluginPolicyDenied):
        await manager.enable_plugin("demo")
    assert plugin.enabled is False
    assert plugin.state == PluginState.STOPPED


@pytest.mark.asyncio
async def test_enable_allows_plugin_in_allowlist(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOMCORE_PLUGINS_ALLOWED", "demo")
    manager = PluginManager(state_path=tmp_path / "state.json")
    plugin = _add_plugin(manager)
    plugin.enabled = False
    plugin.state = PluginState.STOPPED
    monkeypatch.setattr(manager, "_do_register_inplace", lambda *a, **k: None)

    async def fake_init(name):
        return None

    async def fake_tools(name):
        return [{"name": "demo_ok"}]

    monkeypatch.setattr(manager, "_initialize_plugin", fake_init)
    monkeypatch.setattr(manager, "get_tools", fake_tools)

    assert await manager.enable_plugin("demo") is True
    assert plugin.enabled is True

