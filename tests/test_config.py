"""Tests for Settings validation at construction time."""

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from src.config import DEFAULT_DATABASE_URL, Settings


def _settings(**overrides):
    """Build a hermetic Settings that ignores the ambient environment/.env."""
    defaults = {
        "auth_mode": "",
        "admin_token": "",
        "admin_password_hash": "",
        "admin_totp_secret": "",
        "session_secret": "",
    }
    return Settings(_env_file=None, **{**defaults, **overrides})


def test_password_plus_totp_requires_session_secret():
    with pytest.raises(ValidationError, match="SESSION_SECRET must be set"):
        _settings(admin_password_hash="hash", admin_totp_secret="totp")


def test_explicit_jwt_mode_without_session_secret_is_rejected():
    with pytest.raises(ValidationError, match="SESSION_SECRET must be set"):
        _settings(auth_mode="jwt")


def test_explicit_both_mode_without_session_secret_is_rejected():
    with pytest.raises(ValidationError, match="SESSION_SECRET must be set"):
        _settings(auth_mode="both")


def test_whitespace_session_secret_is_rejected():
    with pytest.raises(ValidationError, match="SESSION_SECRET must be set"):
        _settings(
            admin_password_hash="hash",
            admin_totp_secret="totp",
            session_secret="   ",
        )


def test_password_plus_totp_with_session_secret_is_accepted():
    config = _settings(
        admin_password_hash="hash",
        admin_totp_secret="totp",
        session_secret="session-secret",
    )
    assert config.session_secret == "session-secret"


def test_explicit_jwt_mode_with_session_secret_is_accepted():
    config = _settings(auth_mode="jwt", session_secret="session-secret")
    assert config.auth_mode == "jwt"


def test_token_only_mode_without_session_secret_is_accepted():
    config = _settings(admin_token="static-token")
    assert config.admin_token == "static-token"


def test_explicit_token_mode_with_password_totp_is_accepted():
    config = _settings(
        auth_mode="token",
        admin_token="static-token",
        admin_password_hash="hash",
        admin_totp_secret="totp",
    )
    assert config.auth_mode == "token"


def test_partial_credentials_without_session_secret_are_accepted():
    config = _settings(admin_password_hash="hash")
    assert config.admin_password_hash == "hash"


def test_cors_origins_default_to_localhost():
    assert _settings().cors_origins == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]


def test_cors_origins_parsed_from_comma_separated_env(monkeypatch):
    monkeypatch.setenv(
        "CORS_ORIGINS", "https://hub.example.com, https://alt.example.com"
    )
    assert _settings().cors_origins == [
        "https://hub.example.com",
        "https://alt.example.com",
    ]


def test_cors_origins_accepts_a_list_directly():
    config = _settings(cors_origins=["https://hub.example.com"])
    assert config.cors_origins == ["https://hub.example.com"]


def test_database_url_default_has_no_embedded_credentials():
    url = make_url(_settings().database_url)
    assert url.username is None
    assert url.password is None


def test_database_url_default_is_the_placeholder():
    assert _settings().database_url == DEFAULT_DATABASE_URL


def test_report_commands_parsed_from_env(monkeypatch):
    monkeypatch.setenv("LOOMCORE_REPORT_COMMANDS", "uptime, df")
    assert _settings().report_commands == "uptime, df"


def test_report_commands_default_empty(monkeypatch):
    monkeypatch.delenv("LOOMCORE_REPORT_COMMANDS", raising=False)
    assert _settings().report_commands == ""
