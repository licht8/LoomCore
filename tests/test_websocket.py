"""Unit tests for the WebSocket chat interface (no DB, no real plugins)."""

import asyncio

import pytest
from fastapi import WebSocketDisconnect

from src.interfaces import websocket as ws_module


class FakeWebSocket:
    """Minimal stand-in for FastAPI's WebSocket."""

    def __init__(self, incoming: list[dict]):
        self._incoming = list(incoming)
        self.sent: list[dict] = []
        self.allow_disconnect = asyncio.Event()
        self.closed = False

    async def accept(self) -> None:
        pass

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def close(self, code: int | None = None, reason: str | None = None) -> None:
        self.closed = True

    async def receive_json(self) -> dict:
        # Yield so background tasks scheduled by the endpoint get a chance to run.
        await asyncio.sleep(0)
        if self._incoming:
            return self._incoming.pop(0)
        await self.allow_disconnect.wait()
        raise WebSocketDisconnect(code=1000)


def _patch_db(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc) -> bool:
            return False

    async def fake_ensure_session(db, session_id, *args, **kwargs) -> str:
        return session_id

    monkeypatch.setattr(ws_module, "async_session_factory", lambda: DummyContext())
    monkeypatch.setattr(ws_module, "ensure_session", fake_ensure_session)


async def test_process_user_message_reports_tool_error(monkeypatch):
    sent: list[dict] = []
    saved: list[dict] = []

    async def send(payload: dict) -> None:
        sent.append(payload)

    async def fake_route(user_text: str, session_id: str) -> dict:
        return {
            "type": "tool_call",
            "tool_name": "wg_interface_status",
            "tool_args": {"interface": "wg0"},
        }

    async def fake_save(session_id, tool_name, arguments, content, **kwargs):
        saved.append({"tool_name": tool_name, "content": content, **kwargs})

    async def boom(tool_name: str, arguments: dict | None = None) -> dict:
        raise RuntimeError("Interface wg0 is not active")

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)
    monkeypatch.setattr(ws_module, "_execute_tool", boom)
    monkeypatch.setattr(ws_module, "_save_tool_message", fake_save)

    await ws_module._process_user_message(send, "sess", "status")

    assert sent[0]["type"] == "thinking"
    assert sent[1]["type"] == "tool_call"
    assert sent[-1]["type"] == "tool_response"
    assert sent[-1]["status"] == "error"
    assert sent[-1]["tool"] == "wg_interface_status"
    assert "not active" in sent[-1]["content"]
    # The failure must also be persisted so the card survives a reload.
    assert saved and saved[0]["status"] == "error"
    assert saved[0]["tool_name"] == "wg_interface_status"


async def test_process_user_message_success(monkeypatch):
    sent: list[dict] = []

    async def send(payload: dict) -> None:
        sent.append(payload)

    async def fake_route(user_text: str, session_id: str) -> dict:
        return {"type": "text", "content": "hello"}

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)

    await ws_module._process_user_message(send, "sess", "hi")

    assert sent[-1] == {"type": "response", "content": "hello"}


@pytest.mark.asyncio
async def test_websocket_deny_sends_tool_denied(monkeypatch):
    _patch_db(monkeypatch)
    ws = FakeWebSocket([{"action": "deny"}])
    task = asyncio.create_task(ws_module.websocket_endpoint(ws, "sess"))

    await asyncio.sleep(0.05)
    ws.allow_disconnect.set()
    await asyncio.wait_for(task, timeout=1)

    assert {"type": "tool_denied"} in ws.sent


@pytest.mark.asyncio
async def test_websocket_cancel_aborts_running_request(monkeypatch):
    _patch_db(monkeypatch)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow_process(send, session_id: str, user_text: str, tracker=None) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(ws_module, "_process_user_message", slow_process)

    ws = FakeWebSocket([{"message": "hello"}, {"action": "cancel"}])
    task = asyncio.create_task(ws_module.websocket_endpoint(ws, "sess"))

    await asyncio.wait_for(started.wait(), timeout=1)
    await asyncio.wait_for(cancelled.wait(), timeout=1)
    ws.allow_disconnect.set()
    await asyncio.wait_for(task, timeout=1)

    assert {"type": "cancelled"} in ws.sent


@pytest.mark.asyncio
async def test_websocket_busy_while_running(monkeypatch):
    _patch_db(monkeypatch)

    async def slow_process(send, session_id: str, user_text: str, tracker=None) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(ws_module, "_process_user_message", slow_process)

    ws = FakeWebSocket([{"message": "first"}, {"message": "second"}])
    task = asyncio.create_task(ws_module.websocket_endpoint(ws, "sess"))

    await asyncio.sleep(0.05)
    ws.allow_disconnect.set()
    await asyncio.wait_for(task, timeout=1)

    assert {"type": "busy"} in ws.sent


@pytest.mark.asyncio
async def test_run_tool_safely_persists_error(monkeypatch):
    sent: list[dict] = []
    saved: list[dict] = []

    async def send(payload: dict) -> None:
        sent.append(payload)

    async def fake_save(session_id, tool_name, arguments, content, **kwargs):
        saved.append({"tool_name": tool_name, "content": content, **kwargs})

    async def boom(tool_name: str, arguments: dict | None = None) -> dict:
        raise RuntimeError("plugin exploded")

    monkeypatch.setattr(ws_module, "_execute_tool", boom)
    monkeypatch.setattr(ws_module, "_save_tool_message", fake_save)

    tracker: dict = {"tool": None}
    await ws_module._run_tool_safely(
        send, "sess", "wg_interface_status", {}, tracker=tracker
    )

    assert sent[-1]["status"] == "error"
    assert saved and saved[0]["status"] == "error"
    assert "plugin exploded" in saved[0]["content"]
    # The tracker is cleared once the tool reaches a terminal (non-cancel) state.
    assert tracker["tool"] is None


@pytest.mark.asyncio
async def test_websocket_cancel_persists_tool(monkeypatch):
    _patch_db(monkeypatch)
    started = asyncio.Event()
    saved: list[dict] = []

    async def fake_route(user_text: str, session_id: str) -> dict:
        return {
            "type": "tool_call",
            "tool_name": "wg_interface_status",
            "tool_args": {"interface": "wg0"},
        }

    async def slow_execute(tool_name: str, arguments: dict | None = None) -> dict:
        started.set()
        await asyncio.Event().wait()

    async def fake_save(session_id, tool_name, arguments, content, **kwargs):
        saved.append({"tool_name": tool_name, "content": content, **kwargs})

    monkeypatch.setattr(ws_module.airouter_with_plugins, "route", fake_route)
    monkeypatch.setattr(ws_module, "_execute_tool", slow_execute)
    monkeypatch.setattr(ws_module, "_save_tool_message", fake_save)

    ws = FakeWebSocket([{"message": "status"}, {"action": "cancel"}])
    task = asyncio.create_task(ws_module.websocket_endpoint(ws, "sess"))

    await asyncio.wait_for(started.wait(), timeout=1)
    ws.allow_disconnect.set()
    await asyncio.wait_for(task, timeout=1)

    assert {"type": "cancelled"} in ws.sent
    assert saved and saved[0]["tool_name"] == "wg_interface_status"
    assert saved[0]["status"] == "cancelled"
    assert saved[0]["summary"] == "Cancelled"


@pytest.mark.asyncio
async def test_websocket_deny_persists_tool(monkeypatch):
    _patch_db(monkeypatch)
    saved: list[dict] = []

    async def fake_save(session_id, tool_name, arguments, content, **kwargs):
        saved.append(
            {
                "tool_name": tool_name,
                "arguments": arguments,
                "content": content,
                **kwargs,
            }
        )

    monkeypatch.setattr(ws_module, "_save_tool_message", fake_save)

    ws = FakeWebSocket(
        [{"action": "deny", "tool": "wg_remove_peer", "args": {"name": "laptop"}}]
    )
    task = asyncio.create_task(ws_module.websocket_endpoint(ws, "sess"))

    await asyncio.sleep(0.05)
    ws.allow_disconnect.set()
    await asyncio.wait_for(task, timeout=1)

    assert {"type": "tool_denied"} in ws.sent
    assert saved and saved[0]["tool_name"] == "wg_remove_peer"
    assert saved[0]["status"] == "cancelled"
    assert saved[0]["summary"] == "Denied"
    assert saved[0]["arguments"] == {"name": "laptop"}
