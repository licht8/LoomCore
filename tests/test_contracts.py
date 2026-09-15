"""Unit tests for the tool-call runtime contracts."""

import pytest

from src.core.contracts import (
    ToolContractError,
    validate_tool_args,
    validate_tool_result,
)

_PLUGIN_SCHEMA = {
    "type": "object",
    "properties": {"plugin": {"type": "string"}, "force": {"type": "boolean"}},
    "required": ["plugin"],
    "additionalProperties": False,
}


# === validate_tool_args ===


def test_validate_tool_args_no_schema_is_noop():
    validate_tool_args("demo_tool", {"anything": 1}, None)


def test_validate_tool_args_empty_schema_is_noop():
    validate_tool_args("demo_tool", {"anything": 1}, {})


def test_validate_tool_args_opaque_schema_is_noop():
    validate_tool_args("demo_tool", {"anything": 1}, {"description": "opaque"})


def test_validate_tool_args_accepts_valid():
    validate_tool_args("core_enable_plugin", {"plugin": "LoomWG"}, _PLUGIN_SCHEMA)


def test_validate_tool_args_none_becomes_empty_object():
    with pytest.raises(ToolContractError, match="missing required argument"):
        validate_tool_args("core_enable_plugin", None, _PLUGIN_SCHEMA)


def test_validate_tool_args_non_object_raises():
    with pytest.raises(ToolContractError, match="must be an object"):
        validate_tool_args("core_enable_plugin", ["plugin"], _PLUGIN_SCHEMA)


def test_validate_tool_args_missing_required_lists_names():
    with pytest.raises(ToolContractError) as excinfo:
        validate_tool_args("core_enable_plugin", {}, _PLUGIN_SCHEMA)
    assert "missing required argument(s): plugin" in str(excinfo.value)


def test_validate_tool_args_type_error_names_path():
    with pytest.raises(ToolContractError) as excinfo:
        validate_tool_args("demo_tool", {"plugin": "x", "force": "yes"}, _PLUGIN_SCHEMA)
    assert "demo_tool: force:" in str(excinfo.value)


def test_validate_tool_args_rejects_additional_property():
    with pytest.raises(ToolContractError):
        validate_tool_args("demo_tool", {"plugin": "x", "extra": 1}, _PLUGIN_SCHEMA)


def test_validate_tool_args_invalid_schema_is_noop(caplog):
    with caplog.at_level("WARNING"):
        validate_tool_args("demo_tool", {}, {"type": "not-a-real-type"})
    assert any("invalid tool schema" in record.message for record in caplog.records)


# === validate_tool_result ===


def test_validate_tool_result_non_object():
    assert validate_tool_result("nope") == ["tool result is not an object"]


def test_validate_tool_result_missing_content_is_quiet():
    assert validate_tool_result({}) == []


def test_validate_tool_result_content_not_a_list():
    assert validate_tool_result({"content": "text"}) == [
        "tool result 'content' is not a list"
    ]


def test_validate_tool_result_typed_content_is_valid():
    result = {"content": [{"type": "text", "text": "ok"}]}
    assert validate_tool_result(result) == []


def test_validate_tool_result_warns_on_untyped_item():
    warnings = validate_tool_result({"content": [{"text": "no type"}]})
    assert warnings == ["tool result content[0] is not a typed item"]
