"""Tests for the gated timing instrumentation."""

import logging

from src.config import settings
from src.core import timing


def test_log_stage_sanitizes_newlines(monkeypatch, caplog):
    monkeypatch.setattr(settings, "timing_enabled", True)
    with caplog.at_level(logging.INFO, logger="loomcore.timing"):
        timing.log_stage("s", 1.0, tool="a\nb\r")
    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "tool=a b" in message
    assert "\n" not in message
