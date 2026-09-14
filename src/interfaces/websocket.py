import asyncio
import contextlib
import logging
import time
import traceback
from collections import deque
from collections.abc import Awaitable, Callable

from fastapi import WebSocket, WebSocketDisconnect

from src.core.agent import (
    execute_plan,
    next_revision,
    plan_is_direct,
    plan_requires_approval,
    validate_plan,
)
from src.core.audit import record_mutation_isolated
from src.core.confirmations import (
    CONFIRMATION_TIMEOUT_SECONDS,
    parse_confirmation,
)
from src.core.core_tools import CORE_TOOL_NAMES, dispatch_core_tool
from src.core.timing import log_stage, span
from src.db.database import async_session_factory
from src.db.session import add_assistant_message, ensure_session
from src.interfaces.tool_events import (
    build_artifacts as _build_artifacts,
)
from src.interfaces.tool_events import (
    extract_text as _extract_text,
)
from src.interfaces.tool_events import (
    plan_done_payload,
    plan_proposed_payload,
    plan_step_payload,
    tool_call_payload,
    tool_success_payload,
)
from src.interfaces.tool_events import (
    save_plan_message as _save_plan_message,
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

# Inbound WebSocket messages allowed per identity per minute. slowapi cannot
# wrap websockets, so the interface enforces its own limit: the counter is
# shared per authenticated identity (all sessions of the single admin), so
# opening more sockets or rotating the session id cannot raise it.
WS_MESSAGE_LIMIT_PER_MINUTE = 60
_WS_LIMIT_WINDOW_SECONDS = 60.0
_ws_message_times: dict[str, deque[float]] = {}


def _allow_ws_message(identity: str) -> bool:
    """Sliding-window limit on inbound WebSocket messages for one identity."""
    now = time.monotonic()
    window = _ws_message_times.setdefault(identity, deque())
    cutoff = now - _WS_LIMIT_WINDOW_SECONDS
    while window and window[0] <= cutoff:
        window.popleft()
    if len(window) >= WS_MESSAGE_LIMIT_PER_MINUTE:
        return False
    window.append(now)
    return True


def reset_ws_message_limits() -> None:
    """Clear the WebSocket message limiter (used by tests)."""
    _ws_message_times.clear()


async def _execute_tool(tool_name: str, arguments: dict | None = None) -> dict:
    """Run a core meta-tool or the plugin tool that owns the name."""
    async with span("execute_tool", tool=tool_name):
        if tool_name in CORE_TOOL_NAMES:
            return await dispatch_core_tool(tool_name, arguments)
        return await plugin_manager.execute_tool_by_name(tool_name, arguments)


async def _run_plan(
    send: Send,
    session_id: str,
    user_text: str,
    plan: dict,
    tracker: dict | None = None,
) -> None:
    """Replay an approved (or all-safe) plan, then summarize the outcome.

    Each step is mirrored to the UI as a tool card plus a plan_step event, and
    persisted so the cards survive a reload. The plan is the sole dispatch path:
    the executor replays exactly the stored tool and arguments.
    """
    if tracker is not None:
        tracker["plan"] = plan
    plan_started = time.monotonic()

    # A direct action is a single obvious call: it runs as a plain tool card,
    # without a user-facing plan artifact or plan events.
    show_plan = not plan_is_direct(plan)

    async def audit_step(step: dict, status: str, error: str | None = None) -> None:
        """Record a tamper-evident audit row for every mutating step.

        Safe (read-only) steps are not recorded. Auditing is best-effort: a
        failure here must never break the plan.
        """
        risk = step.get("risk")
        if not risk or risk == "safe":
            return
        with contextlib.suppress(Exception):
            await record_mutation_isolated(
                tool=step["tool"],
                arguments=step.get("args"),
                risk=risk,
                status=status,
                session_id=session_id,
                plan_id=plan.get("plan_id"),
                revision=plan.get("revision"),
                error=error,
            )

    async def emit(event: dict) -> None:
        if event.get("type") != "plan_step":
            return
        step = event["step"]
        phase = event["phase"]
        if phase == "started":
            if tracker is not None:
                tracker["tool"] = {
                    "name": step["tool"],
                    "args": step.get("args"),
                    "risk": step.get("risk"),
                }
            await send(tool_call_payload(step["tool"], step.get("args")))
        elif phase == "done":
            if tracker is not None:
                tracker["tool"] = None
            raw = event.get("result")
            content = _extract_text(raw)
            artifacts = _build_artifacts(raw)
            await _save_tool_message(
                session_id,
                step["tool"],
                step.get("args"),
                content,
                status="success",
                summary="Done",
                risk=step.get("risk"),
                artifacts=artifacts,
            )
            await send(
                tool_success_payload(step["tool"], content, step.get("risk"), artifacts)
            )
            await audit_step(step, "success")
        elif phase == "failed":
            if tracker is not None:
                tracker["tool"] = None
            message = event.get("error") or f"Step failed: {step.get('tool')}"
            await _save_tool_message(
                session_id,
                step["tool"],
                step.get("args"),
                message,
                status="error",
                summary="Error",
                risk=step.get("risk"),
            )
            await send(_tool_error_payload(step["tool"], message, step.get("risk")))
            await audit_step(step, "failed", message)
        if show_plan:
            await send(plan_step_payload(plan, step, phase))

    execute_started = time.monotonic()
    try:
        outcome = await execute_plan(plan, dispatch=_execute_tool, emit=emit)
        final_plan = outcome["plan"]
        observations = outcome["results"]
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # An unexpected executor/emit failure must still terminate the turn:
        # mark the plan failed and fall through to a summary + plan_done so the
        # UI never gets stuck waiting.
        logger.error(f"Plan execution crashed: {exc}")
        logger.error(traceback.format_exc())
        plan["status"] = "failed"
        if tracker is not None:
            tracker["tool"] = None
        final_plan = plan
        observations = []
        with contextlib.suppress(Exception):
            await send({"type": "error", "error": str(exc)})
    finally:
        log_stage(
            "execute_plan",
            time.monotonic() - execute_started,
            steps=len(plan.get("steps", [])),
            show_plan=show_plan,
        )

    if show_plan:
        persist_started = time.monotonic()
        with contextlib.suppress(Exception):
            await _save_plan_message(session_id, final_plan)
        log_stage("save_plan", time.monotonic() - persist_started)

    summary_started = time.monotonic()
    if show_plan:
        summary = await _summarize(user_text, final_plan, observations)
    else:
        # Direct action: the tool output already is the answer; skip the second
        # LLM round trip that would only restate it.
        summary = _direct_summary(final_plan, observations)
    log_stage("summary", time.monotonic() - summary_started, llm=show_plan)
    if summary:
        with contextlib.suppress(Exception):
            async with async_session_factory() as db:
                await add_assistant_message(db, session_id, summary)
        with contextlib.suppress(Exception):
            await send({"type": "response", "content": summary})

    # plan_done is terminal for the UI: send it only once the summary is out so
    # the client keeps the turn busy until the whole request has finished.
    if show_plan:
        with contextlib.suppress(Exception):
            await send(plan_done_payload(final_plan))

    if tracker is not None:
        tracker["plan"] = None
    log_stage("plan_total", time.monotonic() - plan_started, show_plan=show_plan)


async def _summarize(user_text: str, plan: dict, observations: list[dict]) -> str:
    """Summarize a finished plan, falling back to a terse status on failure."""
    try:
        return await airouter_with_plugins.summarize_plan(user_text, plan, observations)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error(f"Plan summary failed: {exc}")
        return _fallback_summary(plan)


def _fallback_summary(plan: dict) -> str:
    goal = plan.get("goal") or "Plan"
    status = plan.get("status", "done")
    lines = [f"{goal} ({status})."]
    lines.extend(
        f"- {step.get('tool')}: {step.get('status')}" for step in plan.get("steps", [])
    )
    return "\n".join(lines)


def _direct_summary(plan: dict, observations: list[dict]) -> str:
    """Deterministic summary for a direct action: the tool output is the answer.

    A direct action is a single obvious call, so a second LLM round trip to
    restate its result only adds latency. Surface the raw output instead.
    """
    texts = [
        _extract_text(item.get("result"))
        for item in observations
        if item.get("result") is not None
    ]
    texts = [text for text in texts if text]
    if texts:
        return "\n\n".join(texts)
    return _fallback_summary(plan)


async def _guarded(coro) -> None:
    """Run a background request, logging any unexpected failure."""
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error(f"Background request failed: {exc}")


def _remember_pending(pending: dict | None, plan: dict, user_text: str) -> None:
    """Record a plan awaiting the user's confirmation (with a fresh timeout)."""
    if pending is None:
        return
    pending["plan"] = plan
    pending["user_text"] = user_text
    pending["created_at"] = time.monotonic()
    pending["reasked"] = False


async def _process_user_message(
    send: Send,
    session_id: str,
    user_text: str,
    tracker: dict | None = None,
    pending: dict | None = None,
) -> None:
    """Plan or answer a user message, then run an all-safe plan immediately.

    A plan containing any non-safe step is stored in ``pending`` and execution
    waits for the user to approve, edit or reject it.
    """
    started = time.monotonic()
    answer_type: str | None = None
    try:
        await send({"type": "thinking", "status": "processing"})
        logger.info(f"🧠 Sending request to the LLM for session={session_id}...")

        route_started = time.monotonic()
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
        finally:
            log_stage("route_call", time.monotonic() - route_started)

        unavailable = result.get("tool_schema_warning")
        if unavailable:
            await send({"type": "schema_warning", "unavailable_plugins": unavailable})

        answer_type = result.get("type")

        if answer_type == "text":
            await send({"type": "response", "content": result.get("content", "")})
            return

        if answer_type != "plan":
            return

        plan = result.get("plan") or {}
        requires_approval = plan_requires_approval(plan)

        if plan_is_direct(plan):
            if not requires_approval:
                # One obvious, safe action: just do it, no plan card.
                await _run_plan(send, session_id, user_text, plan, tracker)
                return
            # Direct but mutating: ask for a plain-language confirmation.
            goal = plan.get("goal") or "Proceed"
            await send({"type": "response", "content": f"{goal} Reply yes to proceed."})
            _remember_pending(pending, plan, user_text)
            return

        if not requires_approval:
            # An all-safe plan runs immediately, so it is approved by definition;
            # reflect that in the proposed event so the card is not shown as
            # "Awaiting approval".
            plan["status"] = "approved"

        await send(plan_proposed_payload(plan, requires_approval))

        if requires_approval:
            _remember_pending(pending, plan, user_text)
            return

        await _run_plan(send, session_id, user_text, plan, tracker)
    finally:
        log_stage(
            "turn_total", time.monotonic() - started, kind=answer_type or "unknown"
        )


async def websocket_endpoint(
    websocket: WebSocket, session_id: str, identity: str = "admin"
):
    """WebSocket endpoint for live chat with the AI.

    Logic:
      1. Accept the connection.
      2. Loop reading JSON from the client (messages, plan actions, cancel).
      3. Run each request in a background task so the loop stays responsive.
      4. action=cancel — abort the in-flight request and notify the UI.
      5. action=plan_approve/plan_edit/plan_reject — act on the pending plan.
      6. All-safe plans run immediately; plans with a risky step wait.
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
    # Connection-local record of what is running, so a cancel can persist
    # cancelled cards even after the task is torn down.
    tracker: dict = {"tool": None, "plan": None}
    # A plan waiting for the user's decision.
    pending: dict = {
        "plan": None,
        "user_text": "",
        "created_at": 0.0,
        "reasked": False,
    }
    # One message typed while a turn was running; newest wins, dispatched on
    # completion instead of being dropped.
    queued_text: str | None = None

    async def cancel_current() -> None:
        nonlocal current
        task = current
        current = None
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def _drain_queue() -> None:
        nonlocal queued_text
        text = queued_text
        queued_text = None
        if text:
            start(_process_user_message(send, session_id, text, tracker, pending))

    def _on_task_done(task: asyncio.Task) -> None:
        if current is task:
            _drain_queue()

    def start(coro) -> None:
        nonlocal current
        current = asyncio.create_task(_guarded(coro))
        current.add_done_callback(_on_task_done)

    def is_busy() -> bool:
        return bool(current and not current.done())

    async def persist_cancelled_tool() -> None:
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

    async def persist_cancelled_plan() -> None:
        plan = tracker.get("plan")
        tracker["plan"] = None
        if plan:
            plan["status"] = "cancelled"
            with contextlib.suppress(Exception):
                await _save_plan_message(session_id, plan)
            await send(plan_done_payload(plan))

    def clear_pending() -> None:
        pending["plan"] = None
        pending["user_text"] = ""
        pending["created_at"] = 0.0
        pending["reasked"] = False

    async def approve_plan(plan: dict, user_text: str) -> None:
        plan["status"] = "approved"
        if plan_is_direct(plan):
            start(_run_plan(send, session_id, user_text, plan, tracker))
            return
        with contextlib.suppress(Exception):
            await _save_plan_message(session_id, plan)
        # Reflect the approval in the UI before execution starts; with
        # requires_approval now false the card leaves "Awaiting approval".
        await send(plan_proposed_payload(plan, False))
        start(_run_plan(send, session_id, user_text, plan, tracker))

    async def reject_plan(plan: dict) -> None:
        plan["status"] = "rejected"
        if plan_is_direct(plan):
            step = (plan.get("steps") or [{}])[0]
            with contextlib.suppress(Exception):
                await _save_tool_message(
                    session_id,
                    step.get("tool", ""),
                    step.get("args"),
                    "",
                    status="cancelled",
                    summary="Rejected",
                    risk=step.get("risk"),
                )
            await send({"type": "response", "content": "Cancelled."})
            return
        with contextlib.suppress(Exception):
            await _save_plan_message(session_id, plan)
        await send(plan_done_payload(plan))

    async def handle_message(data) -> None:
        nonlocal queued_text
        action = data.get("action") if isinstance(data, dict) else None

        # Cancel the request currently in flight, if any
        if action == "cancel":
            queued_text = None
            await cancel_current()
            await persist_cancelled_tool()
            await persist_cancelled_plan()
            clear_pending()
            await send({"type": "cancelled"})
            return

        # User rejected the pending plan
        if action == "plan_reject":
            plan = pending.get("plan")
            clear_pending()
            if plan:
                await reject_plan(plan)
            return

        # User approved the pending plan
        if action == "plan_approve":
            if is_busy():
                await send({"type": "busy"})
                return
            plan = pending.get("plan")
            if not plan:
                return
            user_text = pending.get("user_text", "")
            clear_pending()
            await approve_plan(plan, user_text)
            return

        # User edited the pending plan
        if action == "plan_edit":
            if is_busy():
                await send({"type": "busy"})
                return
            plan = pending.get("plan")
            steps = data.get("steps")
            if not plan or not isinstance(steps, list):
                return
            revised = next_revision(plan, steps)
            known = await airouter_with_plugins.known_tool_names()
            errors = validate_plan(revised, known)
            if errors:
                await send(
                    {"type": "error", "error": "Invalid plan: " + "; ".join(errors)}
                )
                return
            user_text = pending.get("user_text", "")
            _remember_pending(pending, revised, user_text)
            with contextlib.suppress(Exception):
                await _save_plan_message(session_id, revised)
            requires_approval = plan_requires_approval(revised)
            await send(plan_proposed_payload(revised, requires_approval))
            if not requires_approval:
                clear_pending()
                start(_run_plan(send, session_id, user_text, revised, tracker))
            return

        # Regular text message from the user
        user_text = data if isinstance(data, str) else data.get("message", "")
        if not user_text:
            return
        if is_busy():
            # Keep the newest message; it runs when the current turn ends.
            queued_text = user_text
            return

        # A pending plan (direct action or plan card) awaiting a text yes/no.
        pending_plan = pending.get("plan")
        if pending_plan is not None:
            age = time.monotonic() - float(pending.get("created_at") or 0.0)
            if age <= CONFIRMATION_TIMEOUT_SECONDS:
                decision = parse_confirmation(user_text)
                if decision == "approve":
                    saved_text = pending.get("user_text", "")
                    clear_pending()
                    await approve_plan(pending_plan, saved_text)
                    return
                if decision == "reject":
                    clear_pending()
                    await reject_plan(pending_plan)
                    return
                if plan_is_direct(pending_plan) and not pending.get("reasked"):
                    pending["reasked"] = True
                    pending["created_at"] = time.monotonic()
                    await send(
                        {
                            "type": "response",
                            "content": "Reply yes to proceed, or no to cancel.",
                        }
                    )
                    return
            # Non-committal, expired, or a second unclear reply: terminalize a
            # plan card so it never sticks, then route the message fresh.
            clear_pending()
            if not plan_is_direct(pending_plan):
                await reject_plan(pending_plan)

        start(_process_user_message(send, session_id, user_text, tracker, pending))

    try:
        while True:
            # Read JSON from the client
            data = await websocket.receive_json()
            logger.info(f"📥 Received client message: {data}")
            if not _allow_ws_message(identity):
                await send(
                    {
                        "type": "error",
                        "error": "Rate limit exceeded. Please slow down.",
                    }
                )
                continue
            try:
                await handle_message(data)
            except WebSocketDisconnect:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # One bad message must not tear down the socket and jam the
                # client; report it and keep the loop alive.
                logger.error(f"❌ Error handling client message: {exc}")
                logger.error(traceback.format_exc())
                with contextlib.suppress(Exception):
                    await send({"type": "error", "error": str(exc)})

    except WebSocketDisconnect:
        logger.info(f"🔌 WebSocket disconnected for session {session_id}")
    except Exception as exc:
        with contextlib.suppress(Exception):
            await send({"type": "error", "error": str(exc)})
        logger.error(f"❌ Fatal WebSocket error for session {session_id}: {exc}")
        logger.error(traceback.format_exc())
    finally:
        await cancel_current()
