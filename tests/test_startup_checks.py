"""Tests for the startup security-posture checks."""

import logging

import pytest

from src.config import settings
from src.core import startup_checks
from src.llm.config import LlmConfig

_CONFIGURED = LlmConfig(
    provider="custom",
    base_url="https://llm.test/v1",
    model="test-model",
)


def _settings(monkeypatch, **overrides):
    defaults = {
        "auth_mode": "",
        "admin_token": "",
        "admin_password_hash": "",
        "admin_totp_secret": "",
        "secure_cookies": True,
        "require_tls": False,
        "loomcore_revoked_file": "",
    }
    for key, value in {**defaults, **overrides}.items():
        monkeypatch.setattr(settings, key, value)
    monkeypatch.setattr(startup_checks, "get_active", lambda: _CONFIGURED)


def test_secure_posture_is_silent(monkeypatch, tmp_path, caplog):
    store = tmp_path / "revoked.json"
    _settings(
        monkeypatch,
        admin_token="static",
        loomcore_revoked_file=str(store),
    )
    with caplog.at_level(logging.WARNING, logger="loomcore.security"):
        startup_checks.check_security_posture()
    assert caplog.messages == []
    assert store.exists()


def test_insecure_cookie_warns(monkeypatch, tmp_path, caplog):
    _settings(
        monkeypatch,
        admin_token="static",
        secure_cookies=False,
        loomcore_revoked_file=str(tmp_path / "revoked.json"),
    )
    with caplog.at_level(logging.WARNING, logger="loomcore.security"):
        startup_checks.check_security_posture()
    assert any("SECURE_COOKIES" in message for message in caplog.messages)


def test_require_tls_with_insecure_cookies_raises(monkeypatch, tmp_path):
    _settings(
        monkeypatch,
        admin_token="static",
        secure_cookies=False,
        require_tls=True,
        loomcore_revoked_file=str(tmp_path / "revoked.json"),
    )
    with pytest.raises(RuntimeError):
        startup_checks.check_security_posture()


def test_both_credentials_without_auth_mode_warns(monkeypatch, tmp_path, caplog):
    _settings(
        monkeypatch,
        admin_token="static",
        admin_password_hash="h",
        admin_totp_secret="t",
        loomcore_revoked_file=str(tmp_path / "revoked.json"),
    )
    with caplog.at_level(logging.WARNING, logger="loomcore.security"):
        startup_checks.check_security_posture()
    assert any("AUTH_MODE=both" in message for message in caplog.messages)


def test_explicit_auth_mode_does_not_warn_about_both(monkeypatch, tmp_path, caplog):
    _settings(
        monkeypatch,
        auth_mode="both",
        admin_token="static",
        admin_password_hash="h",
        admin_totp_secret="t",
        loomcore_revoked_file=str(tmp_path / "revoked.json"),
    )
    with caplog.at_level(logging.WARNING, logger="loomcore.security"):
        startup_checks.check_security_posture()
    assert caplog.messages == []


def test_jwt_mode_without_credentials_warns(monkeypatch, tmp_path, caplog):
    _settings(
        monkeypatch,
        auth_mode="jwt",
        session_secret="a-secret",
        loomcore_revoked_file=str(tmp_path / "revoked.json"),
    )
    with caplog.at_level(logging.WARNING, logger="loomcore.security"):
        startup_checks.check_security_posture()
    assert any("can never authenticate" in message for message in caplog.messages)


def test_both_mode_with_static_token_names_only_jwt(monkeypatch, tmp_path, caplog):
    _settings(
        monkeypatch,
        auth_mode="both",
        admin_token="static",
        session_secret="a-secret",
        loomcore_revoked_file=str(tmp_path / "revoked.json"),
    )
    with caplog.at_level(logging.WARNING, logger="loomcore.security"):
        startup_checks.check_security_posture()
    message = next(m for m in caplog.messages if "AUTH_MODE=both" in m)
    assert "can never authenticate" in message
    assert "admin access will 503" not in message


def test_both_mode_without_token_warns_admin_503(monkeypatch, tmp_path, caplog):
    _settings(
        monkeypatch,
        auth_mode="both",
        session_secret="a-secret",
        loomcore_revoked_file=str(tmp_path / "revoked.json"),
    )
    with caplog.at_level(logging.WARNING, logger="loomcore.security"):
        startup_checks.check_security_posture()
    assert any("admin access will 503" in message for message in caplog.messages)


def test_empty_revocation_store_warns_in_memory(monkeypatch, caplog):
    _settings(monkeypatch, admin_token="static", loomcore_revoked_file="")
    with caplog.at_level(logging.WARNING, logger="loomcore.security"):
        startup_checks.check_security_posture()
    assert any("in memory" in message for message in caplog.messages)


def test_revocation_store_created_as_empty_json(monkeypatch, tmp_path, caplog):
    store = tmp_path / "revoked.json"
    _settings(monkeypatch, admin_token="static", loomcore_revoked_file=str(store))
    with caplog.at_level(logging.WARNING, logger="loomcore.security"):
        startup_checks.check_security_posture()
    assert store.exists()
    assert store.read_text(encoding="utf-8").strip() == "{}"
    assert not any("cannot be persisted" in msg for msg in caplog.messages)


def test_existing_revocation_store_is_not_truncated(monkeypatch, tmp_path, caplog):
    store = tmp_path / "revoked.json"
    store.write_text('{"a": 1}\n', encoding="utf-8")
    _settings(monkeypatch, admin_token="static", loomcore_revoked_file=str(store))
    with caplog.at_level(logging.WARNING, logger="loomcore.security"):
        startup_checks.check_security_posture()
    assert store.read_text(encoding="utf-8").strip() == '{"a": 1}'
    assert not any("cannot be persisted" in msg for msg in caplog.messages)


def test_empty_existing_revocation_store_is_left_untouched(
    monkeypatch, tmp_path, caplog
):
    store = tmp_path / "revoked.json"
    store.write_text("", encoding="utf-8")
    _settings(monkeypatch, admin_token="static", loomcore_revoked_file=str(store))
    with caplog.at_level(logging.WARNING, logger="loomcore.security"):
        startup_checks.check_security_posture()
    assert store.read_text(encoding="utf-8") == ""
    assert not any("cannot be persisted" in msg for msg in caplog.messages)


def test_unwritable_revocation_store_warns(monkeypatch, tmp_path, caplog):
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    _settings(
        monkeypatch,
        admin_token="static",
        loomcore_revoked_file=str(store_dir),
    )
    with caplog.at_level(logging.WARNING, logger="loomcore.security"):
        startup_checks.check_security_posture()
    assert any("cannot be persisted to" in message for message in caplog.messages)


def test_unconfigured_llm_provider_warns(monkeypatch, tmp_path, caplog):
    _settings(
        monkeypatch,
        admin_token="static",
        loomcore_revoked_file=str(tmp_path / "revoked.json"),
    )
    monkeypatch.setattr(startup_checks, "get_active", lambda: LlmConfig())
    with caplog.at_level(logging.WARNING, logger="loomcore.security"):
        startup_checks.check_security_posture()
    assert any(
        "No LLM provider is configured" in message for message in caplog.messages
    )
