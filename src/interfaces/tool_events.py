"""Payload builders and history helpers for tool-related WebSocket events.

These functions shape the events the chat UI receives for a tool call and
persist the same information into the session history. They hold no transport
or orchestration logic -- ``src.interfaces.websocket`` owns those.
"""

from urllib.parse import quote

from src.db.database import async_session_factory
from src.db.session import add_message, add_plan_message
from src.interfaces.events import (
    PlanDoneEvent,
    PlanProposedEvent,
    PlanStepEvent,
    ToolCallEvent,
    ToolResponseEvent,
    event_payload,
)


def extract_text(raw_result: object) -> str:
    """Extract text from an MCP result, tolerating malformed payloads.

    A well-formed MCP result is ``{"content": [{"type": "text", "text": ...}]}``.
    Anything else is degraded gracefully: a non-list ``content`` is stringified,
    non-dict elements and missing/``None`` text values are ignored (non-string
    text is coerced with ``str``), and ``None`` yields an empty string.
    """
    if isinstance(raw_result, dict) and "content" in raw_result:
        content = raw_result["content"]
        if isinstance(content, list):
            return "".join(
                str(item.get("text"))
                for item in content
                if isinstance(item, dict)
                and item.get("type") == "text"
                and item.get("text") is not None
            )
        return str(content)
    return "" if raw_result is None else str(raw_result)


def build_artifacts(raw_result: dict) -> list[dict] | None:
    """Build durable download links for any tool result exposing client artifacts.

    Links are keyed by client name (``/api/artifacts/<name>/<kind>``), so they
    survive plugin restarts and browser reloads.
    """
    if not isinstance(raw_result, dict):
        return None
    data = raw_result.get("data")
    if not isinstance(data, dict):
        return None

    entries: list[dict] = []
    clients = data.get("clients")
    if isinstance(clients, list):
        entries.extend(entry for entry in clients if isinstance(entry, dict))
    elif data.get("name"):
        entries.append(data)

    artifacts: list[dict] = []
    for entry in entries:
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        quoted = quote(name, safe="")
        config_ready = bool(entry.get("config_ready") or entry.get("client_config"))
        qr_ready = bool(entry.get("qr_ready") or entry.get("qr_code"))
        if config_ready:
            artifacts.append(
                {
                    "label": f"Configuration: {name}",
                    "url": f"/api/artifacts/{quoted}/config",
                }
            )
        if qr_ready:
            artifacts.append(
                {
                    "label": f"QR code: {name}",
                    "url": f"/api/artifacts/{quoted}/qr",
                }
            )
    return artifacts or None


async def save_tool_message(
    session_id: str,
    tool_name: str,
    arguments: dict | None,
    content: str,
    *,
    status: str = "success",
    summary: str = "Done",
    risk: str | None = None,
    artifacts: list[dict] | None = None,
) -> None:
    """Save a tool call and its terminal state to the session history.

    The UI restores tool cards from ``tool_calls`` after a reload, so status,
    summary, risk and artifacts all live inside that JSON (no schema change).
    """
    entry: dict = {
        "name": tool_name,
        "arguments": arguments,
        "status": status,
        "summary": summary,
    }
    if risk:
        entry["risk"] = risk
    if artifacts:
        entry["artifacts"] = artifacts
    async with async_session_factory() as db:
        await add_message(db, session_id, "tool", content, tool_calls=[entry])


async def save_plan_message(session_id: str, plan: dict) -> None:
    """Persist the current state of a plan into the session history.

    Plans are append-only, like tool cards: each proposal, edited revision and
    terminal state is stored as a ``role="plan"`` message whose ``tool_calls``
    holds the plan object. The UI collapses revisions by ``plan_id``.
    """
    async with async_session_factory() as db:
        await add_plan_message(db, session_id, plan)


def tool_success_payload(
    tool_name: str,
    content: str,
    risk: str | None = None,
    artifacts: list[dict] | None = None,
) -> dict:
    """Terminal tool_response marking a tool call as successful."""
    return event_payload(
        ToolResponseEvent(
            tool=tool_name,
            content=content,
            status="success",
            summary="Done",
            artifacts=artifacts,
            risk=risk,
        )
    )


def tool_error_payload(
    tool_name: str, message: str, risk: str | None = None
) -> dict:
    """Terminal tool_response marking a tool call as failed."""
    return event_payload(
        ToolResponseEvent(
            tool=tool_name,
            content=message,
            status="error",
            summary="Error",
            risk=risk,
        )
    )


def tool_call_payload(tool_name: str, arguments: dict | None) -> dict:
    """The tool_call event shown while a tool is running."""
    return event_payload(
        ToolCallEvent(
            tool=tool_name,
            args=arguments,
            content=f"Calling tool: {tool_name}",
            status="running",
            summary="Running…",
        )
    )


def plan_proposed_payload(plan: dict, requires_approval: bool) -> dict:
    """The plan_proposed event shown when the agent proposes a plan.

    ``requires_approval`` tells the UI whether to offer Approve/Edit/Reject; an
    all-safe plan is shown and executed without waiting.
    """
    return event_payload(
        PlanProposedEvent(
            plan=plan,
            plan_id=plan.get("plan_id"),
            revision=plan.get("revision", 1),
            goal=plan.get("goal", ""),
            requires_approval=requires_approval,
            content=plan.get("goal", ""),
            summary="Plan ready",
        )
    )


def plan_step_payload(plan: dict, step: dict, phase: str) -> dict:
    """A single step update (phase: started/done/failed)."""
    return event_payload(
        PlanStepEvent(
            plan_id=plan.get("plan_id"),
            revision=plan.get("revision", 1),
            status=plan.get("status"),
            step=step,
            phase=phase,
        )
    )


def plan_done_payload(plan: dict) -> dict:
    """The terminal plan_done event (status: done/failed/rejected/cancelled)."""
    return event_payload(
        PlanDoneEvent(
            plan=plan,
            plan_id=plan.get("plan_id"),
            revision=plan.get("revision", 1),
            status=plan.get("status"),
        )
    )
