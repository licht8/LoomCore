"""Unit tests for the plan-driven WebSocket orchestration."""

import asyncio
import copy
import json
import logging
from datetime import UTC, datetime

import pytest
from fastapi import WebSocketDisconnect

from src.core import core_tools
from src.core.agent import build_plan
from src.interfaces import websocket as ws_module
from src.llm.client import LlmError


class _FakeReportManager:
    """Report declarations of the bundled plugins, as discovery would index them."""

    _KINDS = {
        "wg_list_interfaces": "always",
        "wg_list_peers": "always",
        "wg_interface_status": "always",
        "wg_show_interface_config": "always",
        "wg_stats": "always",
        "wg_diagnostics": "always",
        "ops_list_processes": "always",
        "ops_failed_units": "always",
        "ops_scan_health": "always",
        "ops_network_status": "always",
        "ops_dmesg": "always",
        "ops_disk_usage": "always",
        "ops_system_metrics": "always",
        "ops_run_command": "command",
    }

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def report_kind(self, tool_name):
        return self._KINDS.get(tool_name, "")


@pytest.fixture(autouse=True)
def _bundled_report_tools(monkeypatch):
    """At runtime ``discover_plugins`` indexes the plugin report declarations."""
    monkeypatch.setattr(
        core_tools,
        "plugin_manager",
        _FakeReportManager(core_tools.plugin_manager),
    )



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


def _patch_db(monkeypatch, *, pending=None, session_id="sess"):
    monkeypatch.setattr(ws_module, "async_session_factory", DummyContext)

    async def fake_ensure(db, session_id):
        return session_id

    store = {"values": {}, "assistant": []}
    if pending is not None:
        store["values"][session_id] = pending

    async def fake_add_assistant(db, session_id, content, tool_calls=None):
        store["assistant"].append(content)
        return None

    async def fake_get_pending(db, session_id):
        return store["values"].get(session_id)

    async def fake_set_pending(db, session_id, plan, user_text):
        store["values"][session_id] = {
            "plan": plan,
            "user_text": user_text,
            "created_at": datetime.now(UTC).isoformat(),
        }

    async def fake_claim_pending(db, session_id, plan_id):
        value = store["values"].get(session_id)
        if not value or (value.get("plan") or {}).get("plan_id") != plan_id:
            return None
        store["values"][session_id] = None
        return value

    async def fake_replace_pending(db, session_id, plan_id, plan, user_text):
        value = store["values"].get(session_id)
        if not value or (value.get("plan") or {}).get("plan_id") != plan_id:
            return False
        store["values"][session_id] = {
            "plan": plan,
            "user_text": user_text,
            "created_at": datetime.now(UTC).isoformat(),
        }
        return True

    async def fake_clear_pending(db, session_id):
        store["values"][session_id] = None

    monkeypatch.setattr(ws_module, "ensure_session", fake_ensure)
    monkeypatch.setattr(ws_module, "add_assistant_message", fake_add_assistant)
    monkeypatch.setattr(ws_module, "get_stored_pending", fake_get_pending)
    monkeypatch.setattr(ws_module, "set_stored_pending", fake_set_pending)
    monkeypatch.setattr(ws_module, "claim_stored_pending", fake_claim_pending)
    monkeypatch.setattr(ws_module, "replace_stored_pending", fake_replace_pending)
    monkeypatch.setattr(ws_module, "clear_stored_pending", fake_clear_pending)
    return store


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

    async def fake_execute(tool_name, arguments=None, *, approved=False):
        return {
            "content": [{"type": "text", "text": f"{tool_name} ok"}],
            "data": {},
        }

    async def fake_save_tool(session_id, tool_name, arguments, content, **kwargs):
        saved_tools.append({"tool": tool_name, "content": content, **kwargs})

    async def fake_save_plan(session_id, plan):
        saved_plans.append(plan)

    async def fake_summarize(user_text, plan, observations, **kwargs):
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

    async def fake_dispatch(name, arguments, *, approved=False):
        captured["args"] = (name, arguments)
        return {"ok": True}

    monkeypatch.setattr(ws_module, "dispatch_core_tool", fake_dispatch)

    result = await ws_module._execute_tool("core_health", {})

    assert captured["args"] == ("core_health", {})
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_execute_tool_forwards_approval(monkeypatch):
    captured = {}

    async def fake_dispatch(name, arguments, *, approved=False):
        captured["approved"] = approved
        return {"ok": True}

    monkeypatch.setattr(ws_module, "dispatch_core_tool", fake_dispatch)

    result = await ws_module._execute_tool("core_enable_plugin", {}, approved=True)

    assert captured["approved"] is True
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_execute_tool_routes_plugin_names_to_manager(monkeypatch):
    captured = {}

    async def fake_execute(name, arguments, *, approved=False):
        captured["args"] = (name, arguments)
        captured["approved"] = approved
        return {"ok": True}

    monkeypatch.setattr(
        ws_module.plugin_manager, "execute_tool_by_name", fake_execute
    )

    result = await ws_module._execute_tool("wg_interface_status", {"interface": "wg0"})

    assert captured["args"] == ("wg_interface_status", {"interface": "wg0"})
    assert captured["approved"] is False
    assert result == {"ok": True}

    result = await ws_module._execute_tool(
        "wg_interface_status", {"interface": "wg0"}, approved=True
    )

    assert captured["approved"] is True
    assert result == {"ok": True}


# --------------------------------------------------------------------------- #
# _process_user_message
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_process_user_message_text_response(monkeypatch):
    sent, send = _collector()

    async def fake_route(user_text, session_id, **kwargs):
        return {"type": "text", "content": "hello"}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    await ws_module._process_user_message(send, "sess", "hi")

    assert sent[0] == {
        "type": "thinking",
        "status": "processing",
        "schema_version": 1,
    }
    assert sent[-1]["type"] == "response"
    assert sent[-1]["content"] == "hello"
    assert sent[-1]["schema_version"] == 1
    assert sent[-1]["message_id"].startswith("msg_")


@pytest.mark.asyncio
async def test_process_user_message_reports_llm_error(monkeypatch):
    sent, send = _collector()

    async def fake_route(user_text, session_id, **kwargs):
        raise LlmError("boom")

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    await ws_module._process_user_message(send, "sess", "hi")

    assert sent[-1] == {
        "type": "error",
        "error": "The language model request failed.",
        "schema_version": 1,
    }


@pytest.mark.asyncio
async def test_process_user_message_auto_runs_direct_safe(monkeypatch):
    sent, send = _collector()
    plan = build_plan(
        {
            "goal": "check",
            "steps": [{"tool": "core_health", "args": {}, "why": "health"}],
        }
    )

    async def fake_route(user_text, session_id, **kwargs):
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
    # A direct plan is described in the assistant's own words.
    assert sent[-1]["content"] == "Summary"
    assert saved_tools[0]["status"] == "success"
    assert saved_plans == []
    assert pending["plan"] is None
    assert tracker["plan"] is None


@pytest.mark.asyncio
async def test_direct_report_action_is_summarized(monkeypatch):
    sent, send = _collector()
    plan = build_plan(
        {
            "goal": "check",
            "steps": [{"tool": "core_health", "args": {}, "why": "health"}],
        }
    )
    calls = []

    async def fake_route(user_text, session_id, **kwargs):
        return {"type": "plan", "plan": plan}

    async def fake_summary(user_text, plan, observations, **kwargs):
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
async def test_report_turn_emits_non_compact_markdown_cards(monkeypatch):
    sent, send = _collector()
    plan = build_plan(
        {
            "goal": "check",
            "steps": [{"tool": "core_health", "args": {}, "why": "health"}],
        }
    )

    async def fake_route(user_text, session_id, **kwargs):
        return {"type": "plan", "plan": plan}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)
    saved_tools, _ = _patch_plan_runtime(monkeypatch)

    await ws_module._process_user_message(send, "sess", "check")

    tool_response = next(p for p in sent if p["type"] == "tool_response")
    assert tool_response["compact"] is False
    assert tool_response["format"] == "markdown"
    assert saved_tools[0]["compact"] is False
    assert saved_tools[0]["format"] == "markdown"


@pytest.mark.asyncio
async def test_command_turn_emits_text_format(monkeypatch):
    sent, send = _collector()
    plan = build_plan(
        {
            "goal": "read config",
            "steps": [{"tool": "ops_read_file", "args": {}, "why": "read"}],
        }
    )

    async def fake_route(user_text, session_id, **kwargs):
        return {"type": "plan", "plan": plan}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)
    saved_tools, _ = _patch_plan_runtime(monkeypatch)

    await ws_module._process_user_message(send, "sess", "read config")

    tool_response = next(p for p in sent if p["type"] == "tool_response")
    assert tool_response["format"] == "text"
    assert tool_response["compact"] is False
    assert saved_tools[0]["format"] == "text"
    assert saved_tools[0]["compact"] is False
    assert sent[-1]["type"] == "response"
    assert sent[-1]["content"] == "Summary"


@pytest.mark.asyncio
async def test_allowlisted_command_turn_skips_summary(monkeypatch):
    sent, send = _collector()
    monkeypatch.setattr(ws_module.settings, "report_commands", "uptime")
    plan = build_plan(
        {
            "goal": "uptime",
            "steps": [
                {
                    "tool": "ops_run_command",
                    "args": {"command": "uptime"},
                    "why": "check uptime",
                }
            ],
        }
    )
    plan["status"] = "approved"

    async def boom_summary(user_text, plan, observations, **kwargs):
        raise AssertionError("a read-only command turn must not call the summary")

    _patch_plan_runtime(monkeypatch)
    monkeypatch.setattr(ws_module.airouter_with_plugins, "summarize_plan", boom_summary)

    await ws_module._run_plan(
        send, "sess", "uptime", plan, {"tool": None, "plan": None}
    )

    tool_response = next(p for p in sent if p["type"] == "tool_response")
    assert tool_response["format"] == "text"
    assert tool_response["compact"] is True
    assert sent[-1]["type"] == "response"
    assert sent[-1]["content"] == "ops_run_command ok"


@pytest.mark.asyncio
async def test_mixed_plan_uses_llm_summary_and_non_compact_cards(monkeypatch):
    sent, send = _collector()
    plan = build_plan(
        {
            "goal": "check and read",
            "steps": [
                {"tool": "core_health", "args": {}, "why": "health"},
                {"tool": "ops_read_file", "args": {}, "why": "read"},
            ],
        }
    )
    calls = []

    async def fake_route(user_text, session_id, **kwargs):
        return {"type": "plan", "plan": plan}

    async def fake_summary(user_text, plan, observations, **kwargs):
        calls.append(user_text)
        return "All done."

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)
    _patch_plan_runtime(monkeypatch)
    monkeypatch.setattr(ws_module.airouter_with_plugins, "summarize_plan", fake_summary)

    await ws_module._process_user_message(send, "sess", "check and read")

    assert calls == ["check and read"]
    responses = [p for p in sent if p["type"] == "tool_response"]
    assert len(responses) == 2
    assert all(p["compact"] is False for p in responses)
    summary = next(p for p in sent if p["type"] == "response")
    assert summary["content"] == "All done."


@pytest.mark.asyncio
async def test_summary_replaces_a_raw_table(monkeypatch):
    _patch_db(monkeypatch)
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
            "steps": [
                {"tool": "ops_read_file", "args": {}, "why": "peers"},
                {"tool": "ops_log_search", "args": {}, "why": "interfaces"},
            ],
        }
    )

    async def fake_execute(tool_name, arguments=None, *, approved=False):
        return {"content": [{"type": "text", "text": table}], "data": {}}

    async def fake_summary(user_text, plan, observations, **kwargs):
        return "3 peers on wg0; one is online and two are offline."

    _patch_plan_runtime(monkeypatch)
    monkeypatch.setattr(ws_module, "_execute_tool", fake_execute)
    monkeypatch.setattr(ws_module.airouter_with_plugins, "summarize_plan", fake_summary)

    await ws_module._run_plan(
        send, "sess", "list peers", plan, {"tool": None, "plan": None}
    )

    response = next(p for p in sent if p["type"] == "response")
    assert response["content"] == "3 peers on wg0; one is online and two are offline."
    assert "interface  peer" not in response["content"]


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

    async def fake_route(user_text, session_id, **kwargs):
        return {"type": "plan", "plan": plan}

    async def fake_execute(tool_name, arguments=None, *, approved=False):
        return {"content": [{"type": "text", "text": table}], "data": {}}

    async def boom_summary(user_text, plan, observations, **kwargs):
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

    async def fake_route(user_text, session_id, **kwargs):
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

    async def fake_route(user_text, session_id, **kwargs):
        return {"type": "plan", "plan": plan}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)
    saved_tools, _ = _patch_plan_runtime(monkeypatch)

    async def boom(tool_name, arguments=None, *, approved=False):
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

    async def fake_route(user_text, session_id, **kwargs):
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

    async def fake_route(user_text, session_id, **kwargs):
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

    async def fake_route(user_text, session_id, **kwargs):
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

    async def fake_route(user_text, session_id, **kwargs):
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
async def test_websocket_plan_edit_rejects_invalid_arguments(monkeypatch):
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

    async def fake_route(user_text, session_id, **kwargs):
        return {"type": "plan", "plan": plan}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    edited = {"tool": "core_disable_plugin", "args": {}, "why": "no plugin"}
    ws = FakeWebSocket(
        [{"message": "disable"}, {"action": "plan_edit", "steps": [edited]}],
        delay=0.05,
    )
    ws.allow_disconnect.set()

    await ws_module.websocket_endpoint(ws, "sess")

    errors = [p for p in ws.sent if p["type"] == "error"]
    assert errors and "Invalid plan" in errors[-1]["error"]
    assert "missing required argument(s): plugin" in errors[-1]["error"]
    types = [payload["type"] for payload in ws.sent]
    assert "tool_call" not in types


def _two_step_plan():
    return build_plan(
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


def _supersede_get(store):
    """Return a get_stored_pending that reports a newer pending plan."""

    async def superseded(db, session_id):
        value = store["values"].get(session_id)
        if value and value.get("plan"):
            return {**value, "plan": {**value["plan"], "plan_id": "pl_newer"}}
        return value

    return superseded


async def _run_action(monkeypatch, plan, frame, *, get_pending=None):
    store = _patch_db(monkeypatch)
    saved_tools, saved_plans = _patch_plan_runtime(monkeypatch)

    async def fake_route(user_text, session_id, **kwargs):
        return {"type": "plan", "plan": plan}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)
    if get_pending is not None:
        monkeypatch.setattr(ws_module, "get_stored_pending", get_pending(store))

    ws = FakeWebSocket([{"message": "disable"}, frame], delay=0.05)
    ws.allow_disconnect.set()
    await ws_module.websocket_endpoint(ws, "sess")
    return store, saved_tools, saved_plans, ws


@pytest.mark.asyncio
async def test_websocket_plan_approve_superseded_reports_replaced_and_does_not_run(
    monkeypatch,
):
    store, saved_tools, _saved_plans, ws = await _run_action(
        monkeypatch,
        _two_step_plan(),
        {"action": "plan_approve"},
        get_pending=_supersede_get,
    )

    responses = [p for p in ws.sent if p["type"] == "response"]
    assert responses and "replaced" in responses[-1]["content"]
    types = [payload["type"] for payload in ws.sent]
    assert "tool_call" not in types
    assert saved_tools == []
    assert store["values"]["sess"] is not None


@pytest.mark.asyncio
async def test_websocket_plan_reject_superseded_reports_replaced_and_does_not_run(
    monkeypatch,
):
    store, saved_tools, _saved_plans, ws = await _run_action(
        monkeypatch,
        _two_step_plan(),
        {"action": "plan_reject"},
        get_pending=_supersede_get,
    )

    responses = [p for p in ws.sent if p["type"] == "response"]
    assert responses and "replaced" in responses[-1]["content"]
    types = [payload["type"] for payload in ws.sent]
    assert "tool_call" not in types
    assert saved_tools == []
    assert store["values"]["sess"] is not None


@pytest.mark.asyncio
async def test_websocket_plan_edit_on_replaced_pending_reports_replaced(monkeypatch):
    store = _patch_db(monkeypatch)
    saved_tools, _saved_plans = _patch_plan_runtime(monkeypatch)
    plan = _two_step_plan()

    async def fake_route(user_text, session_id, **kwargs):
        return {"type": "plan", "plan": plan}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)
    monkeypatch.setattr(ws_module, "get_stored_pending", _supersede_get(store))

    edited = {"tool": "core_health", "args": {}, "why": "just check"}
    ws = FakeWebSocket(
        [{"message": "disable"}, {"action": "plan_edit", "steps": [edited]}],
        delay=0.05,
    )
    ws.allow_disconnect.set()
    await ws_module.websocket_endpoint(ws, "sess")

    responses = [p for p in ws.sent if p["type"] == "response"]
    assert responses and "replaced" in responses[-1]["content"]
    types = [payload["type"] for payload in ws.sent]
    assert "tool_call" not in types
    assert saved_tools == []


@pytest.mark.asyncio
async def test_websocket_plan_edit_consumed_pending_reports_inactive(monkeypatch):
    _patch_db(monkeypatch)
    saved_tools, _saved_plans = _patch_plan_runtime(monkeypatch)
    plan = _two_step_plan()

    async def fake_route(user_text, session_id, **kwargs):
        return {"type": "plan", "plan": plan}

    async def losing_replace(db, session_id, plan_id, revised, user_text):
        return False

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)
    monkeypatch.setattr(ws_module, "replace_stored_pending", losing_replace)

    edited = {"tool": "core_health", "args": {}, "why": "just check"}
    ws = FakeWebSocket(
        [{"message": "disable"}, {"action": "plan_edit", "steps": [edited]}],
        delay=0.05,
    )
    ws.allow_disconnect.set()
    await ws_module.websocket_endpoint(ws, "sess")

    responses = [p for p in ws.sent if p["type"] == "response"]
    assert responses and "no longer active" in responses[-1]["content"]
    types = [payload["type"] for payload in ws.sent]
    assert "tool_call" not in types
    assert saved_tools == []


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

    async def slow_execute(tool_name, arguments=None, *, approved=False):
        await asyncio.Event().wait()

    monkeypatch.setattr(ws_module, "_execute_tool", slow_execute)

    plan = build_plan(
        {
            "goal": "check",
            "steps": [{"tool": "core_health", "args": {}, "why": "health"}],
        }
    )

    async def fake_route(user_text, session_id, **kwargs):
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

    async def fake_route(user_text, session_id, **kwargs):
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

    async def fake_route(user_text, session_id, **kwargs):
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

    async def fake_route(user_text, session_id, **kwargs):
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
async def test_websocket_yes_with_expired_pending_errors_and_does_not_route(monkeypatch):
    _patch_db(monkeypatch)
    monkeypatch.setattr(ws_module, "CONFIRMATION_TIMEOUT_SECONDS", -1.0)
    _patch_plan_runtime(monkeypatch)
    plan = _dangerous_plan()
    calls = []

    async def fake_route(user_text, session_id, **kwargs):
        calls.append(user_text)
        return {"type": "plan", "plan": plan}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    ws = FakeWebSocket([{"message": "restart nginx"}, {"message": "yes"}], delay=0.05)
    ws.allow_disconnect.set()

    await ws_module.websocket_endpoint(ws, "sess")

    # The decision is never re-planned, so only the original request is routed.
    assert len(calls) == 1
    assert not any(payload.get("type") == "tool_call" for payload in ws.sent)
    assert any(
        "expired" in str(payload.get("content", "")) for payload in ws.sent
    )


@pytest.mark.asyncio
async def test_websocket_direct_plan_persists_pending_and_prompt(monkeypatch):
    store = _patch_db(monkeypatch)
    _patch_plan_runtime(monkeypatch)
    plan = _dangerous_plan()

    async def fake_route(user_text, session_id, **kwargs):
        return {"type": "plan", "plan": plan}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    ws = FakeWebSocket([{"message": "restart nginx"}], delay=0.05)
    ws.allow_disconnect.set()

    await ws_module.websocket_endpoint(ws, "sess")

    assert store["values"]["sess"] is not None
    assert store["values"]["sess"]["plan"]["plan_id"] == plan["plan_id"]
    assert store["values"]["sess"]["user_text"] == "restart nginx"
    assert any("Reply yes to proceed." in text for text in store["assistant"])


@pytest.mark.asyncio
async def test_websocket_yes_after_reconnect_approves_stored_plan(monkeypatch):
    plan = _dangerous_plan()
    record = {
        "plan": plan,
        "user_text": "restart nginx",
        "created_at": datetime.now(UTC).isoformat(),
    }
    _patch_db(monkeypatch, pending=record)
    saved_tools, _ = _patch_plan_runtime(monkeypatch)
    calls = []

    async def fake_route(user_text, session_id, **kwargs):
        calls.append(user_text)
        return {"type": "text", "content": "hi"}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    ws = FakeWebSocket([{"message": "yes"}], delay=0.05)
    ws.allow_disconnect.set()

    await ws_module.websocket_endpoint(ws, "sess")

    # The plan is hydrated from the store on connect: it runs, nothing is re-planned.
    assert calls == []
    assert [p["type"] for p in ws.sent].count("tool_call") == 1
    assert saved_tools and saved_tools[0]["status"] == "success"


@pytest.mark.asyncio
async def test_websocket_yes_with_superseded_pending_errors_and_does_not_route(
    monkeypatch,
):
    _patch_db(monkeypatch)
    _patch_plan_runtime(monkeypatch)
    old_plan = _dangerous_plan()
    new_plan = _risky_two_step_plan()
    record_a = {
        "plan": old_plan,
        "user_text": "restart nginx",
        "created_at": datetime.now(UTC).isoformat(),
    }
    record_b = {
        "plan": new_plan,
        "user_text": "disable",
        "created_at": datetime.now(UTC).isoformat(),
    }
    reads = {"n": 0}

    async def fake_get(db, session_id):
        reads["n"] += 1
        return record_a if reads["n"] == 1 else record_b

    monkeypatch.setattr(ws_module, "get_stored_pending", fake_get)

    ws = FakeWebSocket([{"message": "yes"}], delay=0.05)
    ws.allow_disconnect.set()

    await ws_module.websocket_endpoint(ws, "sess")

    assert any("replaced" in str(p.get("content", "")) for p in ws.sent)
    assert not any(p.get("type") == "tool_call" for p in ws.sent)


@pytest.mark.asyncio
async def test_websocket_second_yes_after_claim_is_no_longer_active(monkeypatch):
    plan = _dangerous_plan()
    record = {
        "plan": plan,
        "user_text": "restart nginx",
        "created_at": datetime.now(UTC).isoformat(),
    }
    _patch_db(monkeypatch, pending=record)
    _patch_plan_runtime(monkeypatch)

    async def fake_claim(db, session_id, plan_id):
        return None

    monkeypatch.setattr(ws_module, "claim_stored_pending", fake_claim)

    ws = FakeWebSocket([{"message": "yes"}], delay=0.05)
    ws.allow_disconnect.set()

    await ws_module.websocket_endpoint(ws, "sess")

    assert any("no longer active" in str(p.get("content", "")) for p in ws.sent)
    assert not any(p.get("type") == "tool_call" for p in ws.sent)


@pytest.mark.asyncio
async def test_websocket_long_phrase_with_yes_is_not_an_approval_routes_to_planner(
    monkeypatch,
):
    _patch_db(monkeypatch)
    _patch_plan_runtime(monkeypatch)
    plan = _risky_two_step_plan()
    calls = []

    async def fake_route(user_text, session_id, **kwargs):
        calls.append(user_text)
        return {"type": "plan", "plan": plan}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    ws = FakeWebSocket(
        [{"message": "disable"}, {"message": "да, покажи ещё диски"}], delay=0.05
    )
    ws.allow_disconnect.set()

    await ws_module.websocket_endpoint(ws, "sess")

    assert calls == ["disable", "да, покажи ещё диски"]
    assert not any(p.get("type") == "tool_call" for p in ws.sent)


@pytest.mark.asyncio
async def test_websocket_yes_without_pending_routes_normally(monkeypatch):
    _patch_db(monkeypatch)
    _patch_plan_runtime(monkeypatch)
    calls = []

    async def fake_route(user_text, session_id, **kwargs):
        calls.append(user_text)
        return {"type": "text", "content": "hello"}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    ws = FakeWebSocket([{"message": "yes"}], delay=0.05)
    ws.allow_disconnect.set()

    await ws_module.websocket_endpoint(ws, "sess")

    assert calls == ["yes"]
    assert ws.sent[-1]["type"] == "response"
    assert ws.sent[-1]["content"] == "hello"
    assert ws.sent[-1]["schema_version"] == 1
    assert ws.sent[-1]["message_id"].startswith("msg_")


@pytest.mark.asyncio
async def test_websocket_confirmation_isolated_per_session(monkeypatch):
    _patch_db(monkeypatch)
    _patch_plan_runtime(monkeypatch)
    plan = _dangerous_plan()

    async def fake_route(user_text, session_id, **kwargs):
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

    async def fake_route(user_text, session_id, **kwargs):
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

    async def fake_route(user_text, session_id, **kwargs):
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

    async def fake_route(user_text, session_id, **kwargs):
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

    async def boom(tool_name, arguments=None, *, approved=False):
        raise RuntimeError("nope")

    monkeypatch.setattr(ws_module, "_execute_tool", boom)

    await ws_module._run_plan(
        send, "sess", "inspect", plan, {"tool": None, "plan": None}
    )

    done = next(p for p in sent if p["type"] == "plan_done")
    assert done["status"] == "failed"
    assert any(p["type"] == "response" for p in sent)


@pytest.mark.asyncio
async def test_run_plan_crash_does_not_leak_exception_text(monkeypatch):
    sent, send = _collector()
    _patch_plan_runtime(monkeypatch)
    plan = _safe_two_step_plan()

    async def boom(plan, dispatch=None, emit=None):
        raise RuntimeError("secret /etc/wireguard/wg0.conf")

    monkeypatch.setattr(ws_module, "execute_plan", boom)

    await ws_module._run_plan(
        send, "sess", "inspect", plan, {"tool": None, "plan": None}
    )

    errors = [p for p in sent if p["type"] == "error"]
    assert errors
    assert all("secret" not in (p.get("error") or "") for p in errors)
    assert any(p.get("error") == "Internal error." for p in errors)


@pytest.mark.asyncio
async def test_run_plan_completes_when_tool_persistence_fails(monkeypatch, caplog):
    sent, send = _collector()
    _patch_plan_runtime(monkeypatch)
    plan = _safe_two_step_plan()

    async def boom_save(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(ws_module, "_save_tool_message", boom_save)

    with caplog.at_level(logging.WARNING, logger="loomcore.websocket"):
        await ws_module._run_plan(
            send, "sess", "inspect", plan, {"tool": None, "plan": None}
        )

    assert "Failed to persist success tool message" in caplog.text
    types = [p["type"] for p in sent]
    assert "error" not in types
    terminal = [p for p in sent if p["type"] == "tool_response"]
    assert len(terminal) == 2
    assert all(p["status"] == "success" for p in terminal)
    assert "response" in types
    done = next(p for p in sent if p["type"] == "plan_done")
    assert done["status"] == "done"


@pytest.mark.asyncio
async def test_websocket_typed_yes_approves_plan_card(monkeypatch):
    _patch_db(monkeypatch)
    saved_tools, saved_plans = _patch_plan_runtime(monkeypatch)
    plan = _risky_two_step_plan()

    async def fake_route(user_text, session_id, **kwargs):
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

    async def fake_route(user_text, session_id, **kwargs):
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


# === inbound frame validation ===


class _BadJsonWebSocket(FakeWebSocket):
    """Fake socket whose first receive_json raises a JSON decode error."""

    def __init__(self, incoming, **kwargs):
        super().__init__(incoming, **kwargs)
        self._raise_once = True

    async def receive_json(self):
        if self._raise_once:
            self._raise_once = False
            raise json.JSONDecodeError("bad frame", "not-json", 0)
        return await super().receive_json()


class _RepeatedBadJsonWebSocket(FakeWebSocket):
    """Fake socket that rejects a fixed number of malformed frames first."""

    def __init__(self, bad_frames, incoming=None, **kwargs):
        super().__init__(incoming or [], **kwargs)
        self._bad_frames = bad_frames

    async def receive_json(self):
        if self._bad_frames > 0:
            self._bad_frames -= 1
            raise json.JSONDecodeError("bad frame", "not-json", 0)
        return await super().receive_json()


@pytest.mark.asyncio
async def test_websocket_ignores_non_string_message(monkeypatch):
    _patch_db(monkeypatch)
    _patch_plan_runtime(monkeypatch)
    calls = []

    async def fake_route(user_text, session_id, **kwargs):
        calls.append(user_text)
        return {"type": "text", "content": "ok"}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    ws = FakeWebSocket([{"message": 123}, {"message": "hello"}], delay=0.05)
    ws.allow_disconnect.set()

    await ws_module.websocket_endpoint(ws, "sess")

    assert calls == ["hello"]
    assert any(p.get("content") == "ok" for p in ws.sent)


@pytest.mark.asyncio
async def test_websocket_ignores_non_dict_frame(monkeypatch):
    _patch_db(monkeypatch)
    _patch_plan_runtime(monkeypatch)
    calls = []

    async def fake_route(user_text, session_id, **kwargs):
        calls.append(user_text)
        return {"type": "text", "content": "ok"}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    ws = FakeWebSocket([123, {"message": "hello"}], delay=0.05)
    ws.allow_disconnect.set()

    await ws_module.websocket_endpoint(ws, "sess")

    assert calls == ["hello"]
    assert any(p.get("content") == "ok" for p in ws.sent)


@pytest.mark.asyncio
async def test_websocket_skips_malformed_json_frame(monkeypatch):
    _patch_db(monkeypatch)
    _patch_plan_runtime(monkeypatch)
    calls = []

    async def fake_route(user_text, session_id, **kwargs):
        calls.append(user_text)
        return {"type": "text", "content": "ok"}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    ws = _BadJsonWebSocket([{"message": "hello"}], delay=0.05)
    ws.allow_disconnect.set()

    await ws_module.websocket_endpoint(ws, "sess")

    errors = [p for p in ws.sent if p["type"] == "error"]
    assert errors and errors[0]["error"] == "Malformed JSON frame ignored."
    assert calls == ["hello"]
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


@pytest.mark.asyncio
async def test_websocket_rate_limits_malformed_frames(monkeypatch):
    _patch_db(monkeypatch)
    monkeypatch.setattr(ws_module, "WS_MESSAGE_LIMIT_PER_MINUTE", 1)

    ws = _RepeatedBadJsonWebSocket(2)
    ws.allow_disconnect.set()

    await ws_module.websocket_endpoint(ws, "sess")

    errors = [p["error"] for p in ws.sent if p["type"] == "error"]
    assert sum("Malformed JSON frame ignored." in error for error in errors) == 1
    assert sum("Rate limit" in error for error in errors) == 1


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
async def test_summarize_falls_back_to_digest_on_count_mismatch(monkeypatch):
    async def fake_summarize(user_text, plan, observations, **kwargs):
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

    assert "It is healthy" not in summary
    assert "degraded (0 critical, 1 warning, 0 info)" in summary
    assert summary.startswith("check health.")


@pytest.mark.asyncio
async def test_summarize_falls_back_to_digest_on_failure(monkeypatch):
    async def boom(user_text, plan, observations, **kwargs):
        raise RuntimeError("summary model down")

    monkeypatch.setattr(ws_module.airouter_with_plugins, "summarize_plan", boom)
    plan = build_plan(
        {
            "mode": "direct",
            "goal": "disk usage",
            "steps": [{"tool": "ops_disk_usage", "args": {}, "why": "x"}],
        }
    )
    observations = [
        {
            "step": {"id": "s1", "tool": "ops_disk_usage", "status": "done"},
            "result": {"content": [{"type": "text", "text": "disk 42% used"}]},
        }
    ]

    summary = await ws_module._summarize("disk usage", plan, observations)

    assert summary == "disk usage.\n- ops_disk_usage: done — disk 42% used"


def test_digest_summary_caps_long_excerpt():
    long_text = "word " * 200
    observations = [
        {
            "step": {"id": "s1", "tool": "ops_x", "status": "done"},
            "result": {"content": [{"type": "text", "text": long_text}]},
        }
    ]

    digest = ws_module._digest_summary({"goal": "g", "steps": []}, observations)

    assert digest.startswith("g.\n- ops_x: done — ")
    assert digest.endswith("…")
    assert len(digest) < 400


@pytest.mark.asyncio
async def test_summarize_runs_for_all_report_plan(monkeypatch):
    calls = []

    async def fake_summarize(user_text, plan, observations, **kwargs):
        calls.append(user_text)
        return "Disk is 42% used."

    monkeypatch.setattr(
        ws_module.airouter_with_plugins, "summarize_plan", fake_summarize
    )
    plan = build_plan(
        {
            "mode": "plan",
            "goal": "disk usage",
            "steps": [
                {"tool": "ops_disk_usage", "args": {}, "why": "x"},
                {"tool": "ops_system_metrics", "args": {}, "why": "y"},
            ],
        }
    )
    observations = _health_observation("disk 42% used")

    summary = await ws_module._summarize("disk usage", plan, observations)

    assert calls == ["disk usage"]
    assert summary == "Disk is 42% used."


@pytest.mark.asyncio
async def test_summarize_runs_for_direct_command_plan(monkeypatch):
    calls = []

    async def fake_summarize(user_text, plan, observations, **kwargs):
        calls.append(user_text)
        return "The file was read."

    monkeypatch.setattr(
        ws_module.airouter_with_plugins, "summarize_plan", fake_summarize
    )
    plan = build_plan(
        {
            "mode": "direct",
            "goal": "read the config",
            "steps": [{"tool": "ops_read_file", "args": {}, "why": "x"}],
        }
    )
    observations = _health_observation("line one\nline two")

    summary = await ws_module._summarize("read the config", plan, observations)

    assert calls == ["read the config"]
    assert summary == "The file was read."


def test_step_answers_with_raw_ignores_report_tools():
    assert (
        ws_module._step_answers_with_raw({"tool": "ops_disk_usage", "args": {}})
        is False
    )
    assert ws_module._step_answers_with_raw({"tool": "core_health", "args": {}}) is False
    assert (
        ws_module._step_answers_with_raw({"tool": "ops_read_file", "args": {}})
        is False
    )


def test_step_answers_with_raw_read_only_command(monkeypatch):
    monkeypatch.setattr(ws_module.settings, "report_commands", "uptime, df")

    assert (
        ws_module._step_answers_with_raw(
            {"tool": "ops_run_command", "args": {"command": "uptime"}}
        )
        is True
    )
    assert (
        ws_module._step_answers_with_raw(
            {"tool": "ops_run_command", "args": {"command": "/usr/bin/df -h"}}
        )
        is True
    )
    # Not allow-listed.
    assert (
        ws_module._step_answers_with_raw(
            {"tool": "ops_run_command", "args": {"command": "rm -rf /"}}
        )
        is False
    )
    # Shell metacharacters are rejected even for an allow-listed basename.
    assert (
        ws_module._step_answers_with_raw(
            {"tool": "ops_run_command", "args": {"command": "uptime; rm -rf /"}}
        )
        is False
    )
    assert (
        ws_module._step_answers_with_raw(
            {"tool": "ops_run_command", "args": {"command": "df -h | grep x"}}
        )
        is False
    )


def test_step_answers_with_raw_without_allowlist(monkeypatch):
    monkeypatch.setattr(ws_module.settings, "report_commands", "")

    assert (
        ws_module._step_answers_with_raw(
            {"tool": "ops_run_command", "args": {"command": "uptime"}}
        )
        is False
    )


def test_uses_llm_summary_false_for_read_only_command_plan(monkeypatch):
    monkeypatch.setattr(ws_module.settings, "report_commands", "uptime")
    plan = build_plan(
        {
            "mode": "direct",
            "goal": "uptime",
            "steps": [
                {"tool": "ops_run_command", "args": {"command": "uptime"}, "why": "x"}
            ],
        }
    )
    observations = _health_observation("up 3 days")

    assert (
        ws_module._uses_llm_summary(plan, observations, raw_requested=False) is False
    )


def test_uses_llm_summary_true_for_mixed_plan(monkeypatch):
    monkeypatch.setattr(ws_module.settings, "report_commands", "uptime")
    plan = build_plan(
        {
            "mode": "plan",
            "goal": "health and config",
            "steps": [
                {"tool": "core_health", "args": {}, "why": "x"},
                {"tool": "ops_read_file", "args": {}, "why": "y"},
            ],
        }
    )
    observations = _health_observation("disk 42% used")

    assert (
        ws_module._uses_llm_summary(plan, observations, raw_requested=False) is True
    )


# --------------------------------------------------------------------------- #
# inbound payload redaction in logs
# --------------------------------------------------------------------------- #


def test_describe_client_message_redacts_content():
    assert ws_module._describe_client_message("a" * 500) == "text(500 chars)"

    described = ws_module._describe_client_message(
        {"action": "plan_edit", "steps": [1, 2, 3], "message": "secret"}
    )
    assert "secret" not in described
    assert "action='plan_edit'" in described
    assert "message=6 chars" in described
    assert "steps=3" in described


@pytest.mark.asyncio
async def test_websocket_logs_payload_summary_not_content(monkeypatch, caplog):
    _patch_db(monkeypatch)
    _patch_plan_runtime(monkeypatch)

    async def fake_route(user_text, session_id, **kwargs):
        return {"type": "text", "content": "ok"}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    secret = "TOP SECRET MESSAGE do-not-log"
    ws = FakeWebSocket([{"message": secret}], delay=0.05)
    ws.allow_disconnect.set()

    with caplog.at_level("INFO", logger="loomcore.websocket"):
        await ws_module.websocket_endpoint(ws, "sess")

    assert secret not in caplog.text
    assert "Received client message" in caplog.text
    assert "message=" in caplog.text


@pytest.mark.asyncio
async def test_disconnect_keeps_the_shared_window(monkeypatch):
    _patch_db(monkeypatch)
    monkeypatch.setattr(ws_module, "WS_MESSAGE_LIMIT_PER_MINUTE", 1)
    ws_module.reset_ws_message_limits()

    ws = FakeWebSocket([{"message": ""}])
    ws.allow_disconnect.set()

    await ws_module.websocket_endpoint(ws, "sess")

    # Closing a socket must not hand the client a fresh allowance: the window is
    # shared per identity, so the used slot survives the disconnect.
    assert ws_module._allow_ws_message("admin") is False


# --------------------------------------------------------------------------- #
# streaming deltas
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_plan_streams_summary_deltas(monkeypatch):
    _patch_db(monkeypatch)
    sent, send = _collector()
    plan = build_plan(
        {
                "goal": "health",
                "steps": [
                    {"tool": "core_health", "args": {}, "why": "baseline"},
                    {"tool": "ops_read_file", "args": {}, "why": "interfaces"},
                ],
        }
    )

    async def fake_summary(user_text, plan, observations, **kwargs):
        on_delta = kwargs.get("on_delta")
        for fragment in ("all ", "good"):
            if on_delta is not None:
                await on_delta(fragment)
        return "all good"

    _patch_plan_runtime(monkeypatch)
    monkeypatch.setattr(ws_module.airouter_with_plugins, "summarize_plan", fake_summary)

    await ws_module._run_plan(
        send, "sess", "check health", plan, {"tool": None, "plan": None}
    )

    deltas = [p for p in sent if p["type"] == "delta"]
    assert [p["content"] for p in deltas] == ["all ", "good"]
    message_ids = {p["message_id"] for p in deltas}
    assert len(message_ids) == 1
    response = next(p for p in sent if p["type"] == "response")
    assert response["content"] == "all good"
    assert response["message_id"] in message_ids


@pytest.mark.asyncio
async def test_plan_reuses_routing_message_id(monkeypatch):
    _patch_db(monkeypatch)
    sent, send = _collector()
    plan = build_plan(
        {
                "goal": "health",
                "steps": [
                    {"tool": "core_health", "args": {}, "why": "baseline"},
                    {"tool": "ops_read_file", "args": {}, "why": "interfaces"},
                ],
        }
    )

    async def fake_route(user_text, session_id, **kwargs):
        on_delta = kwargs.get("on_delta")
        if on_delta is not None:
            await on_delta("draft")
            await on_delta("", reset=True)
        return {"type": "plan", "plan": plan}

    async def fake_summary(user_text, plan, observations, **kwargs):
        on_delta = kwargs.get("on_delta")
        if on_delta is not None:
            await on_delta("summary text")
        return "summary text"

    _patch_plan_runtime(monkeypatch)
    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)
    monkeypatch.setattr(ws_module.airouter_with_plugins, "summarize_plan", fake_summary)

    await ws_module._process_user_message(send, "sess", "hi")

    deltas = [p for p in sent if p["type"] == "delta"]
    assert len({p["message_id"] for p in deltas}) == 1
    response = next(p for p in sent if p["type"] == "response")
    assert response["message_id"] == deltas[0]["message_id"]
    assert response["content"] == "summary text"


@pytest.mark.asyncio
async def test_text_reply_streams_and_reconciles(monkeypatch):
    _patch_db(monkeypatch)
    sent, send = _collector()

    async def fake_route(user_text, session_id, **kwargs):
        on_delta = kwargs.get("on_delta")
        if on_delta is not None:
            await on_delta("he")
            await on_delta("llo")
        return {"type": "text", "content": "hello"}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    await ws_module._process_user_message(send, "sess", "hi")

    deltas = [p for p in sent if p["type"] == "delta"]
    assert [p["content"] for p in deltas] == ["he", "llo"]
    response = next(p for p in sent if p["type"] == "response")
    assert response["content"] == "hello"
    assert response["message_id"] == deltas[0]["message_id"]


@pytest.mark.asyncio
async def test_reply_reask_emits_reset_delta(monkeypatch):
    _patch_db(monkeypatch)
    sent, send = _collector()

    async def fake_route(user_text, session_id, **kwargs):
        on_delta = kwargs.get("on_delta")
        if on_delta is not None:
            await on_delta("draft")
            await on_delta("", reset=True)
            await on_delta("final")
        return {"type": "text", "content": "final"}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    await ws_module._process_user_message(send, "sess", "hi")

    resets = [p for p in sent if p["type"] == "delta" and p.get("reset")]
    assert len(resets) == 1
    assert resets[0]["content"] == ""
    response = next(p for p in sent if p["type"] == "response")
    assert response["content"] == "final"
