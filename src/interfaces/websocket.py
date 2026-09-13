import asyncio
import contextlib
import logging
import traceback
from collections.abc import Awaitable, Callable

from fastapi import WebSocket, WebSocketDisconnect

from src.core.security import classify_tool_risk
from src.db.database import async_session_factory
from src.db.session import ensure_session
from src.interfaces.tool_events import (
    approval_request_payload,
    tool_call_payload,
    tool_success_payload,
)
from src.interfaces.tool_events import (
    build_artifacts as _build_artifacts,
)
from src.interfaces.tool_events import (
    extract_text as _extract_text,
)
from src.interfaces.tool_events import (
    save_tool_message as _save_tool_message,
)
from src.interfaces.tool_events import (
    tool_error_payload as _tool_error_payload,
)
from src.llm.client import LlmError
from src.llm.router import AIRouter
from src.plugins import plugin_manager

logger = logging.getLogger("loomcore.websocket")

# Shared AIRouter instance using the shared plugin_manager
airouter_with_plugins = AIRouter(plugin_manager=plugin_manager)

# Async function that serializes writes to the WebSocket.
Send = Callable[[dict], Awaitable[None]]


async def _execute_tool(tool_name: str, arguments: dict | None = None) -> dict:
    """Run a tool through the plugin that owns it."""
    return await plugin_manager.execute_tool_by_name(tool_name, arguments)


async def _run_tool_and_notify(
    send: Send,
    session_id: str,
    tool_name: str,
    arguments: dict | None,
    risk: str | None = None,
) -> None:
    """Run a tool, save the result, and send it to the UI."""
    raw_result = await _execute_tool(tool_name, arguments)
    content_text = _extract_text(raw_result)
    artifacts = _build_artifacts(raw_result)

    await _save_tool_message(
        session_id,
        tool_name,
        arguments,
        content_text,
        status="success",
        summary="Done",
        risk=risk,
        artifacts=artifacts,
    )

    await send(tool_success_payload(tool_name, content_text, risk, artifacts))


async def _run_tool_safely(
    send: Send,
    session_id: str,
    tool_name: str,
    arguments: dict | None,
    risk: str | None = None,
    tracker: dict | None = None,
) -> None:
    """Run a tool and always emit a terminal event, even on failure.

    ``tracker`` is a connection-local dict the receive loop watches so it can
    persist a cancellation; it is left set when the task is cancelled.
    """
    if tracker is not None:
        tracker["tool"] = {"name": tool_name, "args": arguments, "risk": risk}
    try:
        await _run_tool_and_notify(send, session_id, tool_name, arguments, risk=risk)
    except asyncio.CancelledError:
        # Leave ``tracker`` set so the receive loop can persist the cancellation.
        raise
    except Exception as exc:
        if tracker is not None:
            tracker["tool"] = None
        logger.error(f"Failed to run tool '{tool_name}': {exc}")
        message = f"Failed to call '{tool_name}': {exc}"
        with contextlib.suppress(Exception):
            await _save_tool_message(
                session_id,
                tool_name,
                arguments,
                message,
                status="error",
                summary="Error",
                risk=risk,
            )
        await send(_tool_error_payload(tool_name, message, risk))
    else:
        if tracker is not None:
            tracker["tool"] = None


async def _guarded(coro) -> None:
    """Run a background request, logging any unexpected failure."""
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error(f"Background request failed: {exc}")


async def _process_user_message(
    send: Send,
    session_id: str,
    user_text: str,
    tracker: dict | None = None,
) -> None:
    """Route a user message, then surface the response or run the chosen tool."""
    await send({"type": "thinking", "status": "processing"})
    logger.info(f"🧠 Sending request to the LLM for session={session_id}...")

    try:
        result = await airouter_with_plugins.route(user_text, session_id)
        logger.info(
            f"✅ LLM responded, type={result.get('type')}, session={session_id}"
        )
    except asyncio.CancelledError:
        raise
    except LlmError as exc:
        await send({"type": "error", "error": str(exc)})
        return
    except Exception as exc:
        await send({"type": "error", "error": f"{type(exc).__name__}: {exc}"})
        return

    unavailable = result.get("tool_schema_warning")
    if unavailable:
        await send({"type": "schema_warning", "unavailable_plugins": unavailable})

    answer_type = result.get("type")

    if answer_type == "text":
        await send({"type": "response", "content": result.get("content", "")})
        return

    if answer_type != "tool_call":
        return

    tool_name = result.get("tool_name", "unknown")
    tool_args = result.get("tool_args")

    # Classify the risk
    risk = classify_tool_risk(tool_name)
    await send(tool_call_payload(tool_name, tool_args))

    if risk == "dangerous":
        await send(approval_request_payload(tool_name, tool_args))
    else:
        # Safe/Moderate — run automatically through the plugin
        await _run_tool_safely(
            send, session_id, tool_name, tool_args, risk=risk, tracker=tracker
        )


async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for live chat with the AI.

    Logic:
      1. Accept the connection.
      2. Loop reading JSON from the client (messages, approve/deny, cancel).
      3. Run each request in a background task so the loop stays responsive.
      4. For action=cancel — abort the in-flight request and notify the UI.
      5. For action=approve/deny — run or refuse the plugin tool.
      6. Safe/Moderate tools — run automatically; dangerous ones need approval.
      7. On disconnect — abort the in-flight request.
    """
    await websocket.accept()

    # Ensure the session exists (create the user and session if needed)
    async with async_session_factory() as ws_db:
        await ensure_session(ws_db, session_id)

    send_lock = asyncio.Lock()

    async def send(payload: dict) -> None:
        async with send_lock:
            await websocket.send_json(payload)

    current: asyncio.Task | None = None
    # Connection-local record of the tool currently running, so a cancel can
    # persist a "cancelled" card even after the task is torn down.
    tracker: dict = {"tool": None}

    async def cancel_current() -> None:
        nonlocal current
        task = current
        current = None
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def start(coro) -> None:
        nonlocal current
        current = asyncio.create_task(_guarded(coro))

    try:
        while True:
            # Read JSON from the client
            data = await websocket.receive_json()
            logger.info(f"📥 Received client message: {data}")

            action = data.get("action") if isinstance(data, dict) else None

            # Cancel the request currently in flight, if any
            if action == "cancel":
                await cancel_current()
                active = tracker.get("tool")
                tracker["tool"] = None
                if active:
                    with contextlib.suppress(Exception):
                        await _save_tool_message(
                            session_id,
                            active["name"],
                            active.get("args"),
                            "",
                            status="cancelled",
                            summary="Cancelled",
                            risk=active.get("risk"),
                        )
                await send({"type": "cancelled"})
                continue

            # User refused a dangerous tool
            if action == "deny":
                tool = data.get("tool")
                if tool:
                    with contextlib.suppress(Exception):
                        await _save_tool_message(
                            session_id,
                            tool,
                            data.get("args") or {},
                            "",
                            status="cancelled",
                            summary="Denied",
                        )
                await send({"type": "tool_denied"})
                continue

            # User approved a dangerous tool
            if action == "approve":
                if current and not current.done():
                    await send({"type": "busy"})
                    continue
                tool = data.get("tool", "unknown")
                tool_args = data.get("args") or {}
                start(_run_tool_safely(send, session_id, tool, tool_args, tracker=tracker))
                continue

            # Regular text message from the user
            user_text = data if isinstance(data, str) else data.get("message", "")
            if not user_text:
                continue
            if current and not current.done():
                await send({"type": "busy"})
                continue
            start(_process_user_message(send, session_id, user_text, tracker))

    except WebSocketDisconnect:
        logger.info(f"🔌 WebSocket disconnected for session {session_id}")
    except Exception as exc:
        with contextlib.suppress(Exception):
            await send({"type": "error", "error": str(exc)})
        logger.error(f"❌ Fatal WebSocket error for session {session_id}: {exc}")
        logger.error(traceback.format_exc())
    finally:
        await cancel_current()
