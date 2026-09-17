"""Tests for Settings validation at construction time."""

import pytest
from pydantic import ValidationError

from src.config import Settings


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
