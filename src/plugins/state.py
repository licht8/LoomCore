"""Persistent enable/disable state for LoomCore plugins.

The disabled set is stored as ``{"disabled": ["<id>", ...]}`` in a JSON file.
The default location is ``<plugins_dir>/../.loomcore/plugins.json`` and can be
overridden with the ``PLUGINS_STATE_FILE`` setting. Reads never fail hard: a
missing, corrupt or unreadable file yields an empty set plus a warning, so the
core always starts.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger("loomcore.plugins.state")

STATE_FILENAME = "plugins.json"
_STATE_DIR = ".loomcore"


def default_state_path(plugins_dir: str | Path) -> Path:
    """Return ``<plugins_dir>/../.loomcore/plugins.json``."""
    return Path(plugins_dir).resolve().parent / _STATE_DIR / STATE_FILENAME


def resolve_state_path(plugins_dir: str | Path, configured: str = "") -> Path:
    """Resolve the state path, honouring the configured override when set."""
    if configured:
        return Path(configured).expanduser()
    return default_state_path(plugins_dir)


def load_disabled(path: Path) -> set[str]:
    """Read the disabled plugin ids; return an empty set on any problem."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return set()
    except UnicodeDecodeError as exc:
        logger.warning(f"Plugin state '{path}' is not valid UTF-8: {exc}")
        return set()
    except OSError as exc:
        logger.warning(f"Cannot read plugin state '{path}': {exc}")
        return set()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning(f"Invalid plugin state JSON in '{path}': {exc}")
        return set()

    if not isinstance(data, dict) or not isinstance(data.get("disabled"), list):
        logger.warning(f"Plugin state '{path}' has an unexpected shape; ignoring")
        return set()
    return {item for item in data["disabled"] if isinstance(item, str)}


def save_disabled(path: Path, disabled: set[str]) -> bool:
    """Persist the disabled plugin ids atomically.

    Returns:
        True — the file was written.
        False — the path is not writable (state stays runtime-only).
    """
    payload = json.dumps({"disabled": sorted(disabled)}, ensure_ascii=False, indent=2)
    try:
        _atomic_write_text(path, payload + "\n")
    except OSError as exc:
        logger.warning(f"Cannot write plugin state '{path}': {exc}")
        return False
    return True


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` atomically and durably (temp file + ``os.replace``).

    The staged file is fsynced before the replace and the parent directory after
    it, so a crash cannot leave a truncated state file. An existing destination's
    permission bits are preserved; a newly created file defaults to ``0o600``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = 0o600
    with contextlib.suppress(OSError):
        mode = os.stat(path).st_mode & 0o777
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, mode)
            else:  # pragma: no cover - Windows has no os.fchmod
                os.chmod(temporary, mode)
            handle = os.fdopen(fd, "w", encoding="utf-8")
        except BaseException:
            # fdopen did not take ownership of the descriptor; close it so the
            # failing call cannot leak the raw file descriptor.
            with contextlib.suppress(OSError):
                os.close(fd)
            raise
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def _fsync_directory(directory: Path) -> None:
    """Best-effort fsync of ``directory`` (no-op where unsupported)."""
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        with contextlib.suppress(OSError):
            os.fsync(dir_fd)
    finally:
        with contextlib.suppress(OSError):
            os.close(dir_fd)
