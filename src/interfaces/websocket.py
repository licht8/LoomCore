import asyncio
import contextlib
import logging
import os
import re
import time
import traceback
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect

from src.config import settings
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
from src.core.core_tools import (
    CORE_TOOL_NAMES,
    _is_report_tool,
    _report_arg_for,
    dispatch_core_tool,
    resolve_tool_risk,
)
from src.core.timing import log_stage, span
from src.db.database import async_session_factory
from src.db.session import add_assistant_message, ensure_session
from src.db.session import claim_pending as claim_stored_pending
from src.db.session import clear_pending as clear_stored_pending
from src.db.session import get_pending as get_stored_pending
from src.db.session import set_pending as set_stored_pending
from src.interfaces.events import (
    busy_payload,
    cancelled_payload,
    delta_payload,
    error_payload,
    plan_progress_payload,
    response_payload,
    schema_warning_payload,
    thinking_payload,
)
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
from src.llm.exceptions import LlmTimeoutError
from src.llm.router import AIRouter
from src.plugins import plugin_manager

logger = logging.getLogger("loomcore.websocket")


def _safe_error_message(exc: Exception) -> str:
    """Map an internal exception to a category-only client message.

    Raw exception text can leak provider URLs, keys or internal paths, so the
    client only ever sees one of these categories; the real exception is logged
    server-side by the caller.
    """
    if isinstance(exc, (LlmTimeoutError, TimeoutError)):
        return "The language model timed out."
    if isinstance(exc, LlmError):
        return "The language model request failed."
    return "Internal error."

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
    # An empty window just means every prior timestamp aged out; the key is
    # already present, so the new message is appended to it below.
    window.append(now)
    return True


def reset_ws_message_limits() -> None:
    """Clear the WebSocket message limiter (used by tests)."""
    _ws_message_times.clear()


def _describe_client_message(data) -> str:
    """Summarize an inbound frame for logs without exposing its content.

    Client frames may carry private conversation text or plan steps; logging
    them verbatim would put user content into the application log.
    """
    if isinstance(data, str):
        return f"text({len(data)} chars)"
    if isinstance(data, dict):
        action = data.get("action")
        parts = [f"action={action!r}"] if action else []
        message = data.get("message")
        if isinstance(message, str):
            parts.append(f"message={len(message)} chars")
        steps = data.get("steps")
        if isinstance(steps, list):
            parts.append(f"steps={len(steps)}")
        if not parts:
            parts.append(f"keys={sorted(data)}")
        return "dict(" + ", ".join(parts) + ")"
    if isinstance(data, list):
        return f"list({len(data)})"
    return type(data).__name__


async def _execute_tool(
    tool_name: str, arguments: dict | None = None, *, approved: bool = False
) -> dict:
    """Run a core meta-tool or the plugin tool that owns the name."""
    async with span("execute_tool", tool=tool_name):
        if tool_name in CORE_TOOL_NAMES:
            return await dispatch_core_tool(tool_name, arguments, approved=approved)
        return await plugin_manager.execute_tool_by_name(tool_name, arguments)


async def _run_plan(
    send: Send,
    session_id: str,
    user_text: str,
    plan: dict,
    tracker: dict | None = None,
    *,
    message_id: str | None = None,
) -> None:
    """Replay an approved (or all-safe) plan, then summarize the outcome.

    Each step is mirrored to the UI as a tool card plus a plan_step event, and
    persisted so the cards survive a reload. The plan is the sole dispatch path:
    the executor replays exactly the stored tool and arguments.

    ``message_id`` ties the summary deltas to a bubble opened earlier in the same
    turn (the routing reply stream); when omitted (approve/re-run paths) a fresh
    id is allocated.
    """
    if tracker is not None:
        tracker["plan"] = plan
    plan_started = time.monotonic()

    # A direct action is a single obvious call: it runs as a plain tool card,
    # without a user-facing plan artifact or plan events.
    show_plan = not plan_is_direct(plan)

    # Whether the raw tool output is the answer is known up front (it depends
    # only on the steps and the raw-output request), so the cards can be marked
    # compact before execution starts and the summary decision stays consistent.
    raw_requested = _wants_raw_output(user_text)
    answers_with_raw = _plan_answers_with_raw(plan, raw_requested=raw_requested)

    async def audit_step(step: dict, status: str, error: str | None = None) -> None:
        """Record a tamper-evident audit row for every mutating step.

        Safe (read-only) steps are not recorded. Auditing is best-effort: a
        failure here must never break the plan.
        """
        risk = step.get("risk")
        if not risk or risk == "safe":
            return
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
            tool_format = "markdown" if _is_report_tool(step["tool"]) else "text"
            try:
                await _save_tool_message(
                    session_id,
                    step["tool"],
                    step.get("args"),
                    content,
                    status="success",
                    summary="Done",
                    risk=step.get("risk"),
                    artifacts=artifacts,
                    format=tool_format,
                    compact=answers_with_raw,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to persist success tool message for %s: %s",
                    step["tool"],
                    exc,
                )
            await send(
                tool_success_payload(
                    step["tool"],
                    content,
                    step.get("risk"),
                    artifacts,
                    format=tool_format,
                    compact=answers_with_raw,
                )
            )
            await audit_step(step, "success")
        elif phase == "failed":
            if tracker is not None:
                tracker["tool"] = None
            message = event.get("error") or f"Step failed: {step.get('tool')}"
            tool_format = "markdown" if _is_report_tool(step["tool"]) else "text"
            try:
                await _save_tool_message(
                    session_id,
                    step["tool"],
                    step.get("args"),
                    message,
                    status="error",
                    summary="Error",
                    risk=step.get("risk"),
                    format=tool_format,
                    compact=answers_with_raw,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to persist error tool message for %s: %s",
                    step["tool"],
                    exc,
                )
            await send(
                _tool_error_payload(
                    step["tool"],
                    message,
                    step.get("risk"),
                    format=tool_format,
                    compact=answers_with_raw,
                )
            )
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
            await send(error_payload(_safe_error_message(exc)))
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
    # Report turns and explicit raw-output requests are answered with the raw
    # tool output; everything else is answered in the assistant's own words.
    uses_llm_summary = _uses_llm_summary(
        final_plan, observations, raw_requested=raw_requested
    )
    message_id = message_id or f"msg_{uuid4().hex}"

    async def on_delta(fragment: str, *, reset: bool = False) -> None:
        await send(delta_payload(message_id, fragment, reset=reset))

    summary = await _summarize(
        user_text,
        final_plan,
        observations,
        raw_requested=raw_requested,
        on_delta=on_delta,
    )
    log_stage("summary", time.monotonic() - summary_started, llm=uses_llm_summary)
    if summary:
        with contextlib.suppress(Exception):
            async with async_session_factory() as db:
                await add_assistant_message(db, session_id, summary)
        with contextlib.suppress(Exception):
            await send(response_payload(summary, message_id=message_id))

    # plan_done is terminal for the UI: send it only once the summary is out so
    # the client keeps the turn busy until the whole request has finished.
    if show_plan:
        with contextlib.suppress(Exception):
            await send(plan_done_payload(final_plan))

    if tracker is not None:
        tracker["plan"] = None
    log_stage("plan_total", time.monotonic() - plan_started, show_plan=show_plan)


def _read_only_commands() -> frozenset[str]:
    """Parse the ``LOOMCORE_REPORT_COMMANDS`` allow-list (comma/space separated)."""
    raw = settings.report_commands or ""
    return frozenset(raw.replace(",", " ").split())


# Shell metacharacters that make a command un-classifiable as read-only, so
# ``uptime; rm -rf /`` can never skip the summary.
_SHELL_METACHARACTERS = frozenset(";|&><$`()\n\r")


def _command_is_read_only(command: str) -> bool:
    """True when the command's basename is allow-listed as read-only."""
    if not command or any(ch in _SHELL_METACHARACTERS for ch in command):
        return False
    tokens = command.split()
    if not tokens:
        return False
    return os.path.basename(tokens[0]) in _read_only_commands()


def _step_answers_with_raw(step: dict) -> bool:
    """True when a step's raw output already is the answer.

    Only an allow-listed read-only ``ops_run_command`` qualifies; report tools
    (``report: true``) do not, so every plan is described in the assistant's own
    words.
    """
    arg = _report_arg_for(step.get("tool", ""))
    if arg:
        value = step.get("args", {}).get(arg)
        if isinstance(value, str):
            return _command_is_read_only(value)
    return False


def _plan_steps_answer_with_raw(plan: dict) -> bool:
    """True when every step of a non-empty plan answers with raw output."""
    steps = plan.get("steps", [])
    return bool(steps) and all(_step_answers_with_raw(step) for step in steps)


def _plan_answers_with_raw(plan: dict, *, raw_requested: bool) -> bool:
    """True when the raw tool output is the answer (no LLM summary needed)."""
    return raw_requested or _plan_steps_answer_with_raw(plan)


def _uses_llm_summary(
    plan: dict, observations: list[dict], *, raw_requested: bool
) -> bool:
    """True when the turn needs an LLM summary instead of raw tool output."""
    return bool(observations) and not _plan_answers_with_raw(
        plan, raw_requested=raw_requested
    )


async def _summarize(
    user_text: str,
    plan: dict,
    observations: list[dict],
    *,
    raw_requested: bool = False,
    on_delta: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    """Summarize a finished plan in the assistant's own words.

    Raw tool text is surfaced only when the user explicitly asked for it or when
    an allow-listed read-only command already is the answer. When the summary
    call fails, times out, returns nothing, or contradicts the numbers in the
    observations, a short deterministic digest is posted instead of the raw
    output. ``on_delta`` (if set) streams the summary text; it is only forwarded
    on the LLM path.
    """
    if not _uses_llm_summary(plan, observations, raw_requested=raw_requested):
        if not observations:
            return _fallback_summary(plan)
        return _raw_summary(plan, observations)
    try:
        summary = await airouter_with_plugins.summarize_plan(
            user_text, plan, observations, on_delta=on_delta
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error(f"Plan summary failed: {exc}")
        return _digest_summary(plan, observations)
    if not (summary or "").strip():
        return _digest_summary(plan, observations)
    if _counts_mismatch(summary, observations):
        logger.warning(
            "Plan summary changed the reported counts; surfacing a short digest"
        )
        return _digest_summary(plan, observations)
    return summary


# A health-style triple: "0 critical, 1 warning, 0 info".
_COUNTS_RE = re.compile(r"(\d+)\s+critical,\s*(\d+)\s+warnings?,\s*(\d+)\s+info")

# Explicit requests for the unprocessed tool output bypass the summary call.
_RAW_OUTPUT_RE = re.compile(
    r"raw\s+(?:output|text|result|response|table|data)|verbatim|as[- ]?is|"
    r"every\s+row|all\s+rows|list\s+every|full\s+(?:list|table|output|result)|"
    r"whole\s+(?:list|table|output|result)|exact\s+(?:output|result|text)|"
    r"как есть|сырой вывод|полный вывод|весь список|все строки|каждую строку",
    re.IGNORECASE,
)


def _wants_raw_output(user_text: str) -> bool:
    """True when the user explicitly asked for the unprocessed tool output."""
    return bool(user_text and _RAW_OUTPUT_RE.search(user_text))


def _observation_text(observations: list[dict]) -> str:
    texts = [
        _extract_text(item.get("result"))
        for item in observations
        if item.get("result") is not None
    ]
    return "\n\n".join(text for text in texts if text)


def _counts_mismatch(summary: str, observations: list[dict]) -> bool:
    """True when the LLM summary contradicts the counts in the tool results."""
    observed = _COUNTS_RE.search(_observation_text(observations))
    claimed = _COUNTS_RE.search(summary or "")
    return bool(observed and claimed and observed.groups() != claimed.groups())


def _fallback_summary(plan: dict) -> str:
    goal = plan.get("goal") or "Plan"
    status = plan.get("status", "done")
    lines = [f"{goal} ({status})."]
    lines.extend(
        f"- {step.get('tool')}: {step.get('status')}" for step in plan.get("steps", [])
    )
    return "\n".join(lines)


def _raw_summary(plan: dict, observations: list[dict]) -> str:
    """Deterministic, unprocessed tool output for a finished plan.

    Used only when the user explicitly asks for the raw output or an allow-listed
    read-only command already is the answer; every other fallback uses
    :func:`_digest_summary`.
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


# A digest keeps a short, single-line excerpt of each tool result.
_DIGEST_EXCERPT_CHARS = 240


def _digest_excerpt(text: str) -> str:
    """Collapse whitespace and cap a tool result for the digest fallback."""
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= _DIGEST_EXCERPT_CHARS:
        return collapsed
    return collapsed[: _DIGEST_EXCERPT_CHARS - 1].rstrip() + "…"


def _digest_summary(plan: dict, observations: list[dict]) -> str:
    """Short deterministic digest used when the LLM summary is unavailable.

    One line per step with a capped excerpt, so a stalled or failing summary
    never degrades the answer to the full raw tool dump.
    """
    goal = (plan.get("goal") or "Plan").strip()
    lines = [f"{goal}."]
    for item in observations:
        step = item.get("step", {})
        tool = step.get("tool", "tool")
        status = step.get("status", "done")
        excerpt = _digest_excerpt(_extract_text(item.get("result")) or "")
        if excerpt:
            lines.append(f"- {tool}: {status} — {excerpt}")
        else:
            lines.append(f"- {tool}: {status}")
    return "\n".join(lines)


async def _guarded(coro) -> None:
    """Run a background request, logging any unexpected failure."""
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error(f"Background request failed: {exc}")


def _hydrate_pending(pending: dict | None, stored: dict | None) -> None:
    """Fill the connection cache from the persisted pending plan (or clear it)."""
    if pending is None:
        return
    if not stored:
        pending["plan"] = None
        pending["user_text"] = ""
        pending["created_at"] = 0.0
        pending["reasked"] = False
        return
    pending["plan"] = stored.get("plan")
    pending["user_text"] = stored.get("user_text", "")
    pending["created_at"] = time.monotonic()
    pending["reasked"] = False


def _pending_age_seconds(created_at: str | None) -> float:
    """Seconds since the persisted pending plan was recorded (0 if unknown)."""
    if not created_at:
        return 0.0
    try:
        recorded = datetime.fromisoformat(created_at)
    except (TypeError, ValueError):
        return 0.0
    if recorded.tzinfo is None:
        recorded = recorded.replace(tzinfo=UTC)
    return (datetime.now(UTC) - recorded).total_seconds()


async def _remember_pending(
    session_id: str,
    pending: dict | None,
    plan: dict,
    user_text: str,
    *,
    prompt: str | None = None,
) -> None:
    """Record a plan awaiting the user's confirmation.

    Updates the connection cache and persists the record on the session, so a
    reload or reconnect still knows which plan is pending. ``prompt`` (the
    plain-language confirmation question) is persisted as an assistant message.
    """
    if pending is not None:
        pending["plan"] = plan
        pending["user_text"] = user_text
        pending["created_at"] = time.monotonic()
        pending["reasked"] = False
    with contextlib.suppress(Exception):
        async with async_session_factory() as db:
            if prompt is not None:
                await add_assistant_message(db, session_id, prompt)
            await set_stored_pending(db, session_id, plan, user_text)


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
        await send(thinking_payload())
        logger.info(f"🧠 Sending request to the LLM for session={session_id}...")

        message_id = f"msg_{uuid4().hex}"

        async def on_delta(fragment: str, *, reset: bool = False) -> None:
            await send(delta_payload(message_id, fragment, reset=reset))

        async def on_progress(fragment: str, *, reset: bool = False) -> None:
            await send(plan_progress_payload(message_id, fragment, reset=reset))

        route_started = time.monotonic()
        try:
            result = await airouter_with_plugins.route(
                user_text,
                session_id,
                on_delta=on_delta,
                on_progress=on_progress,
            )
            logger.info(
                f"✅ LLM responded, type={result.get('type')}, session={session_id}"
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "LLM route failed for session %s: %s", session_id, exc
            )
            await send(error_payload(_safe_error_message(exc)))
            return
        finally:
            log_stage("route_call", time.monotonic() - route_started)

        unavailable = result.get("tool_schema_warning")
        if unavailable:
            await send(schema_warning_payload(unavailable))

        answer_type = result.get("type")

        if answer_type == "text":
            await send(
                response_payload(result.get("content", ""), message_id=message_id)
            )
            return

        if answer_type != "plan":
            return

        plan = result.get("plan") or {}
        requires_approval = plan_requires_approval(plan)

        if plan_is_direct(plan):
            if not requires_approval:
                # One obvious, safe action: just do it, no plan card.
                await _run_plan(
                    send, session_id, user_text, plan, tracker, message_id=message_id
                )
                return
            # Direct but mutating: ask for a plain-language confirmation.
            goal = plan.get("goal") or "Proceed"
            prompt = f"{goal} Reply yes to proceed."
            await send(response_payload(prompt))
            await _remember_pending(session_id, pending, plan, user_text, prompt=prompt)
            return

        if not requires_approval:
            # An all-safe plan runs immediately, so it is approved by definition;
            # reflect that in the proposed event so the card is not shown as
            # "Awaiting approval".
            plan["status"] = "approved"

        await send(plan_proposed_payload(plan, requires_approval))

        if requires_approval:
            await _remember_pending(session_id, pending, plan, user_text)
            return

        await _run_plan(
            send, session_id, user_text, plan, tracker, message_id=message_id
        )
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
    # Restore a plan that was awaiting approval before this connection (a page
    # reload or reconnect must not lose it).
    with contextlib.suppress(Exception):
        async with async_session_factory() as db:
            _hydrate_pending(pending, await get_stored_pending(db, session_id))
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

    async def forget_pending_plan() -> None:
        """Clear the in-memory cache and the persisted pending plan."""
        clear_pending()
        with contextlib.suppress(Exception):
            async with async_session_factory() as db:
                await clear_stored_pending(db, session_id)

    # A decision that cannot be applied to the plan the user saw.
    replaced_message = (
        "The plan you were approving was replaced by a newer one. "
        "Review the latest plan and reply yes again."
    )
    inactive_message = "That plan is no longer active."
    expired_message = "That plan has expired. Please ask again."

    async def decide_pending(decision: str, cached_plan: dict) -> None:
        """Bind a textual yes/no to the persisted plan.

        The cached plan is trusted only while it is still the stored pending
        record; otherwise the decision is refused rather than applied to the
        wrong plan, and an expired record is discarded.
        """
        plan_id = cached_plan.get("plan_id")
        try:
            async with async_session_factory() as db:
                stored = await get_stored_pending(db, session_id)
        except Exception:
            logger.exception("Failed to read the pending plan for %s", session_id)
            stored = None
        stored_plan = (stored or {}).get("plan") or {}
        if not stored or stored_plan.get("plan_id") != plan_id:
            _hydrate_pending(pending, stored)
            await send(response_payload(replaced_message))
            return
        if stored_plan.get("status") != "proposed":
            await send(response_payload(inactive_message))
            return
        try:
            async with async_session_factory() as db:
                claimed = await claim_stored_pending(db, session_id, plan_id)
        except Exception:
            logger.exception("Failed to claim the pending plan for %s", session_id)
            claimed = None
        if claimed is None:
            await forget_pending_plan()
            await send(response_payload(inactive_message))
            return
        if (
            _pending_age_seconds(claimed.get("created_at"))
            > CONFIRMATION_TIMEOUT_SECONDS
        ):
            clear_pending()
            await send(response_payload(expired_message))
            return
        claimed_plan = claimed.get("plan") or stored_plan
        saved_text = claimed.get("user_text") or pending.get("user_text", "")
        clear_pending()
        if decision == "approve":
            await approve_plan(claimed_plan, saved_text)
        else:
            await reject_plan(claimed_plan)

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
            await send(response_payload("Cancelled."))
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
            await forget_pending_plan()
            await send(cancelled_payload())
            return

        # User rejected the pending plan
        if action == "plan_reject":
            plan = pending.get("plan")
            await forget_pending_plan()
            if plan:
                await reject_plan(plan)
            return

        # User approved the pending plan
        if action == "plan_approve":
            if is_busy():
                await send(busy_payload())
                return
            plan = pending.get("plan")
            if not plan:
                return
            user_text = pending.get("user_text", "")
            await forget_pending_plan()
            await approve_plan(plan, user_text)
            return

        # User edited the pending plan
        if action == "plan_edit":
            if is_busy():
                await send(busy_payload())
                return
            plan = pending.get("plan")
            steps = data.get("steps")
            if not plan or not isinstance(steps, list):
                return
            revised = next_revision(plan, steps, risk_of=resolve_tool_risk)
            schemas = await airouter_with_plugins.tool_schemas()
            errors = validate_plan(revised, set(schemas), schemas)
            if errors:
                await send(error_payload("Invalid plan: " + "; ".join(errors)))
                return
            user_text = pending.get("user_text", "")
            await _remember_pending(session_id, pending, revised, user_text)
            with contextlib.suppress(Exception):
                await _save_plan_message(session_id, revised)
            requires_approval = plan_requires_approval(revised)
            await send(plan_proposed_payload(revised, requires_approval))
            if not requires_approval:
                await forget_pending_plan()
                start(_run_plan(send, session_id, user_text, revised, tracker))
            return

        # Regular text message from the user
        if isinstance(data, str):
            user_text = data
        elif isinstance(data, dict) and isinstance(data.get("message"), str):
            user_text = data["message"]
        else:
            logger.warning(
                "Ignoring frame with no string message for session %s", session_id
            )
            return
        if not user_text:
            return
        if is_busy():
            # Keep the newest message; it runs when the current turn ends.
            queued_text = user_text
            return

        # A pending plan (direct action or plan card) awaiting a text yes/no.
        if pending.get("plan") is None:
            # The cache may be empty after a fresh connect, or another
            # connection may have proposed a plan; re-read the persisted record.
            with contextlib.suppress(Exception):
                async with async_session_factory() as db:
                    stored = await get_stored_pending(db, session_id)
                _hydrate_pending(pending, stored)
        pending_plan = pending.get("plan")
        if pending_plan is not None:
            decision = parse_confirmation(user_text)
            if decision in ("approve", "reject"):
                # A decision is always bound to the pending plan — it is never
                # silently routed to the planner instead.
                await decide_pending(decision, pending_plan)
                return
            if plan_is_direct(pending_plan) and not pending.get("reasked"):
                pending["reasked"] = True
                await send(response_payload("Reply yes to proceed, or no to cancel."))
                return
            # Non-committal: terminalize a plan card so it never sticks, then
            # route the message fresh.
            await forget_pending_plan()
            if not plan_is_direct(pending_plan):
                await reject_plan(pending_plan)

        start(_process_user_message(send, session_id, user_text, tracker, pending))

    try:
        while True:
            # Read JSON from the client
            try:
                data = await websocket.receive_json()
            except ValueError:
                if not _allow_ws_message(identity):
                    await send(error_payload("Rate limit exceeded. Please slow down."))
                    continue
                logger.warning(
                    "Ignoring malformed JSON frame for session %s", session_id
                )
                with contextlib.suppress(Exception):
                    await send(error_payload("Malformed JSON frame ignored."))
                continue
            logger.info(f"📥 Received client message: {_describe_client_message(data)}")
            if not _allow_ws_message(identity):
                await send(error_payload("Rate limit exceeded. Please slow down."))
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
                    await send(error_payload(_safe_error_message(exc)))

    except WebSocketDisconnect:
        logger.info(f"🔌 WebSocket disconnected for session {session_id}")
    except Exception as exc:
        with contextlib.suppress(Exception):
            await send(error_payload(_safe_error_message(exc)))
        logger.error(f"❌ Fatal WebSocket error for session {session_id}: {exc}")
        logger.error(traceback.format_exc())
    finally:
        await cancel_current()
