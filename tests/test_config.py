"""Tests for Settings validation at construction time."""

import pytest
from pydantic import ValidationError

from src.config import Settings


def test_password_plus_totp_requires_session_secret():
    with pytest.raises(ValidationError, match="SESSION_SECRET must be set"):
        Settings(
            admin_password_hash="hash",
            admin_totp_secret="totp",
            session_secret="",
        )


def test_password_plus_totp_with_session_secret_is_accepted():
    config = Settings(
        admin_password_hash="hash",
        admin_totp_secret="totp",
        session_secret="session-secret",
    )
    assert config.session_secret == "session-secret"


def test_token_only_mode_without_session_secret_is_accepted():
    config = Settings(
        admin_token="static-token",
        admin_password_hash="",
        admin_totp_secret="",
        session_secret="",
    )
    assert config.admin_token == "static-token"


def test_explicit_token_mode_with_password_totp_is_accepted():
    config = Settings(
        auth_mode="token",
        admin_token="static-token",
        admin_password_hash="hash",
        admin_totp_secret="totp",
        session_secret="",
    )
    assert config.auth_mode == "token"


def test_partial_credentials_without_session_secret_are_accepted():
    config = Settings(
        admin_password_hash="hash",
        admin_totp_secret="",
        session_secret="",
    )
    assert config.admin_password_hash == "hash"
