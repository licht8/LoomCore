"""Tests for the central core version resolution."""

import logging
from importlib.metadata import PackageNotFoundError

from src.core import version as version_module


def test_resolve_version_uses_metadata(monkeypatch):
    monkeypatch.setattr(version_module, "version", lambda name: "1.2.3")
    assert version_module._resolve_version() == "1.2.3"


def test_resolve_version_warns_on_fallback(monkeypatch, caplog):
    def raise_missing(name):
        raise PackageNotFoundError(name)

    monkeypatch.setattr(version_module, "version", raise_missing)
    with caplog.at_level(logging.WARNING, logger="loomcore.version"):
        result = version_module._resolve_version()
    assert result == "0.0.1"
    assert any("fallback" in message for message in caplog.messages)
