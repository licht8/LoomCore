"""Tests for AUTH_MODE resolution and credential gating."""

import pytest
from fastapi import HTTPException

from src.auth import create_jwt_token
from src.config import settings
from src.core.security import auth_mode, require_admin_token

SECRET = "test-session-secret-long-enough-for-hs256-0123456789"


def _settings(monkeypatch, **overrides):
    defaults = {
        "auth_mode": "",
        "admin_token": "",
        "admin_password_hash": "",
        "admin_totp_secret": "",
        "session_secret": "",
    }
    for key, value in {**defaults, **overrides}.items():
        monkeypatch.setattr(settings, key, value)


def test_auto_resolves_jwt_when_password_configured(monkeypatch):
    _settings(monkeypatch, admin_password_hash="h", admin_totp_secret="t")
    assert auth_mode() == "jwt"


def test_auto_resolves_token_without_password(monkeypatch):
    _settings(monkeypatch, admin_token="tk")
    assert auth_mode() == "token"


def test_explicit_mode_wins(monkeypatch):
    _settings(
        monkeypatch,
        auth_mode="both",
        admin_password_hash="h",
        admin_totp_secret="t",
    )
    assert auth_mode() == "both"


def test_invalid_mode_falls_back_to_auto(monkeypatch):
    _settings(monkeypatch, auth_mode="nonsense", admin_token="tk")
    assert auth_mode() == "token"


def test_jwt_mode_accepts_jwt(monkeypatch):
    _settings(
        monkeypatch,
        admin_password_hash="h",
        admin_totp_secret="t",
        session_secret=SECRET,
    )
    token = create_jwt_token({"username": "admin"}, SECRET)
    assert require_admin_token(token) is None


def test_jwt_mode_rejects_valid_static_token_with_403(monkeypatch):
    _settings(
        monkeypatch,
        admin_token="shared",
        admin_password_hash="h",
        admin_totp_secret="t",
        session_secret=SECRET,
    )
    with pytest.raises(HTTPException) as exc:
        require_admin_token("shared")
    assert exc.value.status_code == 403


def test_token_mode_rejects_valid_jwt_with_403(monkeypatch):
    _settings(
        monkeypatch,
        auth_mode="token",
        admin_token="shared",
        admin_password_hash="h",
        admin_totp_secret="t",
        session_secret=SECRET,
    )
    token = create_jwt_token({"username": "admin"}, SECRET)
    with pytest.raises(HTTPException) as exc:
        require_admin_token(token)
    assert exc.value.status_code == 403


def test_both_mode_accepts_either_credential(monkeypatch):
    _settings(
        monkeypatch,
        auth_mode="both",
        admin_token="shared",
        admin_password_hash="h",
        admin_totp_secret="t",
        session_secret=SECRET,
    )
    token = create_jwt_token({"username": "admin"}, SECRET)
    assert require_admin_token("shared") is None
    assert require_admin_token(token) is None


def test_jwt_mode_without_credentials_is_503(monkeypatch):
    _settings(monkeypatch, auth_mode="jwt")
    with pytest.raises(HTTPException) as exc:
        require_admin_token("anything")
    assert exc.value.status_code == 503
