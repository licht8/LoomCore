"""Unit tests for the persisted plugin enable/disable state."""

import json
import logging
import os
from pathlib import Path

import pytest

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


# === durability (D5.1) ===

def _record_fchmod(monkeypatch):
    seen: dict[str, int] = {}

    def fake_fchmod(fd, mode):
        seen["mode"] = mode

    monkeypatch.setattr(state_module.os, "fchmod", fake_fchmod, raising=False)
    return seen


def test_save_uses_0600_for_new_file(tmp_path, monkeypatch):
    seen = _record_fchmod(monkeypatch)
    path = tmp_path / "plugins.json"
    assert state_module.save_disabled(path, {"a"}) is True
    assert seen["mode"] == 0o600


def test_save_preserves_existing_file_mode(tmp_path, monkeypatch):
    path = tmp_path / "plugins.json"
    path.write_text("{}", encoding="utf-8")
    if os.name != "nt":
        os.chmod(path, 0o640)
    expected = os.stat(path).st_mode & 0o777

    seen = _record_fchmod(monkeypatch)
    assert state_module.save_disabled(path, {"a"}) is True
    assert seen["mode"] == expected


def test_atomic_write_fsyncs_staged_file(tmp_path, monkeypatch):
    path = tmp_path / "out.json"
    synced: list[int] = []
    real_fsync = os.fsync

    def fake_fsync(fd: int) -> None:
        synced.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(state_module.os, "fsync", fake_fsync)
    state_module._atomic_write_text(path, "hello\n")

    assert synced
    assert path.read_text(encoding="utf-8") == "hello\n"


def test_fsync_directory_best_effort(tmp_path, monkeypatch):
    opened: dict[str, object] = {}
    synced: list[int] = []

    def fake_open(path_arg, flags):
        opened["path"] = path_arg
        return 99

    monkeypatch.setattr(state_module.os, "open", fake_open)
    monkeypatch.setattr(state_module.os, "fsync", lambda fd: synced.append(fd))
    monkeypatch.setattr(state_module.os, "close", lambda fd: None)

    state_module._fsync_directory(tmp_path)

    assert opened["path"] == tmp_path
    assert synced == [99]


def test_fsync_directory_ignores_open_errors(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("no directory fd")

    monkeypatch.setattr(state_module.os, "open", boom)

    state_module._fsync_directory(tmp_path)  # must not raise


def test_atomic_write_closes_fd_when_fdopen_fails(tmp_path, monkeypatch):
    path = tmp_path / "out.json"
    closed: list[int] = []
    real_close = state_module.os.close

    def boom(*args, **kwargs):
        raise OSError("fdopen failed")

    def record_close(fd):
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(state_module.os, "fdopen", boom)
    monkeypatch.setattr(state_module.os, "close", record_close)

    with pytest.raises(OSError):
        state_module._atomic_write_text(path, "x")

    assert closed
    assert list(tmp_path.iterdir()) == []
