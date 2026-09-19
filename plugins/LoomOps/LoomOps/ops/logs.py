"""Log access: journald when reachable, plain files otherwise.

The hub mounts the host journal read-only (persistent under ``/host/journal``,
volatile under ``/host/journal-volatile``) and the host log directory under
``/host/var/log`` (see the plugin manifest). A directory only counts as
available when it actually holds ``*.journal`` files; otherwise the tools fall
back to scanning files under ``LOOMOPS_LOG_ROOTS``.
"""

from __future__ import annotations

import heapq
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

from . import bounded_int, confine

DEFAULT_LOG_ROOTS = ("/host/var/log",)
DEFAULT_MAX_LINES = 200
MAX_LINES = 5000
JOURNAL_TIMEOUT = 15
_TAIL_BYTES = 128_000
# Host journal files are bind-mounted read-only here (see the plugin manifest and
# docker-compose.yml). The persistent store is preferred; the volatile store
# (/run/log/journal) is the fallback for hosts without persistent journald.
DEFAULT_JOURNAL_DIRS = ("/host/journal", "/host/journal-volatile")
# journalctl prints this on success when nothing matches; it is not an error.
NO_ENTRIES_MARKER = "-- No entries --"

_ERROR_MARKERS = ("error", "fail", "critical", "panic", "fatal", "segfault", "oom")


def log_roots() -> list[Path]:
    raw = os.environ.get("LOOMOPS_LOG_ROOTS", "")
    if not raw.strip():
        return [Path(item) for item in DEFAULT_LOG_ROOTS]
    roots: list[Path] = []
    for item in raw.replace(os.pathsep, ",").split(","):
        item = item.strip()
        if item:
            roots.append(Path(item).expanduser())
    return roots


def journal_dirs() -> list[Path]:
    """Configured host journal directories, most persistent first."""
    raw = os.environ.get("LOOMOPS_JOURNAL_DIRS") or os.environ.get("LOOMOPS_JOURNAL_DIR", "")
    if not raw.strip():
        return [Path(item) for item in DEFAULT_JOURNAL_DIRS]
    dirs: list[Path] = []
    for item in raw.replace(os.pathsep, ",").split(","):
        item = item.strip()
        if item:
            dirs.append(Path(item).expanduser())
    return dirs


def _resolved_root(root: Path) -> Path | None:
    try:
        return root.expanduser().resolve()
    except (OSError, RuntimeError):
        return None


def _walk_regular_files(root: Path):
    """Yield the regular files below ``root`` without following symlinks."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            path = Path(dirpath) / name
            try:
                info = os.lstat(path)
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                yield path


def _has_journal_files(directory: Path) -> bool:
    """True when the directory actually holds journal files.

    Journal files live under a machine-id subdirectory, so the check recurses.
    A merely-mounted-but-empty directory must not count as available, and
    symlinked entries never count.
    """
    root = _resolved_root(directory)
    if root is None or not root.is_dir():
        return False
    try:
        for path in _walk_regular_files(root):
            if path.name.endswith(".journal"):
                return True
    except OSError:
        return False
    return False


def journal_dir() -> Path | None:
    """The first mounted directory that holds journal files, if any."""
    for directory in journal_dirs():
        if _has_journal_files(directory):
            return directory
    return None


def max_log_lines() -> int:
    return bounded_int(
        os.environ.get("LOOMOPS_MAX_LOG_LINES"), DEFAULT_MAX_LINES, MAX_LINES
    )


def journal_available() -> bool:
    if shutil.which("journalctl") is None:
        return False
    return journal_dir() is not None


def _journal_lines(text: str) -> list[str]:
    """Split journalctl output, dropping blanks and the "no entries" marker."""
    lines: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped == NO_ENTRIES_MARKER:
            continue
        lines.append(line)
    return lines


def _run_journal(args: list[str]) -> str:
    binary = shutil.which("journalctl")
    if binary is None:
        raise RuntimeError("journalctl is not available")
    directory = journal_dir()
    if directory is None:
        raise RuntimeError("no journal files are available to read")
    argv = [binary, "--no-pager", "--directory", str(directory)]
    argv += args
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=JOURNAL_TIMEOUT,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Cannot read the journal: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip() or "unknown error"
        raise RuntimeError(f"journalctl failed: {detail}")
    return completed.stdout or ""


def _tail_lines(root: Path, path: Path, count: int) -> list[str]:
    try:
        fd, info = confine.open_read(root, path)
    except (OSError, ValueError):
        return []
    try:
        with confine.open_fd(fd, "rb") as handle:
            size = info.st_size
            block = min(size, _TAIL_BYTES)
            handle.seek(size - block)
            data = handle.read(block)
    except OSError:
        return []
    return data.decode("utf-8", errors="replace").splitlines()[-count:]


def _iter_log_files(limit: int = 100):
    for root in log_roots():
        base = _resolved_root(root)
        if base is None or not base.is_dir():
            continue
        # nsmallest keeps only ``limit`` paths in memory (the tree is still
        # traversed, but nothing is materialized); a plain sorted() would hold
        # every path under the root at once.
        for path in heapq.nsmallest(limit, _walk_regular_files(base)):
            yield base, path


def log_search(arguments: dict[str, Any]) -> dict[str, Any]:
    source = str(arguments.get("source") or "auto").lower()
    limit = bounded_int(arguments.get("limit"), max_log_lines(), MAX_LINES)
    query = str(arguments.get("query") or "")
    since = str(arguments.get("since") or "")
    unit = str(arguments.get("unit") or "")

    if source in ("auto", "journal") and journal_available():
        return _search_journal(query=query, since=since, unit=unit, limit=limit)
    if source == "journal":
        raise RuntimeError(
            "journald is not available; use source='files' or mount the host "
            "journal under /host/journal (persistent) or /host/journal-volatile"
        )
    return _search_files(query=query, since=since, unit=unit, limit=limit)


def _search_journal(*, query: str, since: str, unit: str, limit: int) -> dict[str, Any]:
    args = ["--lines", str(limit)]
    if since:
        args += ["--since", since]
    if unit:
        args += ["--unit", unit]
    if query:
        # --grep treats the value as a PCRE. Escape it so metacharacters are
        # matched literally (as the file fallback does) instead of changing the
        # match or making journalctl reject the pattern.
        args += ["--grep", re.escape(query)]
    text = _run_journal(args)
    lines = _journal_lines(text)
    return {
        "output": "\n".join(lines) or "No matching log entries",
        "source": "journal",
        "entries": lines,
        "count": len(lines),
    }


def _search_files(*, query: str, since: str, unit: str, limit: int) -> dict[str, Any]:
    roots = log_roots()
    if not roots:
        raise RuntimeError("No log roots configured; set LOOMOPS_LOG_ROOTS")
    if since or unit:
        # File scanning cannot honor journald's time/unit selectors; failing
        # loudly beats silently returning an unfiltered result.
        raise RuntimeError(
            "since/unit filters are only supported by journald; log files "
            "cannot be time-filtered — mount the host journal or drop them"
        )
    entries: list[str] = []
    scanned = 0
    needle = query.lower()
    for root, path in _iter_log_files():
        scanned += 1
        for line in _tail_lines(root, path, 200):
            if not needle or needle in line.lower():
                entries.append(f"{path.name}: {line}")
    selected = entries[-limit:]
    return {
        "output": "\n".join(selected) or "No matching log entries",
        "source": "files",
        "entries": selected,
        "count": len(selected),
        "roots": [str(root) for root in roots],
        "files_scanned": scanned,
    }


def journal_errors(arguments: dict[str, Any]) -> dict[str, Any]:
    limit = bounded_int(arguments.get("limit"), DEFAULT_MAX_LINES, MAX_LINES)
    since = str(arguments.get("since") or "1 hour ago")
    if journal_available():
        text = _run_journal(
            ["--lines", str(limit), "--since", since, "--priority", "err"]
        )
        lines = _journal_lines(text)
        return {
            "output": "\n".join(lines) or "No errors in the journal",
            "source": "journal",
            "entries": lines,
            "count": len(lines),
        }
    entries: list[str] = []
    for root, path in _iter_log_files():
        for line in _tail_lines(root, path, 200):
            lowered = line.lower()
            if any(marker in lowered for marker in _ERROR_MARKERS):
                entries.append(f"{path.name}: {line}")
    selected = entries[-limit:]
    return {
        "output": "\n".join(selected) or "No error lines found in the log roots",
        "source": "files",
        "entries": selected,
        "count": len(selected),
    }
