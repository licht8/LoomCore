"""Unit tests for the versioned WebSocket event registry."""

from src.interfaces import events as events_module
from src.interfaces.events import (
    EVENT_MODELS,
    WS_SCHEMA_VERSION,
    ResponseEvent,
    event_payload,
)
from src.interfaces.tool_events import (
    plan_done_payload,
    plan_proposed_payload,
    plan_step_payload,
    tool_call_payload,
    tool_error_payload,
    tool_success_payload,
)

_EXPECTED_TYPES = {
    "thinking",
    "response",
    "delta",
    "plan_progress",
    "tool_call",
    "tool_response",
    "plan_proposed",
    "plan_step",
    "plan_done",
    "schema_warning",
    "cancelled",
    "busy",
    "error",
}


# === registry shape ===


def test_registry_has_every_ws_message_type():
    assert set(EVENT_MODELS) == _EXPECTED_TYPES


def test_each_model_declares_its_own_type():
    for type_name, model in EVENT_MODELS.items():
        payload = event_payload(model())
        assert payload["type"] == type_name


def test_every_payload_carries_the_schema_version():
    for model in EVENT_MODELS.values():
        assert event_payload(model())["schema_version"] == WS_SCHEMA_VERSION == 1


def test_payload_drops_unset_fields():
    assert events_module.response_payload("hello") == {
        "type": "response",
        "content": "hello",
        "schema_version": 1,
    }


def test_explicit_none_is_omitted_from_payload():
    payload = event_payload(ResponseEvent(content="x", text=None))
    assert payload == {"type": "response", "content": "x", "schema_version": 1}
    assert "text" not in payload


# === module builders ===


def test_thinking_payload_default_status():
    assert events_module.thinking_payload() == {
        "type": "thinking",
        "status": "processing",
        "schema_version": 1,
    }


def test_response_payload_keeps_optional_text():
    payload = events_module.response_payload("hi", text="raw")
    assert payload["content"] == "hi"
    assert payload["text"] == "raw"


def test_response_payload_includes_message_id():
    payload = events_module.response_payload("hi", message_id="msg_1")
    assert payload["content"] == "hi"
    assert payload["message_id"] == "msg_1"


def test_delta_payload_shape():
    payload = events_module.delta_payload("msg_1", "frag")
    assert payload == {
        "type": "delta",
        "message_id": "msg_1",
        "content": "frag",
        "reset": False,
        "schema_version": 1,
    }


def test_delta_payload_reset_flag():
    payload = events_module.delta_payload("msg_1", "", reset=True)
    assert payload["reset"] is True
    assert payload["content"] == ""


def test_plan_progress_payload_shape():
    payload = events_module.plan_progress_payload("msg_1", "check disk")
    assert payload == {
        "type": "plan_progress",
        "message_id": "msg_1",
        "content": "check disk",
        "reset": False,
        "schema_version": 1,
    }


def test_plan_progress_payload_reset_flag():
    payload = events_module.plan_progress_payload("msg_1", "", reset=True)
    assert payload["reset"] is True
    assert payload["content"] == ""


def test_schema_warning_payload_lists_plugins():
    payload = events_module.schema_warning_payload(["LoomWG"])
    assert payload["unavailable_plugins"] == ["LoomWG"]
    assert payload["type"] == "schema_warning"


def test_cancelled_and_busy_payloads():
    assert events_module.cancelled_payload() == {
        "type": "cancelled",
        "schema_version": 1,
    }
    assert events_module.busy_payload() == {"type": "busy", "schema_version": 1}


def test_error_payload_carries_message():
    assert events_module.error_payload("boom") == {
        "type": "error",
        "error": "boom",
        "schema_version": 1,
    }


# === tool_events builders flow through the registry ===


def _sample_plan() -> dict:
    return {
        "plan_id": "pl_1",
        "revision": 2,
        "goal": "do a thing",
        "status": "proposed",
        "steps": [{"id": "s1", "tool": "core_health", "args": {}, "risk": "safe"}],
    }


def test_tool_success_payload_is_registered():
    payload = tool_success_payload("core_health", "ok", risk="safe")
    assert payload["type"] == "tool_response"
    assert payload["status"] == "success"
    assert payload["schema_version"] == 1
    assert "artifacts" not in payload
    assert "result" not in payload


def test_tool_error_payload_is_registered():
    payload = tool_error_payload("core_health", "boom")
    assert payload["type"] == "tool_response"
    assert payload["status"] == "error"
    assert payload["content"] == "boom"


def test_tool_response_payload_format_and_compact():
    payload = tool_success_payload(
        "core_list_tools", "ok", format="markdown", compact=True
    )
    assert payload["type"] == "tool_response"
    assert payload["format"] == "markdown"
    assert payload["compact"] is True

    default = tool_success_payload("ops_run_command", "ok")
    assert default["format"] == "text"
    assert default["compact"] is False


def test_tool_call_payload_is_registered():
    payload = tool_call_payload("core_health", {"x": 1})
    assert payload["type"] == "tool_call"
    assert payload["status"] == "running"
    assert payload["args"] == {"x": 1}


def test_plan_payloads_are_registered():
    plan = _sample_plan()
    proposed = plan_proposed_payload(plan, requires_approval=True)
    assert proposed["type"] == "plan_proposed"
    assert proposed["plan_id"] == "pl_1"
    assert proposed["revision"] == 2

    step = plan_step_payload(plan, plan["steps"][0], "started")
    assert step["type"] == "plan_step"
    assert step["phase"] == "started"

    done = plan_done_payload(plan)
    assert done["type"] == "plan_done"
    assert done["status"] == "proposed"
