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
    ],
)
def test_explicit_dangerous_tools(tool_name):
    assert classify_tool_risk(tool_name) == "dangerous"


def test_risk_classification_is_case_insensitive():
    assert classify_tool_risk("WG_INTERFACE_RESTART") == "dangerous"


def test_keyword_risk_classification():
    assert classify_tool_risk("wg_purge_delete_all") == "dangerous"
    assert classify_tool_risk("wg_deploy_config") == "moderate"
    assert classify_tool_risk("wg_add_client") == "moderate"
    assert classify_tool_risk("wg_unknown_operation") == "safe"


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
