"""Tool risk classification and admin access tests."""

import pytest
from fastapi import HTTPException

from src.auth import create_jwt_token
from src.config import settings
from src.core.security import classify_tool_risk, require_admin_token


def test_install_dependencies_requires_approval():
    assert classify_tool_risk("wg_install_dependencies") == "dangerous"


def test_read_only_tools_are_safe():
    assert classify_tool_risk("wg_interface_status") == "safe"
    assert classify_tool_risk("wg_diagnostics") == "safe"


@pytest.mark.parametrize(
    "tool_name",
    [
        "wg_remove_peer",
        "wg_export_existing_clients",
        "wg_setup_server",
        "wg_interface_start",
        "wg_interface_stop",
        "wg_interface_restart",
        "wg_service_enable",
        "wg_service_disable",
        "core_enable_plugin",
        "core_disable_plugin",
        "core_reload_plugin",
        "ops_run_command",
        "ops_write_file",
        "ops_kill_process",
        "ops_systemctl_restart",
    ],
)
def test_explicit_dangerous_tools(tool_name):
    assert classify_tool_risk(tool_name) == "dangerous"


def test_ops_read_tools_are_safe():
    assert classify_tool_risk("ops_read_file") == "safe"
    assert classify_tool_risk("ops_list_processes") == "safe"
    assert classify_tool_risk("ops_systemctl_status") == "safe"
    assert classify_tool_risk("ops_disk_usage") == "safe"
    assert classify_tool_risk("ops_network_status") == "safe"


def test_core_read_tools_are_safe():
    assert classify_tool_risk("core_health") == "safe"
    assert classify_tool_risk("core_list_plugins") == "safe"
    assert classify_tool_risk("core_capabilities") == "safe"


def test_risk_classification_is_case_insensitive():
    assert classify_tool_risk("WG_INTERFACE_RESTART") == "dangerous"


def test_keyword_risk_classification():
    assert classify_tool_risk("wg_purge_delete_all") == "dangerous"
    assert classify_tool_risk("wg_deploy_config") == "moderate"
    assert classify_tool_risk("wg_add_client") == "moderate"
    assert classify_tool_risk("wg_unknown_operation") == "safe"


def test_read_only_config_tools_are_safe():
    assert classify_tool_risk("wg_check_interface_config") == "safe"
    assert classify_tool_risk("wg_show_interface_config") == "safe"
    assert classify_tool_risk("wg_list_interfaces") == "safe"
    assert classify_tool_risk("wg_list_peers") == "safe"
    assert classify_tool_risk("wg_stats") == "safe"
    assert classify_tool_risk("wg_get_client_artifact") == "safe"
    assert classify_tool_risk("ops_scan_health") == "safe"
    assert classify_tool_risk("ops_dmesg") == "safe"
    assert classify_tool_risk("ops_failed_units") == "safe"
    assert classify_tool_risk("ops_log_search") == "safe"
    assert classify_tool_risk("ops_network_connections") == "safe"


def test_read_only_mutations_keep_their_risk():
    assert classify_tool_risk("wg_create_peer") == "moderate"


def test_keyword_matching_ignores_substrings():
    # "rm" inside "format" and "add" inside "address" must not trigger.
    assert classify_tool_risk("wg_format_disk") == "safe"
    assert classify_tool_risk("ops_address_lookup") == "safe"


def test_declared_risk_overrides_heuristics():
    # A plugin may downgrade a nominally dangerous name...
    assert classify_tool_risk("ops_run_command", "safe") == "safe"
    # ...or upgrade a name the heuristics would treat as safe.
    assert classify_tool_risk("core_health", "dangerous") == "dangerous"


def test_declared_risk_is_normalized_and_falls_back_when_unknown():
    assert classify_tool_risk("core_health", "  SAFE ") == "safe"
    assert classify_tool_risk("wg_remove_peer", "nonsense") == "dangerous"


def _clear_admin_settings(monkeypatch, **overrides):
    defaults = {
        "admin_token": "",
        "admin_password_hash": "",
        "admin_totp_secret": "",
        "session_secret": "",
    }
    for key, value in {**defaults, **overrides}.items():
        monkeypatch.setattr(settings, key, value)


def test_require_admin_token_bearer_accepts_matching(monkeypatch):
    _clear_admin_settings(monkeypatch, admin_token="top-secret")
    assert require_admin_token("top-secret") is None


def test_require_admin_token_bearer_rejects_wrong_and_missing(monkeypatch):
    _clear_admin_settings(monkeypatch, admin_token="top-secret")

    with pytest.raises(HTTPException) as wrong:
        require_admin_token("nope")
    assert wrong.value.status_code == 401

    with pytest.raises(HTTPException) as missing:
        require_admin_token(None)
    assert missing.value.status_code == 401


def test_require_admin_token_503_when_unconfigured(monkeypatch):
    _clear_admin_settings(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        require_admin_token("anything")
    assert exc.value.status_code == 503


def test_require_admin_token_accepts_static_token_in_jwt_mode(monkeypatch):
    _clear_admin_settings(
        monkeypatch,
        admin_password_hash="hash",
        admin_totp_secret="secret",
        session_secret="jwt-session-secret-long-enough-hs256",
        admin_token="shared-token",
    )

    assert require_admin_token("shared-token") is None

    with pytest.raises(HTTPException) as exc:
        require_admin_token("wrong")
    assert exc.value.status_code == 401


def test_require_admin_token_jwt_mode(monkeypatch):
    _clear_admin_settings(
        monkeypatch,
        admin_password_hash="hash",
        admin_totp_secret="secret",
        session_secret="jwt-session-secret-long-enough-hs256",
    )
    token = create_jwt_token({"username": "admin"}, "jwt-session-secret-long-enough-hs256")

    assert require_admin_token(token) is None

    with pytest.raises(HTTPException) as exc:
        require_admin_token(None)
    assert exc.value.status_code == 401
