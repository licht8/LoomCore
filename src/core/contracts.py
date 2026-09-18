"""Runtime contracts for the tool-call boundary.

Plugins publish a JSON Schema per tool (``inputSchema``); this module turns that
schema into a runtime check. The LLM is an untrusted producer, so arguments are
validated before they go on the wire, and plugin results are sanity-checked when
they come back. Validation is deliberately best-effort: an unknown or malformed
schema never blocks a call, and a malformed result only warns (RULES 2).
"""

from __future__ import annotations

import logging
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

logger = logging.getLogger("loomcore.contracts")

# Keys that make a schema worth validating; an empty/opaque object is skipped.
_SCHEMA_KEYS = ("type", "properties", "required", "oneOf", "anyOf", "allOf")


class ToolContractError(ValueError):
    """Raised when tool arguments do not satisfy the tool's input schema."""


def _validator(schema: Any) -> Draft202012Validator | None:
    """Build a validator for a tool schema, or None when it cannot be used."""
    if not isinstance(schema, dict) or not schema:
        return None
    if not any(key in schema for key in _SCHEMA_KEYS):
        return None
    try:
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema)
    except SchemaError as exc:
        logger.warning(f"Ignoring invalid tool schema: {exc.message}")
        return None


def validate_tool_args(tool_name: str, args: Any, schema: Any) -> None:
    """Raise :class:`ToolContractError` when ``args`` violate ``schema``.

    A missing or unusable schema is a no-op, so a plugin that publishes no
    ``inputSchema`` keeps working exactly as before.
    """
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ToolContractError(f"{tool_name} arguments must be an object")

    validator = _validator(schema)
    if validator is None:
        return

    errors = sorted(
        validator.iter_errors(args),
        key=lambda error: [str(part) for part in error.path],
    )
    if not errors:
        return

    error = errors[0]
    if error.validator == "required":
        required = schema.get("required") if isinstance(schema, dict) else None
        missing = [name for name in (required or []) if name not in args]
        raise ToolContractError(
            f"{tool_name} is missing required argument(s): {', '.join(missing)}"
        )

    location = ".".join(str(part) for part in error.path)
    detail = f"{location}: {error.message}" if location else error.message
    raise ToolContractError(f"{tool_name}: {detail}")


def validate_tool_result(result: Any) -> list[str]:
    """Return lenient warnings about a plugin's MCP-shaped result.

    Never raises: a plugin result is only logged, because a plugin crash must
    not take down the core (RULES 2).
    """
    warnings: list[str] = []
    if not isinstance(result, dict):
        return ["tool result is not an object"]
    content = result.get("content")
    if content is None:
        return warnings
    if not isinstance(content, list):
        return ["tool result 'content' is not a list"]
    for index, item in enumerate(content):
        if not isinstance(item, dict) or "type" not in item:
            warnings.append(f"tool result content[{index}] is not a typed item")
    return warnings
