#!/usr/bin/env python3
"""Entry point for a LoomCore MCP plugin (template).

Copy this folder to `plugins/<PluginId>/`, then update:
  - `manifest.json` (id, name, version, description, tools_prefix)
  - `TOOLS_SCHEMA` and `HANDLERS` below

The hub launches this file and speaks JSON-RPC 2.0 over stdin/stdout, one
request per line. A request is answered with a single response line.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from manifest import PLUGIN_ID, check_permissions, load_manifest, validate_manifest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(PLUGIN_ID)

TOOLS_SCHEMA = [
    {
        "name": "example_echo",
        "description": "Echo the provided text back.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    }
]


def example_echo(arguments: dict[str, Any]) -> dict[str, Any]:
    text = str(arguments.get("text", ""))
    return _text(text, length=len(text))


HANDLERS = {"example_echo": example_echo}


def _text(content: str, **data: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": content}], "data": data}


def _response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _dispatch(request: dict[str, Any]) -> dict[str, Any]:
    method = request.get("method", "")
    request_id = request.get("id", 0)
    if method == "initialize":
        manifest = load_manifest()
        return _response(
            request_id,
            {
                "protocolVersion": manifest.get("protocol", "2024-11-05"),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": manifest.get("id", PLUGIN_ID),
                    "version": manifest.get("version", "0.0.0"),
                },
            },
        )
    if method == "tools/list":
        return _response(request_id, {"tools": TOOLS_SCHEMA})
    if method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name", "")
        handler = HANDLERS.get(name)
        if handler is None:
            return _error(request_id, -32601, f"Unknown tool: {name}")
        try:
            return _response(request_id, handler(params.get("arguments") or {}))
        except (OSError, RuntimeError, ValueError) as exc:
            return _error(request_id, -32000, str(exc))
    return _error(request_id, -32601, f"Unknown method: {method}")


def _self_check() -> None:
    manifest = load_manifest()
    for issue in validate_manifest(manifest):
        logger.warning("manifest issue: %s", issue)
    for warning in check_permissions(manifest):
        logger.warning("permission check: %s", warning)


def main() -> None:
    _self_check()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            payload = _dispatch(json.loads(line))
        except json.JSONDecodeError as exc:
            payload = _error(0, -32700, str(exc))
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
