"""Payload builders and history helpers for tool-related WebSocket events.

These functions shape the events the chat UI receives for a tool call and
persist the same information into the session history. They hold no transport
or orchestration logic -- ``src.interfaces.websocket`` owns those.
"""

from urllib.parse import quote

from src.db.database import async_session_factory
from src.db.session import add_message


def extract_text(raw_result: dict) -> str:
    """Extract text from an MCP result ({"content": [{"type": "text", ...}]})."""
    if isinstance(raw_result, dict) and "content" in raw_result:
        return "".join(
            item.get("text", "")
            for item in raw_result["content"]
            if item.get("type") == "text"
        )
    return str(raw_result)


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


def tool_success_payload(
    tool_name: str,
    content: str,
    risk: str | None = None,
    artifacts: list[dict] | None = None,
) -> dict:
    """Terminal tool_response marking a tool call as successful."""
    payload = {
        "type": "tool_response",
        "tool": tool_name,
        "content": content,
        "result": content,
        "status": "success",
        "summary": "Done",
        "artifacts": artifacts,
    }
    if risk:
        payload["risk"] = risk
    return payload


def tool_error_payload(
    tool_name: str, message: str, risk: str | None = None
) -> dict:
    """Terminal tool_response marking a tool call as failed."""
    payload = {
        "type": "tool_response",
        "tool": tool_name,
        "content": message,
        "result": message,
        "status": "error",
        "summary": "Error",
    }
    if risk:
        payload["risk"] = risk
    return payload


def tool_call_payload(tool_name: str, arguments: dict | None) -> dict:
    """The tool_call event shown while a tool is running."""
    return {
        "type": "tool_call",
        "tool": tool_name,
        "args": arguments,
        "content": f"Calling tool: {tool_name}",
        "status": "running",
        "summary": "Running…",
    }


def approval_request_payload(tool_name: str, arguments: dict | None) -> dict:
    """The approval_request event shown for a dangerous tool."""
    return {
        "type": "approval_request",
        "tool": tool_name,
        "args": arguments,
        "risk": "dangerous",
        "action": tool_name,
        "content": f"Confirm running tool: {tool_name}",
    }
