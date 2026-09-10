"""Minimal JSON-RPC transport used by LoomCore plugin processes."""

from __future__ import annotations

import json
import sys
from typing import Any, Callable


Handler = Callable[[dict[str, Any]], dict[str, Any]]


def response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def dispatch(
    request: dict[str, Any],
    handlers: dict[str, Handler],
    tools_schema: list[dict[str, Any]],
) -> dict[str, Any]:
    request_id = request.get("id", 0)
    method = request.get("method", "")
    params = request.get("params") or {}

    if method == "initialize":
        return response(
            request_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "loomwg", "version": "0.1.0"},
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
        try:
            payload = dispatch(json.loads(line), handlers, tools_schema)
        except json.JSONDecodeError as exc:
            payload = error(0, -32700, str(exc))
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()
