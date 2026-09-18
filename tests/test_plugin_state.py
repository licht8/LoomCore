"""Unit tests for the persisted plugin enable/disable state."""

import json
import logging
from pathlib import Path

from src.plugins import state as state_module


def test_default_state_path():
    path = state_module.default_state_path("/app/plugins")
    assert path.name == "plugins.json"
    assert path.parent.name == ".loomcore"


def test_resolve_state_path_override():
    assert state_module.resolve_state_path("/app/plugins", "/tmp/x.json") == Path(
        "/tmp/x.json"
    )


def test_resolve_state_path_default():
    assert state_module.resolve_state_path("/app/plugins") == (
        state_module.default_state_path("/app/plugins")
    )


def test_load_disabled_missing(tmp_path):
    assert state_module.load_disabled(tmp_path / "plugins.json") == set()


def test_load_disabled_corrupt(tmp_path):
    path = tmp_path / "plugins.json"
    path.write_text("{broken", encoding="utf-8")
    assert state_module.load_disabled(path) == set()


def test_load_disabled_rejects_invalid_utf8(tmp_path, caplog):
    path = tmp_path / "plugins.json"
    path.write_bytes(b"\xff\xfe\x00")
    with caplog.at_level(logging.WARNING, logger="loomcore.plugins.state"):
        assert state_module.load_disabled(path) == set()
    assert "is not valid UTF-8" in caplog.text


def test_load_disabled_wrong_shape(tmp_path):
    path = tmp_path / "plugins.json"
    path.write_text(json.dumps(["a", "b"]), encoding="utf-8")
    assert state_module.load_disabled(path) == set()


def test_load_disabled_filters_non_strings(tmp_path):
    path = tmp_path / "plugins.json"
    path.write_text(json.dumps({"disabled": ["a", 1, None]}), encoding="utf-8")
    assert state_module.load_disabled(path) == {"a"}


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / ".loomcore" / "plugins.json"
    assert state_module.save_disabled(path, {"b", "a"}) is True
    assert state_module.load_disabled(path) == {"a", "b"}
    assert json.loads(path.read_text(encoding="utf-8")) == {"disabled": ["a", "b"]}


def test_save_disabled_unwritable_returns_false(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("file", encoding="utf-8")
    assert state_module.save_disabled(blocker / "plugins.json", {"a"}) is False
