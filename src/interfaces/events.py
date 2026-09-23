"""Versioned registry of WebSocket event payloads.

Every payload the chat UI receives is a JSON object discriminated on ``type``.
This module is the single definition of those shapes: the builders here and in
``src.interfaces.tool_events`` construct them, and ``src.interfaces.websocket``
only sends payloads the registry knows about. The union mirrors
``frontend/lib/chat-types.ts`` (``WsMessage``).

The contract is additive-only: a new optional field may be added, but a
``type`` is never removed or repurposed without bumping ``WS_SCHEMA_VERSION``.
Extra fields the frontend does not declare (``tool``, ``risk``,
``schema_version``) are ignored by TypeScript and safe to send.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

WS_SCHEMA_VERSION = 1


class _Event(BaseModel):
    """Base event: every payload carries the schema version envelope."""

    schema_version: int = WS_SCHEMA_VERSION


class ThinkingEvent(_Event):
    type: Literal["thinking"] = "thinking"
    status: str | None = None


class ResponseEvent(_Event):
    type: Literal["response"] = "response"
    content: str | None = None
    text: str | None = None
    message_id: str | None = None


class DeltaEvent(_Event):
    type: Literal["delta"] = "delta"
    message_id: str | None = None
    content: str | None = None
    reset: bool = False


class ToolCallEvent(_Event):
    type: Literal["tool_call"] = "tool_call"
    content: str | None = None
    text: str | None = None
    tool: str | None = None
    args: dict[str, Any] | None = None
    summary: str | None = None
    code: str | None = None
    artifacts: list[dict[str, Any]] | None = None
    status: str | None = None


class ToolResponseEvent(_Event):
    type: Literal["tool_response"] = "tool_response"
    status: str | None = None
    content: str | None = None
    text: str | None = None
    error: str | None = None
    summary: str | None = None
    code: str | None = None
    artifacts: list[dict[str, Any]] | None = None
    tool: str | None = None
    risk: str | None = None
    format: Literal["markdown", "text"] = "text"
    compact: bool = False


class PlanProposedEvent(_Event):
    type: Literal["plan_proposed"] = "plan_proposed"
    plan: dict[str, Any] | None = None
    plan_id: str | None = None
    revision: int | None = None
    goal: str | None = None
    requires_approval: bool | None = None
    content: str | None = None
    summary: str | None = None


class PlanStepEvent(_Event):
    type: Literal["plan_step"] = "plan_step"
    plan_id: str | None = None
    revision: int | None = None
    status: str | None = None
    step: dict[str, Any] | None = None
    phase: str | None = None


class PlanDoneEvent(_Event):
    type: Literal["plan_done"] = "plan_done"
    plan: dict[str, Any] | None = None
    plan_id: str | None = None
    revision: int | None = None
    status: str | None = None


class SchemaWarningEvent(_Event):
    type: Literal["schema_warning"] = "schema_warning"
    unavailable_plugins: list[str] | None = None


class CancelledEvent(_Event):
    type: Literal["cancelled"] = "cancelled"


class BusyEvent(_Event):
    type: Literal["busy"] = "busy"


class ErrorEvent(_Event):
    type: Literal["error"] = "error"
    error: str | None = None
    text: str | None = None
    content: str | None = None


# The exhaustive set of event types the backend may emit.
EVENT_MODELS: dict[str, type[_Event]] = {
    "thinking": ThinkingEvent,
    "response": ResponseEvent,
    "delta": DeltaEvent,
    "tool_call": ToolCallEvent,
    "tool_response": ToolResponseEvent,
    "plan_proposed": PlanProposedEvent,
    "plan_step": PlanStepEvent,
    "plan_done": PlanDoneEvent,
    "schema_warning": SchemaWarningEvent,
    "cancelled": CancelledEvent,
    "busy": BusyEvent,
    "error": ErrorEvent,
}


def event_payload(model: _Event) -> dict:
    """Serialize an event model to a wire dict, dropping ``None`` fields.

    ``exclude_none`` is deliberate: **every** field whose value is ``None`` is
    omitted rather than sent as ``null``, whether the caller passed it or left the
    model default. No registry field treats an explicit ``null`` as meaningful,
    and the TS union marks these fields optional, so the payload stays additive.
    """
    return model.model_dump(exclude_none=True)


def thinking_payload(status: str = "processing") -> dict:
    return event_payload(ThinkingEvent(status=status))


def response_payload(
    content: str, text: str | None = None, message_id: str | None = None
) -> dict:
    return event_payload(ResponseEvent(content=content, text=text, message_id=message_id))


def delta_payload(message_id: str, content: str, reset: bool = False) -> dict:
    return event_payload(
        DeltaEvent(message_id=message_id, content=content, reset=reset)
    )


def schema_warning_payload(unavailable_plugins: list[str]) -> dict:
    return event_payload(
        SchemaWarningEvent(unavailable_plugins=list(unavailable_plugins))
    )


def cancelled_payload() -> dict:
    return event_payload(CancelledEvent())


def busy_payload() -> dict:
    return event_payload(BusyEvent())


def error_payload(message: str) -> dict:
    return event_payload(ErrorEvent(error=message))
