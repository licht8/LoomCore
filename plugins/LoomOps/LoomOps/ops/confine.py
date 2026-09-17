"""Symlink-safe path confinement primitives.

``files.resolve_path`` checks the fully resolved path, but the actual file
operation happens later, so a symlink swapped in between could escape the
allowed roots. The helpers here re-walk the resolved path without ever
following a symlink.

On POSIX (``DIR_FD_SAFE``) every component is opened relative to the previous
handle with ``O_NOFOLLOW``/``O_DIRECTORY``, so a swap cannot be followed at all.
Elsewhere the fallback is best-effort: it ``lstat``-checks every component,
re-checks the opened leaf's device/inode, and re-verifies the resolved parent
right before the operation; a parent swapped for a symlink after that last
check cannot be ruled out without ``dir_fd`` support.
"""

from __future__ import annotations

import contextlib
import errno
import os
import stat
import tempfile
from pathlib import Path
from typing import IO

DIR_FD_SAFE = (
    hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_DIRECTORY")
    and all(
        function in os.supports_dir_fd
        for function in (os.open, os.mkdir, os.rename, os.stat, os.unlink)
    )
)

_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
if hasattr(os, "O_NOFOLLOW"):
    _READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
else:
    _READ_FLAGS = os.O_RDONLY
_CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)


def open_fd(fd: int, mode: str) -> IO[bytes]:
    """Wrap a raw descriptor in a file object, closing it if that fails."""
    try:
        return os.fdopen(fd, mode)
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(fd)
        raise


def matching_root(target: Path, roots: list[Path]) -> Path | None:
    """The longest configured root that contains ``target``, if any."""
    best: Path | None = None
    for root in roots:
        contained = target == root or root in target.parents
        if contained and (best is None or len(root.parts) > len(best.parts)):
            best = root
    return best


def _relative_parts(root: Path, target: Path) -> list[str]:
    if target == root:
        return []
    if root not in target.parents:
        raise ValueError(f"Path '{target}' is outside the allowed paths")
    return list(target.relative_to(root).parts)


def _forbid_symlink(path: Path) -> ValueError:
    return ValueError(f"Refusing to follow symlink: {path}")


def _require_root(root: Path) -> None:
    """The configured root must exist; only its subdirectories are created."""
    if not root.exists():
        raise ValueError(f"Allowed path does not exist: {root}")
    if not root.is_dir():
        raise ValueError(f"Allowed path is not a directory: {root}")


def _verify_parent(root: Path, target: Path) -> None:
    """Best-effort fallback guard against a parent swapped for a symlink."""
    try:
        resolved = target.parent.resolve()
    except (OSError, RuntimeError) as exc:
        raise _forbid_symlink(target.parent) from exc
    if resolved != root and root not in resolved.parents:
        raise _forbid_symlink(target.parent)


def _ensure_below_root(root: Path, directory: Path) -> None:
    """Create only the missing components between ``root`` and ``directory``."""
    current = root
    for part in _relative_parts(root, directory):
        current = current / part
        with contextlib.suppress(FileExistsError):
            current.mkdir()


def _check_components(root: Path, target: Path, *, allow_missing: bool) -> None:
    """lstat every component of ``target`` below ``root`` and reject symlinks."""
    current = root
    for part in _relative_parts(root, target):
        current = current / part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            if allow_missing:
                return
            raise
        if stat.S_ISLNK(info.st_mode):
            raise _forbid_symlink(current)


def _is_symlink(part: str, parent_fd: int) -> bool:
    try:
        info = os.stat(part, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        return False
    return stat.S_ISLNK(info.st_mode)


def _open_or_create_dir(part: str, parent_fd: int, *, create: bool, path: Path) -> int:
    try:
        return os.open(part, _DIR_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            raise
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR) and _is_symlink(part, parent_fd):
            raise _forbid_symlink(path) from exc
        raise
    with contextlib.suppress(FileExistsError):
        os.mkdir(part, dir_fd=parent_fd)
    try:
        return os.open(part, _DIR_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR) and _is_symlink(part, parent_fd):
            raise _forbid_symlink(path) from exc
        raise


def _open_dir_chain(root: Path, directory: Path, *, create: bool) -> int:
    """Open ``directory`` below ``root``, one no-follow handle per component."""
    fd = os.open(root, _DIR_FLAGS)
    current = root
    try:
        for part in _relative_parts(root, directory):
            current = current / part
            next_fd = _open_or_create_dir(part, fd, create=create, path=current)
            os.close(fd)
            fd = next_fd
    except BaseException:
        os.close(fd)
        raise
    return fd


def _reject_symlinked_leaf(parent_fd: int, target: Path) -> None:
    try:
        info = os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(info.st_mode):
        raise ValueError(f"Path is a directory: {target}")
    if stat.S_ISLNK(info.st_mode):
        raise _forbid_symlink(target)


def open_read(root: Path, target: Path) -> tuple[int, os.stat_result]:
    """Open ``target`` for reading, never following a symlink.

    Returns ``(fd, fstat_result)``; the caller owns ``fd``. Raises
    ``FileNotFoundError`` when the leaf is missing or not a regular file, and
    ``ValueError`` when a symlink (or a same-path swap) is detected.
    """
    if target == root or not root.is_dir():
        raise FileNotFoundError(f"Not a regular file: {target}")
    if DIR_FD_SAFE:
        return _open_read_dir_fd(root, target)
    return _open_read_fallback(root, target)


def _open_read_dir_fd(root: Path, target: Path) -> tuple[int, os.stat_result]:
    parent_fd = _open_dir_chain(root, target.parent, create=False)
    try:
        try:
            fd = os.open(target.name, _READ_FLAGS, dir_fd=parent_fd)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise _forbid_symlink(target) from exc
            raise
    finally:
        os.close(parent_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise FileNotFoundError(f"Not a regular file: {target}")
    except BaseException:
        os.close(fd)
        raise
    return fd, info


def _open_read_fallback(root: Path, target: Path) -> tuple[int, os.stat_result]:
    _check_components(root, target, allow_missing=False)
    _verify_parent(root, target)
    try:
        fd = os.open(target, _READ_FLAGS)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise _forbid_symlink(target) from exc
        raise
    try:
        info = os.fstat(fd)
        opened = os.lstat(target)
        if not stat.S_ISREG(info.st_mode):
            raise FileNotFoundError(f"Not a regular file: {target}")
        if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
            raise _forbid_symlink(target)
    except BaseException:
        os.close(fd)
        raise
    return fd, info


def write_atomic(root: Path, target: Path, data: bytes) -> None:
    """Atomically replace ``target`` with ``data``, never through a symlink."""
    if target == root:
        raise ValueError(f"Path is a directory: {target}")
    _require_root(root)
    if DIR_FD_SAFE:
        _write_atomic_dir_fd(root, target, data)
    else:
        _write_atomic_fallback(root, target, data)


def _write_atomic_dir_fd(root: Path, target: Path, data: bytes) -> None:
    parent_fd = _open_dir_chain(root, target.parent, create=True)
    try:
        _reject_symlinked_leaf(parent_fd, target)
        tmp_name = f".{target.name}.{os.urandom(8).hex()}"
        fd = os.open(tmp_name, _CREATE_FLAGS, 0o600, dir_fd=parent_fd)
        try:
            with open_fd(fd, "wb") as handle:
                handle.write(data)
            os.rename(
                tmp_name, target.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd
            )
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name, dir_fd=parent_fd)
            raise
    finally:
        os.close(parent_fd)


def _write_atomic_fallback(root: Path, target: Path, data: bytes) -> None:
    _check_components(root, target, allow_missing=True)
    try:
        info = os.lstat(target)
    except FileNotFoundError:
        info = None
    if info is not None and stat.S_ISDIR(info.st_mode):
        raise ValueError(f"Path is a directory: {target}")
    _ensure_below_root(root, target.parent)
    _verify_parent(root, target)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with open_fd(fd, "wb") as handle:
            handle.write(data)
        _verify_parent(root, target)
        os.replace(tmp_name, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
