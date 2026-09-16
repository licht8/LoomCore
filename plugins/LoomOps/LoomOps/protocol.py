"""Minimal JSON-RPC transport used by the LoomOps plugin."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import __version__

logger = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], dict[str, Any]]

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "manifest.json"


def _server_identity() -> tuple[str, str]:
    """Return (name, version) from the manifest, falling back to defaults."""
    try:
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "LoomOps", __version__
    if not isinstance(data, dict):
        return "LoomOps", __version__
    return str(data.get("name") or "LoomOps"), str(data.get("version") or __version__)


SERVER_NAME, SERVER_VERSION = _server_identity()


def response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def dispatch(
    request: dict[str, Any],
    handlers: dict[str, Handler],
    tools_schema: list[dict[str, Any]],
) -> dict[str, Any]:
    request_id = request.get("id", 0)
    method = request.get("method", "")
    params = request.get("params")
    if params is None:
        params = {}
    elif not isinstance(params, dict):
        return error(request_id, -32602, "Invalid params")

    if method == "initialize":
        return response(
            request_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )
    if method == "tools/list":
        return response(request_id, {"tools": tools_schema})
    if method == "tools/call":
        tool_name = params.get("name", "")
        handler = handlers.get(tool_name)
        if handler is None:
            return error(request_id, -32601, f"Unknown tool: {tool_name}")
        try:
            return response(request_id, handler(params.get("arguments") or {}))
        except (OSError, RuntimeError, ValueError) as exc:
            return error(request_id, -32000, str(exc))
    return error(request_id, -32601, f"Unknown method: {method}")


def error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def run(
    handlers: dict[str, Handler],
    tools_schema: list[dict[str, Any]],
) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        if not line.strip():
            continue
        sys.stdout.write(_serialize(_handle_line(line, handlers, tools_schema)) + "\n")
        sys.stdout.flush()


def _internal_error(request_id: Any, message: str) -> dict[str, Any]:
    return error(request_id, -32603, message)


def _serialize(payload: dict[str, Any]) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError, RecursionError):
        logger.exception("Failed to serialize response")
        try:
            return json.dumps(
                _internal_error(payload.get("id"), "Internal error"),
                ensure_ascii=False,
            )
        except (TypeError, ValueError, RecursionError):
            logger.exception("Failed to serialize error response")
            return json.dumps(
                error(None, -32603, "Internal error"), ensure_ascii=False
            )


def _handle_line(
    line: str,
    handlers: dict[str, Handler],
    tools_schema: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        request = json.loads(line)
    except (json.JSONDecodeError, RecursionError) as exc:
        return error(0, -32700, str(exc))
    if not isinstance(request, dict):
        return error(0, -32600, "Invalid Request")
    request_id = request.get("id", 0)
    try:
        return dispatch(request, handlers, tools_schema)
    except Exception:
        logger.exception("Unhandled error while dispatching request %r", request_id)
        return _internal_error(request_id, "Internal error")
