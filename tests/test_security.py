"""Tool risk classification tests."""

from src.core.security import classify_tool_risk


def test_install_dependencies_requires_approval():
    assert classify_tool_risk("wg_install_dependencies") == "dangerous"


def test_read_only_tools_are_safe():
    assert classify_tool_risk("wg_interface_status") == "safe"
    assert classify_tool_risk("wg_diagnostics") == "safe"
