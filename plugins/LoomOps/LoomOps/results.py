"""Shared MCP result helper for LoomOps tools."""

from __future__ import annotations

from typing import Any


def text_result(content: str, **data: Any) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": content}],
        "data": data,
    }
