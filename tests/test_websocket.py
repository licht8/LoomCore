"""Unit tests for the plan-driven WebSocket orchestration."""

import asyncio
import copy

import pytest
from fastapi import WebSocketDisconnect

from src.core.agent import build_plan
from src.interfaces import websocket as ws_module
from src.llm.client import LlmError


@pytest.fixture(autouse=True)
def _reset_ws_message_limits():
    """The WS message limiter is module-global; reset it around every test."""
    ws_module.reset_ws_message_limits()
    yield
    ws_module.reset_ws_message_limits()


@pytest.fixture(autouse=True)
def _stub_mutation_audit(monkeypatch):
    """Keep the central audit off the database; tests that target it override."""

    async def _noop(**_kwargs):
        return None

    monkeypatch.setattr(ws_module, "record_mutation_isolated", _noop)


class FakeWebSocket:
    """Minimal WebSocket double that replays a scripted incoming list."""

    def __init__(self, incoming, *, delay=0.0):
        self._incoming = list(incoming)
        self._delay = delay
        self.sent = []
        self.closed = False
        self.allow_disconnect = asyncio.Event()

    async def accept(self):
        return None

    async def send_json(self, payload):
        # A real socket serializes immediately; snapshot so later mutations of
        # the shared plan dict do not rewrite already-sent events.
        self.sent.append(copy.deepcopy(payload))

    async def close(self):
        self.closed = True

    async def receive_json(self):
        await asyncio.sleep(self._delay)
        if self._incoming:
            return self._incoming.pop(0)
        await self.allow_disconnect.wait()
        raise WebSocketDisconnect()


class DummyContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc_info):
        return False


def _patch_db(monkeypatch):
    monkeypatch.setattr(ws_module, "async_session_factory", DummyContext)

    async def fake_ensure(db, session_id):
        return session_id

    async def fake_add_assistant(db, session_id, content, tool_calls=None):
        return None

    monkeypatch.setattr(ws_module, "ensure_session", fake_ensure)
    monkeypatch.setattr(ws_module, "add_assistant_message", fake_add_assistant)


def _patch_plan_runtime(
    monkeypatch,
    *,
    saved_tools=None,
    saved_plans=None,
    summarize="Summary",
    known=None,
):
    """Patch the plan executor, persistence and summary layers."""
    saved_tools = saved_tools if saved_tools is not None else []
    saved_plans = saved_plans if saved_plans is not None else []
    known = known or {"core_health", "core_disable_plugin", "wg_interface_status"}

    async def fake_execute(tool_name, arguments=None):
        return {
            "content": [{"type": "text", "text": f"{tool_name} ok"}],
            "data": {},
        }

    async def fake_save_tool(session_id, tool_name, arguments, content, **kwargs):
        saved_tools.append({"tool": tool_name, "content": content, **kwargs})

    async def fake_save_plan(session_id, plan):
        saved_plans.append(plan)

    async def fake_summarize(user_text, plan, observations):
        return summarize

    async def fake_known():
        return set(known)

    monkeypatch.setattr(ws_module, "_execute_tool", fake_execute)
    monkeypatch.setattr(ws_module, "_save_tool_message", fake_save_tool)
    monkeypatch.setattr(ws_module, "_save_plan_message", fake_save_plan)
    monkeypatch.setattr(
        ws_module.airouter_with_plugins, "summarize_plan", fake_summarize
    )
    monkeypatch.setattr(ws_module.airouter_with_plugins, "known_tool_names", fake_known)
    return saved_tools, saved_plans


def _collector():
    sent = []

    async def send(payload):
        sent.append(copy.deepcopy(payload))

    return sent, send


# --------------------------------------------------------------------------- #
# _execute_tool routing
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_execute_tool_routes_core_names_to_dispatch(monkeypatch):
    captured = {}

    async def fake_dispatch(name, arguments):
        captured["args"] = (name, arguments)
        return {"ok": True}

    monkeypatch.setattr(ws_module, "dispatch_core_tool", fake_dispatch)

    result = await ws_module._execute_tool("core_health", {})

    assert captured["args"] == ("core_health", {})
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_execute_tool_routes_plugin_names_to_manager(monkeypatch):
    captured = {}

    async def fake_execute(name, arguments):
        captured["args"] = (name, arguments)
        return {"ok": True}

    monkeypatch.setattr(
        ws_module.plugin_manager, "execute_tool_by_name", fake_execute
    )

    result = await ws_module._execute_tool("wg_interface_status", {"interface": "wg0"})

    assert captured["args"] == ("wg_interface_status", {"interface": "wg0"})
    assert result == {"ok": True}


# --------------------------------------------------------------------------- #
# _process_user_message
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_process_user_message_text_response(monkeypatch):
    sent, send = _collector()

    async def fake_route(user_text, session_id):
        return {"type": "text", "content": "hello"}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    await ws_module._process_user_message(send, "sess", "hi")

    assert sent[0] == {"type": "thinking", "status": "processing"}
    assert sent[-1] == {"type": "response", "content": "hello"}


@pytest.mark.asyncio
async def test_process_user_message_reports_llm_error(monkeypatch):
    sent, send = _collector()

    async def fake_route(user_text, session_id):
        raise LlmError("boom")

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    await ws_module._process_user_message(send, "sess", "hi")

    assert sent[-1] == {"type": "error", "error": "boom"}


@pytest.mark.asyncio
async def test_process_user_message_auto_runs_direct_safe(monkeypatch):
    sent, send = _collector()
    plan = build_plan(
        {
            "goal": "check",
            "steps": [{"tool": "core_health", "args": {}, "why": "health"}],
        }
    )

    async def fake_route(user_text, session_id):
        return {"type": "plan", "plan": plan}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)
    saved_tools, saved_plans = _patch_plan_runtime(monkeypatch)
    tracker = {"tool": None, "plan": None}
    pending = {"plan": None, "user_text": ""}

    await ws_module._process_user_message(send, "sess", "check", tracker, pending)

    types = [payload["type"] for payload in sent]
    assert "plan_proposed" not in types
    assert "plan_done" not in types
    assert types.count("tool_call") == 1
    assert "tool_response" in types
    assert types[-1] == "response"
    assert sent[-1]["content"] == "Summary"
    assert saved_tools[0]["status"] == "success"
    assert saved_plans == []
    assert pending["plan"] is None
    assert tracker["plan"] is None


@pytest.mark.asyncio
async def test_direct_action_uses_llm_summary(monkeypatch):
    sent, send = _collector()
    plan = build_plan(
        {
            "goal": "check",
            "steps": [{"tool": "core_health", "args": {}, "why": "health"}],
        }
    )
    calls = []

    async def fake_route(user_text, session_id):
        return {"type": "plan", "plan": plan}

    async def fake_summary(user_text, plan, observations):
        calls.append((user_text, observations))
        return "All good — the core is healthy."

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)
    _patch_plan_runtime(monkeypatch)
    monkeypatch.setattr(ws_module.airouter_with_plugins, "summarize_plan", fake_summary)

    tracker = {"tool": None, "plan": None}
    pending = {"plan": None, "user_text": ""}
    await ws_module._process_user_message(send, "sess", "check", tracker, pending)

    assert calls and calls[0][0] == "check"
    assert sent[-1]["type"] == "response"
    assert sent[-1]["content"] == "All good — the core is healthy."


@pytest.mark.asyncio
async def test_summary_replaces_a_raw_table(monkeypatch):
    sent, send = _collector()
    table = (
        "interface  peer        endpoint              latest\n"
        "wg0        YEHOR4      1.2.3.4:51820         12s\n"
        "wg0        Yehor       5.6.7.8:51820          offline\n"
        "wg0        china       9.9.9.9:51820          offline"
    )
    plan = build_plan(
        {
            "goal": "list peers",
            "steps": [{"tool": "wg_list_peers", "args": {}, "why": "peers"}],
        }
    )

    async def fake_route(user_text, session_id):
        return {"type": "plan", "plan": plan}

    async def fake_execute(tool_name, arguments=None):
        return {"content": [{"type": "text", "text": table}], "data": {}}

    async def fake_summary(user_text, plan, observations):
        return "3 peers on wg0; one is online and two are offline."

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)
    _patch_plan_runtime(monkeypatch)
    monkeypatch.setattr(ws_module, "_execute_tool", fake_execute)
    monkeypatch.setattr(ws_module.airouter_with_plugins, "summarize_plan", fake_summary)

    await ws_module._process_user_message(send, "sess", "list peers", {}, {})

    assert sent[-1]["type"] == "response"
    assert sent[-1]["content"] == "3 peers on wg0; one is online and two are offline."
    assert "interface  peer" not in sent[-1]["content"]


@pytest.mark.asyncio
async def test_explicit_raw_output_request_returns_the_table(monkeypatch):
    sent, send = _collector()
    table = "wg0  YEHOR4  1.2.3.4:51820\nwg0  china  9.9.9.9:51820"
    plan = build_plan(
        {
            "goal": "list peers",
            "steps": [{"tool": "wg_list_peers", "args": {}, "why": "peers"}],
        }
    )

    async def fake_route(user_text, session_id):
        return {"type": "plan", "plan": plan}

    async def fake_execute(tool_name, arguments=None):
        return {"content": [{"type": "text", "text": table}], "data": {}}

    async def boom_summary(user_text, plan, observations):
        raise AssertionError("raw output requests must not call the LLM summary")

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)
    _patch_plan_runtime(monkeypatch)
    monkeypatch.setattr(ws_module, "_execute_tool", fake_execute)
    monkeypatch.setattr(ws_module.airouter_with_plugins, "summarize_plan", boom_summary)

    await ws_module._process_user_message(
        send, "sess", "show me the raw output", {}, {}
    )

    assert sent[-1]["type"] == "response"
    assert sent[-1]["content"] == table


def test_wants_raw_output_detects_phrases():
    assert ws_module._wants_raw_output("show me the raw output")
    assert ws_module._wants_raw_output("list every row")
    assert ws_module._wants_raw_output("give me the full table")
    assert ws_module._wants_raw_output("покажи полный вывод")
    assert not ws_module._wants_raw_output("list the wireguard peers")
    assert not ws_module._wants_raw_output("")


@pytest.mark.asyncio
async def test_process_user_message_direct_dangerous_asks_in_text(monkeypatch):
    sent, send = _collector()
    plan = build_plan(
        {
            "goal": "restart nginx",
            "steps": [
                {
                    "tool": "core_disable_plugin",
                    "args": {"plugin": "LoomOps"},
                    "why": "stop it",
                }
            ],
        }
    )

    async def fake_route(user_text, session_id):
        return {"type": "plan", "plan": plan}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)
    _patch_plan_runtime(monkeypatch)
    tracker = {"tool": None, "plan": None}
    pending = {"plan": None, "user_text": ""}

    await ws_module._process_user_message(send, "sess", "disable", tracker, pending)

    types = [payload["type"] for payload in sent]
    assert "plan_proposed" not in types
    assert "tool_call" not in types
    assert sent[-1]["type"] == "response"
    assert "Reply yes to proceed." in sent[-1]["content"]
    assert pending["plan"]["plan_id"] == plan["plan_id"]
    assert pending["user_text"] == "disable"


@pytest.mark.asyncio
async def test_process_user_message_reports_step_failure(monkeypatch):
    sent, send = _collector()
    plan = build_plan(
        {
            "goal": "check",
            "steps": [{"tool": "core_health", "args": {}, "why": "health"}],
        }
    )

    async def fake_route(user_text, session_id):
        return {"type": "plan", "plan": plan}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)
    saved_tools, _ = _patch_plan_runtime(monkeypatch)

    async def boom(tool_name, arguments=None):
        raise RuntimeError("nope")

    monkeypatch.setattr(ws_module, "_execute_tool", boom)

    await ws_module._process_user_message(send, "sess", "check", {}, {})

    response = next(p for p in sent if p["type"] == "tool_response")
    assert response["status"] == "error"
    assert "nope" in response["content"]
    assert not any(p["type"] == "plan_done" for p in sent)
    assert saved_tools[-1]["status"] == "error"


# --------------------------------------------------------------------------- #
# websocket_endpoint actions
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_websocket_plan_approve(monkeypatch):
    _patch_db(monkeypatch)
    saved_tools, saved_plans = _patch_plan_runtime(monkeypatch)
    plan = build_plan(
        {
            "goal": "restart service and check",
            "steps": [
                {
                    "tool": "core_disable_plugin",
                    "args": {"plugin": "LoomOps"},
                    "why": "stop it",
                },
                {"tool": "core_health", "args": {}, "why": "verify"},
            ],
        }
    )

    async def fake_route(user_text, session_id):
        return {"type": "plan", "plan": plan}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    ws = FakeWebSocket(
        [{"message": "disable"}, {"action": "plan_approve"}], delay=0.05
    )
    ws.allow_disconnect.set()

    await ws_module.websocket_endpoint(ws, "sess")

    types = [payload["type"] for payload in ws.sent]
    assert "plan_proposed" in types
    assert types.count("tool_call") == 2
    assert "plan_done" in types
    assert "response" in types
    assert saved_tools[0]["status"] == "success"
    assert saved_plans[-1]["status"] == "done"


@pytest.mark.asyncio
async def test_websocket_plan_reject(monkeypatch):
    _patch_db(monkeypatch)
    saved_tools, saved_plans = _patch_plan_runtime(monkeypatch)
    plan = build_plan(
        {
            "goal": "restart service and check",
            "steps": [
                {
                    "tool": "core_disable_plugin",
                    "args": {"plugin": "LoomOps"},
                    "why": "stop it",
                },
                {"tool": "core_health", "args": {}, "why": "verify"},
            ],
        }
    )

    async def fake_route(user_text, session_id):
        return {"type": "plan", "plan": plan}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    ws = FakeWebSocket([{"message": "disable"}, {"action": "plan_reject"}], delay=0.05)
    ws.allow_disconnect.set()

    await ws_module.websocket_endpoint(ws, "sess")

    types = [payload["type"] for payload in ws.sent]
    assert "plan_proposed" in types
    assert "tool_call" not in types
    done = next(p for p in ws.sent if p["type"] == "plan_done")
    assert done["status"] == "rejected"
    assert saved_tools == []
    assert saved_plans[-1]["status"] == "rejected"


@pytest.mark.asyncio
async def test_websocket_plan_edit_auto_runs(monkeypatch):
    _patch_db(monkeypatch)
    saved_tools, saved_plans = _patch_plan_runtime(monkeypatch)
    plan = build_plan(
        {
            "goal": "disable",
            "steps": [
                {
                    "tool": "core_disable_plugin",
                    "args": {"plugin": "LoomOps"},
                    "why": "stop it",
                }
            ],
        }
    )

    async def fake_route(user_text, session_id):
        return {"type": "plan", "plan": plan}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    edited = {"tool": "core_health", "args": {}, "why": "just check"}
    ws = FakeWebSocket(
        [{"message": "disable"}, {"action": "plan_edit", "steps": [edited]}],
        delay=0.05,
    )
    ws.allow_disconnect.set()

    await ws_module.websocket_endpoint(ws, "sess")

    proposed = [p for p in ws.sent if p["type"] == "plan_proposed"]
    assert len(proposed) == 1
    assert proposed[-1]["revision"] == 2
    assert proposed[-1]["requires_approval"] is False
    types = [payload["type"] for payload in ws.sent]
    assert "tool_call" in types
    assert saved_plans[-1]["status"] == "done"


@pytest.mark.asyncio
async def test_websocket_plan_edit_rejects_unknown_tool(monkeypatch):
    _patch_db(monkeypatch)
    _patch_plan_runtime(monkeypatch)
    plan = build_plan(
        {
            "goal": "disable",
            "steps": [
                {
                    "tool": "core_disable_plugin",
                    "args": {"plugin": "LoomOps"},
                    "why": "stop it",
                }
            ],
        }
    )

    async def fake_route(user_text, session_id):
        return {"type": "plan", "plan": plan}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    bogus = {"tool": "not_a_tool", "args": {}, "why": "nope"}
    ws = FakeWebSocket(
        [{"message": "disable"}, {"action": "plan_edit", "steps": [bogus]}],
        delay=0.05,
    )
    ws.allow_disconnect.set()

    await ws_module.websocket_endpoint(ws, "sess")

    errors = [p for p in ws.sent if p["type"] == "error"]
    assert errors and "Invalid plan" in errors[-1]["error"]
    types = [payload["type"] for payload in ws.sent]
    assert "tool_call" not in types


@pytest.mark.asyncio
async def test_websocket_busy_message_queued_and_runs(monkeypatch):
    _patch_db(monkeypatch)
    calls = []

    async def fake_process(send, session_id, user_text, tracker=None, pending=None):
        calls.append(user_text)
        if user_text == "one":
            await asyncio.sleep(0.1)

    monkeypatch.setattr(ws_module, "_process_user_message", fake_process)

    ws = FakeWebSocket([{"message": "one"}, {"message": "two"}], delay=0.02)
    asyncio.get_event_loop().call_later(0.6, ws.allow_disconnect.set)

    await ws_module.websocket_endpoint(ws, "sess")

    assert calls == ["one", "two"]
    assert not any(payload.get("type") == "busy" for payload in ws.sent)


@pytest.mark.asyncio
async def test_summary_response_precedes_plan_done(monkeypatch):
    _patch_db(monkeypatch)
    _patch_plan_runtime(monkeypatch)
    sent, send = _collector()
    plan = build_plan(
        {
            "goal": "inspect",
            "steps": [
                {"tool": "core_health", "args": {}, "why": "a"},
                {"tool": "core_health", "args": {}, "why": "b"},
            ],
        }
    )

    await ws_module._run_plan(
        send, "sess", "check", plan, {"tool": None, "plan": None}
    )

    types = [payload["type"] for payload in sent]
    assert "plan_done" in types
    assert "response" in types
    assert types.index("response") < types.index("plan_done")


@pytest.mark.asyncio
async def test_websocket_cancel_during_plan(monkeypatch):
    _patch_db(monkeypatch)
    saved_tools = []
    saved_plans = []

    async def fake_save_tool(session_id, tool_name, arguments, content, **kwargs):
        saved_tools.append({"tool": tool_name, **kwargs})

    async def fake_save_plan(session_id, plan):
        saved_plans.append(plan)

    monkeypatch.setattr(ws_module, "_save_tool_message", fake_save_tool)
    monkeypatch.setattr(ws_module, "_save_plan_message", fake_save_plan)

    async def slow_execute(tool_name, arguments=None):
        await asyncio.Event().wait()

    monkeypatch.setattr(ws_module, "_execute_tool", slow_execute)

    plan = build_plan(
        {
            "goal": "check",
            "steps": [{"tool": "core_health", "args": {}, "why": "health"}],
        }
    )

    async def fake_route(user_text, session_id):
        return {"type": "plan", "plan": plan}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    ws = FakeWebSocket([{"message": "check"}, {"action": "cancel"}], delay=0.05)
    ws.allow_disconnect.set()

    await ws_module.websocket_endpoint(ws, "sess")

    assert any(payload.get("type") == "cancelled" for payload in ws.sent)
    assert any(tool.get("status") == "cancelled" for tool in saved_tools)
    assert saved_plans[-1]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_websocket_cancel_aborts_running_request(monkeypatch):
    _patch_db(monkeypatch)
    started = asyncio.Event()

    async def slow_process(send, session_id, user_text, tracker=None, pending=None):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(ws_module, "_process_user_message", slow_process)

    ws = FakeWebSocket([{"message": "one"}, {"action": "cancel"}], delay=0.05)
    ws.allow_disconnect.set()

    await ws_module.websocket_endpoint(ws, "sess")

    assert started.is_set()
    assert any(payload.get("type") == "cancelled" for payload in ws.sent)


# --------------------------------------------------------------------------- #
# conversational-first direct confirmations
# --------------------------------------------------------------------------- #


def _dangerous_plan(goal="restart nginx"):
    return build_plan(
        {
            "goal": goal,
            "steps": [
                {
                    "tool": "core_disable_plugin",
                    "args": {"plugin": "LoomOps"},
                    "why": "stop it",
                }
            ],
        }
    )


@pytest.mark.asyncio
async def test_websocket_direct_confirm_yes_runs(monkeypatch):
    _patch_db(monkeypatch)
    saved_tools, saved_plans = _patch_plan_runtime(monkeypatch)
    plan = _dangerous_plan()

    async def fake_route(user_text, session_id):
        return {"type": "plan", "plan": plan}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    ws = FakeWebSocket([{"message": "restart nginx"}, {"message": "yes"}], delay=0.05)
    ws.allow_disconnect.set()

    await ws_module.websocket_endpoint(ws, "sess")

    types = [payload["type"] for payload in ws.sent]
    assert "plan_proposed" not in types
    assert types.count("tool_call") == 1
    assert "tool_response" in types
    assert saved_tools[0]["status"] == "success"
    assert saved_plans == []


@pytest.mark.asyncio
async def test_websocket_direct_confirm_no_rejects(monkeypatch):
    _patch_db(monkeypatch)
    saved_tools, _ = _patch_plan_runtime(monkeypatch)
    plan = _dangerous_plan()

    async def fake_route(user_text, session_id):
        return {"type": "plan", "plan": plan}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    ws = FakeWebSocket([{"message": "restart nginx"}, {"message": "нет"}], delay=0.05)
    ws.allow_disconnect.set()

    await ws_module.websocket_endpoint(ws, "sess")

    types = [payload["type"] for payload in ws.sent]
    assert "tool_call" not in types
    assert any(
        payload.get("content") == "Cancelled." for payload in ws.sent
    )
    assert any(tool.get("status") == "cancelled" for tool in saved_tools)


@pytest.mark.asyncio
async def test_websocket_direct_unclear_reasks_then_resets(monkeypatch):
    _patch_db(monkeypatch)
    _patch_plan_runtime(monkeypatch)
    plan = _dangerous_plan()

    async def fake_route(user_text, session_id):
        return {"type": "plan", "plan": plan}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    ws = FakeWebSocket(
        [{"message": "restart nginx"}, {"message": "maybe"}, {"message": "whatever"}],
        delay=0.05,
    )
    ws.allow_disconnect.set()

    await ws_module.websocket_endpoint(ws, "sess")

    contents = [payload.get("content") for payload in ws.sent]
    assert "Reply yes to proceed, or no to cancel." in contents
    assert not any(payload.get("type") == "tool_call" for payload in ws.sent)


@pytest.mark.asyncio
async def test_websocket_direct_confirm_timeout_routes_new_message(monkeypatch):
    _patch_db(monkeypatch)
    monkeypatch.setattr(ws_module, "CONFIRMATION_TIMEOUT_SECONDS", -1.0)
    _patch_plan_runtime(monkeypatch)
    plan = _dangerous_plan()
    calls = []

    async def fake_route(user_text, session_id):
        calls.append(user_text)
        return {"type": "plan", "plan": plan}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    ws = FakeWebSocket([{"message": "restart nginx"}, {"message": "yes"}], delay=0.05)
    ws.allow_disconnect.set()

    await ws_module.websocket_endpoint(ws, "sess")

    assert len(calls) == 2
    assert not any(payload.get("type") == "tool_call" for payload in ws.sent)


@pytest.mark.asyncio
async def test_websocket_yes_without_pending_routes_normally(monkeypatch):
    _patch_db(monkeypatch)
    _patch_plan_runtime(monkeypatch)
    calls = []

    async def fake_route(user_text, session_id):
        calls.append(user_text)
        return {"type": "text", "content": "hello"}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    ws = FakeWebSocket([{"message": "yes"}], delay=0.05)
    ws.allow_disconnect.set()

    await ws_module.websocket_endpoint(ws, "sess")

    assert calls == ["yes"]
    assert ws.sent[-1] == {"type": "response", "content": "hello"}


@pytest.mark.asyncio
async def test_websocket_confirmation_isolated_per_session(monkeypatch):
    _patch_db(monkeypatch)
    _patch_plan_runtime(monkeypatch)
    plan = _dangerous_plan()

    async def fake_route(user_text, session_id):
        return {"type": "plan", "plan": plan}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    first = FakeWebSocket([{"message": "restart nginx"}], delay=0.05)
    first.allow_disconnect.set()
    await ws_module.websocket_endpoint(first, "sess-a")

    second = FakeWebSocket([{"message": "yes"}], delay=0.05)
    second.allow_disconnect.set()
    await ws_module.websocket_endpoint(second, "sess-b")

    assert not any(payload.get("type") == "tool_call" for payload in second.sent)


# --------------------------------------------------------------------------- #
# plan status lifecycle and terminal guarantees
# --------------------------------------------------------------------------- #


def _safe_two_step_plan(goal="inspect"):
    return build_plan(
        {
            "goal": goal,
            "steps": [
                {"tool": "core_health", "args": {}, "why": "a"},
                {"tool": "core_health", "args": {}, "why": "b"},
            ],
        }
    )


def _risky_two_step_plan(goal="disable and check"):
    return build_plan(
        {
            "goal": goal,
            "steps": [
                {
                    "tool": "core_disable_plugin",
                    "args": {"plugin": "LoomOps"},
                    "why": "stop it",
                },
                {"tool": "core_health", "args": {}, "why": "verify"},
            ],
        }
    )


@pytest.mark.asyncio
async def test_plan_proposed_auto_run_marks_approved(monkeypatch):
    sent, send = _collector()
    plan = _safe_two_step_plan()

    async def fake_route(user_text, session_id):
        return {"type": "plan", "plan": plan}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)
    _patch_plan_runtime(monkeypatch)
    pending = {"plan": None, "user_text": ""}

    await ws_module._process_user_message(send, "sess", "inspect", {}, pending)

    proposed = next(p for p in sent if p["type"] == "plan_proposed")
    assert proposed["requires_approval"] is False
    assert proposed["plan"]["status"] == "approved"
    steps = [p for p in sent if p["type"] == "plan_step"]
    assert steps and all(p["status"] == "running" for p in steps)
    done = next(p for p in sent if p["type"] == "plan_done")
    assert done["status"] == "done"
    assert pending["plan"] is None


@pytest.mark.asyncio
async def test_plan_proposed_requires_approval_stays_proposed(monkeypatch):
    sent, send = _collector()
    plan = _risky_two_step_plan()

    async def fake_route(user_text, session_id):
        return {"type": "plan", "plan": plan}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)
    _patch_plan_runtime(monkeypatch)
    pending = {"plan": None, "user_text": ""}

    await ws_module._process_user_message(send, "sess", "disable", {}, pending)

    proposed = next(p for p in sent if p["type"] == "plan_proposed")
    assert proposed["requires_approval"] is True
    assert proposed["plan"]["status"] == "proposed"
    assert not any(p["type"] == "plan_done" for p in sent)
    assert pending["plan"]["plan_id"] == plan["plan_id"]


@pytest.mark.asyncio
async def test_websocket_plan_approve_marks_approved(monkeypatch):
    _patch_db(monkeypatch)
    _patch_plan_runtime(monkeypatch)
    plan = _risky_two_step_plan()

    async def fake_route(user_text, session_id):
        return {"type": "plan", "plan": plan}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    ws = FakeWebSocket(
        [{"message": "disable"}, {"action": "plan_approve"}], delay=0.05
    )
    ws.allow_disconnect.set()

    await ws_module.websocket_endpoint(ws, "sess")

    proposed = [p for p in ws.sent if p["type"] == "plan_proposed"]
    assert len(proposed) == 2
    assert proposed[0]["plan"]["status"] == "proposed"
    assert proposed[-1]["requires_approval"] is False
    assert proposed[-1]["plan"]["status"] == "approved"


@pytest.mark.asyncio
async def test_plan_failure_emits_plan_done_failed(monkeypatch):
    sent, send = _collector()
    _patch_plan_runtime(monkeypatch)
    plan = _safe_two_step_plan()

    async def boom(tool_name, arguments=None):
        raise RuntimeError("nope")

    monkeypatch.setattr(ws_module, "_execute_tool", boom)

    await ws_module._run_plan(
        send, "sess", "inspect", plan, {"tool": None, "plan": None}
    )

    done = next(p for p in sent if p["type"] == "plan_done")
    assert done["status"] == "failed"
    assert any(p["type"] == "response" for p in sent)


@pytest.mark.asyncio
async def test_run_plan_survives_persistence_crash(monkeypatch):
    sent, send = _collector()
    _patch_plan_runtime(monkeypatch)
    plan = _safe_two_step_plan()

    async def boom_save(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(ws_module, "_save_tool_message", boom_save)

    await ws_module._run_plan(
        send, "sess", "inspect", plan, {"tool": None, "plan": None}
    )

    types = [p["type"] for p in sent]
    assert "error" in types
    assert "response" in types
    done = next(p for p in sent if p["type"] == "plan_done")
    assert done["status"] == "failed"


@pytest.mark.asyncio
async def test_websocket_typed_yes_approves_plan_card(monkeypatch):
    _patch_db(monkeypatch)
    saved_tools, saved_plans = _patch_plan_runtime(monkeypatch)
    plan = _risky_two_step_plan()

    async def fake_route(user_text, session_id):
        return {"type": "plan", "plan": plan}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    ws = FakeWebSocket([{"message": "disable"}, {"message": "yes"}], delay=0.05)
    ws.allow_disconnect.set()

    await ws_module.websocket_endpoint(ws, "sess")

    types = [p["type"] for p in ws.sent]
    assert "plan_proposed" in types
    assert types.count("tool_call") == 2
    assert saved_tools[0]["status"] == "success"
    assert saved_plans[-1]["status"] == "done"


@pytest.mark.asyncio
async def test_websocket_unrelated_message_cancels_plan_card(monkeypatch):
    _patch_db(monkeypatch)
    _patch_plan_runtime(monkeypatch)
    plan = _risky_two_step_plan()
    calls = []

    async def fake_route(user_text, session_id):
        calls.append(user_text)
        if len(calls) == 1:
            return {"type": "plan", "plan": plan}
        return {"type": "text", "content": "ok"}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    ws = FakeWebSocket([{"message": "disable"}, {"message": "maybe"}], delay=0.05)
    ws.allow_disconnect.set()

    await ws_module.websocket_endpoint(ws, "sess")

    done = [p for p in ws.sent if p["type"] == "plan_done"]
    assert done and done[0]["status"] == "rejected"
    assert calls == ["disable", "maybe"]
    assert not any(p["type"] == "tool_call" for p in ws.sent)
    assert any(p.get("content") == "ok" for p in ws.sent)


# === WebSocket message rate limiting ===

def test_allow_ws_message_enforces_window(monkeypatch):
    ws_module.reset_ws_message_limits()
    monkeypatch.setattr(ws_module, "WS_MESSAGE_LIMIT_PER_MINUTE", 2)

    assert ws_module._allow_ws_message("admin") is True
    assert ws_module._allow_ws_message("admin") is True
    assert ws_module._allow_ws_message("admin") is False
    # A different identity has its own window.
    assert ws_module._allow_ws_message("other") is True


@pytest.mark.asyncio
async def test_websocket_rejects_messages_over_the_limit(monkeypatch):
    _patch_db(monkeypatch)
    monkeypatch.setattr(ws_module, "WS_MESSAGE_LIMIT_PER_MINUTE", 1)

    # Empty messages are ignored by handle_message, so only the limiter runs and
    # the (unpatched) LLM router/DB is never reached - exactly one rejection.
    ws = FakeWebSocket(["", ""], delay=0.05)
    ws.allow_disconnect.set()

    await ws_module.websocket_endpoint(ws, "sess")

    errors = [p for p in ws.sent if p["type"] == "error"]
    assert len(errors) == 1
    assert "Rate limit" in errors[0]["error"]


# === central mutation audit ===

@pytest.mark.asyncio
async def test_run_plan_audits_non_safe_steps(monkeypatch):
    _patch_plan_runtime(monkeypatch)
    calls = []

    async def fake_record(**kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(ws_module, "record_mutation_isolated", fake_record)
    plan = build_plan(
        {
            "mode": "plan",
            "goal": "restart wg0",
            "steps": [
                {
                    "tool": "wg_interface_restart",
                    "args": {"interface": "wg0"},
                    "why": "restart",
                }
            ],
        }
    )
    plan["status"] = "approved"
    _, send = _collector()

    await ws_module._run_plan(send, "sess", "restart wg0", plan)

    assert len(calls) == 1
    assert calls[0]["tool"] == "wg_interface_restart"
    assert calls[0]["risk"] != "safe"
    assert calls[0]["status"] == "success"
    assert calls[0]["plan_id"] == plan["plan_id"]


@pytest.mark.asyncio
async def test_run_plan_skips_audit_for_safe_steps(monkeypatch):
    _patch_plan_runtime(monkeypatch)
    calls = []

    async def fake_record(**kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(ws_module, "record_mutation_isolated", fake_record)
    plan = build_plan(
        {
            "mode": "direct",
            "goal": "check health",
            "steps": [{"tool": "core_health", "args": {}, "why": "check"}],
        }
    )
    _, send = _collector()

    await ws_module._run_plan(send, "sess", "check health", plan)

    assert calls == []


# --------------------------------------------------------------------------- #
# _counts_mismatch summary guard
# --------------------------------------------------------------------------- #


def _health_observation(text):
    return [{"step": {"id": "s1"}, "result": {"content": [{"type": "text", "text": text}]}}]


def test_counts_mismatch_flags_changed_numbers():
    observations = _health_observation(
        "Health scan: degraded (0 critical, 1 warning, 0 info)"
    )

    assert ws_module._counts_mismatch(
        "The host is healthy: 0 critical, 0 warnings, 1 info.", observations
    )
    assert not ws_module._counts_mismatch(
        "Health scan: degraded (0 critical, 1 warning, 0 info)", observations
    )
    assert not ws_module._counts_mismatch("All good.", observations)


@pytest.mark.asyncio
async def test_summarize_falls_back_to_raw_output_on_count_mismatch(monkeypatch):
    async def fake_summarize(user_text, plan, observations):
        return "It is healthy: 0 critical, 0 warnings, 1 info."

    monkeypatch.setattr(
        ws_module.airouter_with_plugins, "summarize_plan", fake_summarize
    )
    plan = build_plan(
        {
            "mode": "plan",
            "goal": "check health",
            "steps": [
                {"tool": "ops_scan_health", "args": {}, "why": "a"},
                {"tool": "ops_system_metrics", "args": {}, "why": "b"},
            ],
        }
    )
    observations = _health_observation(
        "Health scan: degraded (0 critical, 1 warning, 0 info)"
    )

    summary = await ws_module._summarize("why slow", plan, observations)

    assert "degraded (0 critical, 1 warning, 0 info)" in summary
    assert "0 warnings, 1 info" not in summary
