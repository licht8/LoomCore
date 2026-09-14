"""Log access: journald when reachable, plain files otherwise.

The hub mounts the host journal read-only (persistent under ``/host/journal``,
volatile under ``/host/journal-volatile``) and the host log directory under
``/host/var/log`` (see the plugin manifest). A directory only counts as
available when it actually holds ``*.journal`` files; otherwise the tools fall
back to scanning files under ``LOOMOPS_LOG_ROOTS``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import bounded_int

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


def _has_journal_files(directory: Path) -> bool:
    """True when the directory actually holds journal files.

    Journal files live under a machine-id subdirectory, so the check recurses.
    A merely-mounted-but-empty directory must not count as available.
    """
    try:
        if not directory.is_dir():
            return False
        return next(directory.rglob("*.journal"), None) is not None
    except OSError:
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


def _tail_lines(path: Path, count: int) -> list[str]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            block = min(size, _TAIL_BYTES)
            handle.seek(size - block)
            data = handle.read(block)
    except OSError:
        return []
    return data.decode("utf-8", errors="replace").splitlines()[-count:]


def _iter_log_files(limit: int = 100):
    for root in log_roots():
        if not root.is_dir():
            continue
        count = 0
        for path in sorted(root.rglob("*")):
            if path.is_file():
                yield path
                count += 1
                if count >= limit:
                    break


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
    return _search_files(query=query, limit=limit)


def _search_journal(*, query: str, since: str, unit: str, limit: int) -> dict[str, Any]:
    args = ["--lines", str(limit)]
    if since:
        args += ["--since", since]
    if unit:
        args += ["--unit", unit]
    if query:
        args += ["--grep", query]
    text = _run_journal(args)
    lines = _journal_lines(text)
    return {
        "output": "\n".join(lines) or "No matching log entries",
        "source": "journal",
        "entries": lines,
        "count": len(lines),
    }


def _search_files(*, query: str, limit: int) -> dict[str, Any]:
    roots = log_roots()
    if not roots:
        raise RuntimeError("No log roots configured; set LOOMOPS_LOG_ROOTS")
    entries: list[str] = []
    scanned = 0
    needle = query.lower()
    for path in _iter_log_files():
        scanned += 1
        for line in _tail_lines(path, 200):
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
    for path in _iter_log_files():
        for line in _tail_lines(path, 200):
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
