"""Tests for the startup security-posture checks."""

import logging

import pytest

from src.config import settings
from src.core import startup_checks


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


def test_empty_revocation_store_warns_in_memory(monkeypatch, caplog):
    _settings(monkeypatch, admin_token="static", loomcore_revoked_file="")
    with caplog.at_level(logging.WARNING, logger="loomcore.security"):
        startup_checks.check_security_posture()
    assert any("in memory" in message for message in caplog.messages)


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
