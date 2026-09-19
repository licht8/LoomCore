"""Process inspection and signalling via /proc."""

from __future__ import annotations

import os
import signal
from pathlib import Path
from typing import Any

from . import audit_event

PROC_ROOT = Path("/proc")

DEFAULT_LIMIT = 200
MAX_LIMIT = 2000

ALLOWED_SIGNALS = {
    "TERM": getattr(signal, "SIGTERM", 15),
    "KILL": getattr(signal, "SIGKILL", 9),
    "INT": getattr(signal, "SIGINT", 2),
    "HUP": getattr(signal, "SIGHUP", 1),
}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _start_time(pid: int) -> str | None:
    """The process start time (22nd field of /proc/<pid>/stat), or None.

    Used to pin a target's identity across a signal: the kernel may recycle a
    pid once the original process exits, so the bare pid is not enough.
    """
    text = _read_text(PROC_ROOT / str(pid) / "stat")
    if not text:
        return None
    close = text.rfind(")")
    if close == -1:
        return None
    # Fields after the comm (which may contain spaces) start at State (field 3);
    # starttime is field 22, i.e. index 22 - 3 within this slice.
    fields = text[close + 1 :].split()
    index = 22 - 3
    if len(fields) <= index:
        return None
    return fields[index]


def _process(pid: int) -> dict[str, Any] | None:
    base = PROC_ROOT / str(pid)
    if not base.is_dir():
        return None
    status_text = _read_text(base / "status")
    if not status_text:
        # The pid vanished mid-scan (or status is unreadable): treat it as gone
        # rather than emitting a blank row that looks like live data.
        return None
    name = ""
    state = ""
    for line in status_text.splitlines():
        if line.startswith("Name:"):
            name = line.partition(":")[2].strip()
        elif line.startswith("State:"):
            state = line.partition(":")[2].strip()
    cmdline = _read_text(base / "cmdline").replace("\x00", " ").strip()
    return {"pid": pid, "name": name, "state": state, "cmdline": cmdline}


def list_processes(arguments: dict[str, Any]) -> dict[str, Any]:
    limit = arguments.get("limit")
    limit = DEFAULT_LIMIT if not isinstance(limit, int) or limit <= 0 else min(limit, MAX_LIMIT)
    try:
        entries = sorted(
            (entry for entry in PROC_ROOT.iterdir() if entry.name.isdigit()),
            key=lambda entry: int(entry.name),
        )
    except OSError as exc:
        raise RuntimeError(f"Cannot read /proc: {exc}") from exc

    rows: list[dict[str, Any]] = []
    for entry in entries:
        info = _process(int(entry.name))
        if info is not None:
            rows.append(info)
        if len(rows) >= limit:
            break
    lines = [f"{'PID':>7}  {'STATE':<6}  COMMAND"]
    lines.extend(
        f"{row['pid']:>7}  {row['state'][:6]:<6}  {row['cmdline'] or row['name']}" for row in rows
    )
    return {
        "output": "\n".join(lines),
        "processes": rows,
        "count": len(rows),
        "limited": len(rows) >= limit,
    }


def kill_process(arguments: dict[str, Any]) -> dict[str, Any]:
    pid = arguments.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int):
        raise ValueError("pid must be an integer")
    # Pin the target's identity now, before the remaining validation, and
    # confirm it just before signalling: the two reads straddle that work, so a
    # pid that exits and is recycled in between is refused instead of receiving
    # a signal meant for the original process.
    start_time = _start_time(pid)
    if pid <= 1:
        raise ValueError("Refusing to signal pid <= 1")
    if pid == os.getpid():
        raise ValueError("Refusing to signal the plugin's own process")
    name = str(arguments.get("signal") or "TERM").upper()
    sig = ALLOWED_SIGNALS.get(name)
    if sig is None:
        raise ValueError(
            f"Unsupported signal '{name}'. Allowed: {', '.join(ALLOWED_SIGNALS)}"
        )
    if start_time is None:
        raise ValueError(f"No process with pid {pid}")
    if _start_time(pid) != start_time:
        raise ValueError(
            f"Process {pid} changed before it could be signalled; refusing to signal"
        )
    try:
        os.kill(pid, sig)
    except ProcessLookupError as exc:
        raise ValueError(f"No process with pid {pid}") from exc
    except PermissionError as exc:
        raise RuntimeError(f"Not permitted to signal pid {pid}") from exc
    audit_error = audit_event("ops_kill_process", pid=pid, signal=name)
    return {
        "output": f"Sent SIG{name} to pid {pid}",
        "pid": pid,
        "signal": name,
        "audit_error": audit_error,
    }
