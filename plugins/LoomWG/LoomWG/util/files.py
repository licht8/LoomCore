"""Atomic file writes shared across the plugin."""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path


def _fsync_directory(directory: Path) -> None:
    """Best-effort fsync of ``directory`` so a rename survives a crash.

    Not supported on every platform (e.g. Windows cannot open a directory),
    so failures are ignored: the rename is already atomic, this only hardens
    it against power loss.
    """
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def atomic_write_text(path: Path, text: str, *, mode: int | None = None) -> None:
    """Write ``text`` to ``path`` atomically (temp file + ``os.replace``).

    The parent directory is created when missing. When ``mode`` is given, the
    temporary file is chmod-ed before it replaces the destination, so secrets
    such as WireGuard keys stay mode 600. The temp file is flushed and
    ``fsync``-ed before the rename, and the parent directory is synced after,
    so a crash cannot leave a zero-length destination.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise
