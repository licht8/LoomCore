"""Read-only host monitoring: load, CPU, memory, dmesg and connections.

Everything here is observational. `pid: host` and `SYSLOG` (see the plugin
manifest) make the data host-wide; without them the plugin sees only the
container and reports degraded access.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from . import bounded_int

PROC_ROOT = Path("/proc")

CPU_SAMPLE_SECONDS = 0.2
DEFAULT_DMESG_LINES = 200
MAX_DMESG_LINES = 5000
DEFAULT_CONNECTION_LIMIT = 200
MAX_CONNECTION_LIMIT = 5000

_HOST_INIT_NAMES = {"systemd", "init"}

# busybox/other minimal dmesg implementations reject util-linux flags.
_USAGE_ERROR_MARKERS = ("invalid option", "unrecognized", "unknown option", "usage:")
_PERMISSION_ERROR_MARKERS = (
    "permission denied",
    "not permitted",
)


def host_pid_active() -> bool:
    """Whether /proc looks like the host PID namespace (init is systemd/init)."""
    try:
        name = (PROC_ROOT / "1" / "comm").read_text(encoding="utf-8").strip()
    except OSError:
        name = ""
    if name:
        return name in _HOST_INIT_NAMES
    try:
        status = (PROC_ROOT / "1" / "status").read_text(encoding="utf-8")
    except OSError:
        return False
    for line in status.splitlines():
        if line.startswith("Name:"):
            return line.partition(":")[2].strip() in _HOST_INIT_NAMES
    return False


def load_average() -> tuple[float, float, float] | None:
    try:
        text = (PROC_ROOT / "loadavg").read_text(encoding="utf-8")
    except OSError:
        return None
    parts = text.split()
    try:
        return float(parts[0]), float(parts[1]), float(parts[2])
    except (IndexError, ValueError):
        return None


def _cpu_times() -> tuple[int, int] | None:
    try:
        first_line = (PROC_ROOT / "stat").read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError):
        return None
    fields = first_line.split()
    if not fields or fields[0] != "cpu":
        return None
    values = [int(value) for value in fields[1:] if value.isdigit()]
    if len(values) < 4:
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return idle, sum(values)


def cpu_percent() -> float | None:
    """Sample /proc/stat twice and return the busy CPU percentage."""
    first = _cpu_times()
    if first is None:
        return None
    time.sleep(CPU_SAMPLE_SECONDS)
    second = _cpu_times()
    if second is None:
        return None
    total_delta = second[1] - first[1]
    if total_delta <= 0:
        return None
    idle_delta = second[0] - first[0]
    return round(max(0.0, 100.0 * (1.0 - idle_delta / total_delta)), 1)


def memory_info() -> dict[str, int]:
    info: dict[str, int] = {}
    try:
        text = (PROC_ROOT / "meminfo").read_text(encoding="utf-8")
    except OSError:
        return info
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        fields = rest.split()
        if fields and fields[0].isdigit():
            info[key.strip()] = int(fields[0])
    return info


def uptime_seconds() -> float | None:
    try:
        text = (PROC_ROOT / "uptime").read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return float(text.split()[0])
    except (IndexError, ValueError):
        return None


def system_metrics(arguments: dict[str, Any]) -> dict[str, Any]:
    load = load_average()
    cpu_count = os.cpu_count() or 1
    memory = memory_info()
    total_kb = memory.get("MemTotal", 0)
    available_kb = memory.get("MemAvailable", memory.get("MemFree", 0))
    used_kb = max(total_kb - available_kb, 0)
    # Without /proc/meminfo (or with no MemTotal) the numbers are unknown, not
    # zero — report them as None and label the row unavailable.
    has_memory = total_kb > 0
    percent = cpu_percent()
    uptime = uptime_seconds()

    memory_row = (
        f"Memory: {_pct(used_kb, total_kb)} used of {total_kb // 1024} MiB"
        if has_memory
        else "Memory: n/a"
    )
    rows = [
        f"CPU: {percent if percent is not None else 'n/a'}% across {cpu_count} cores",
        f"Load: {_fmt_load(load)}",
        memory_row,
        f"Uptime: {_fmt_uptime(uptime)}",
        f"Host PID namespace: {'yes' if host_pid_active() else 'no (container-scoped)'}",
    ]
    return {
        "output": "\n".join(rows),
        "load_average": list(load) if load else None,
        "load_per_cpu": round(load[0] / cpu_count, 2) if load else None,
        "cpu_count": cpu_count,
        "cpu_percent": percent,
        "memory_total_kb": total_kb if has_memory else None,
        "memory_available_kb": available_kb if has_memory else None,
        "memory_used_kb": used_kb if has_memory else None,
        "memory_used_percent": _pct(used_kb, total_kb, raw=True) if has_memory else None,
        "uptime_seconds": uptime,
        "host_pid_namespace": host_pid_active(),
    }


def _fmt_load(load: tuple[float, float, float] | None) -> str:
    if load is None:
        return "n/a"
    return f"{load[0]:.2f} {load[1]:.2f} {load[2]:.2f}"


def _fmt_uptime(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    hours, remainder = divmod(int(seconds), 3600)
    minutes = remainder // 60
    return f"{hours}h {minutes}m"


def _pct(part: int, whole: int, *, raw: bool = False):
    if not whole:
        return None if raw else "n/a"
    value = round(part / whole * 100, 1)
    return value if raw else f"{value}%"


def _run_dmesg(binary: str, args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Cannot read the kernel ring buffer: {exc}") from exc


def _is_usage_error(stderr: str) -> bool:
    lowered = (stderr or "").lower()
    return any(marker in lowered for marker in _USAGE_ERROR_MARKERS)


def _is_permission_error(stderr: str) -> bool:
    lowered = (stderr or "").lower()
    return any(marker in lowered for marker in _PERMISSION_ERROR_MARKERS)


def dmesg(arguments: dict[str, Any]) -> dict[str, Any]:
    binary = shutil.which("dmesg")
    if binary is None:
        raise RuntimeError("dmesg is not installed in this container")
    lines = bounded_int(arguments.get("lines"), DEFAULT_DMESG_LINES, MAX_DMESG_LINES)
    # --ctime is a util-linux extension; busybox dmesg and other minimal
    # implementations reject it, so retry without it on a usage error instead
    # of misreporting an unsupported flag as an access problem.
    completed = _run_dmesg(binary, ["--ctime"])
    if completed.returncode != 0 and _is_usage_error(completed.stderr):
        completed = _run_dmesg(binary, [])
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip() or "unknown error"
        if _is_permission_error(detail):
            raise RuntimeError(f"dmesg failed (kernel.dmesg_restrict?): {detail}")
        raise RuntimeError(f"dmesg failed: {detail}")
    all_lines = completed.stdout.splitlines()
    selected = all_lines[-lines:]
    return {
        "output": "\n".join(selected) or "(kernel ring buffer is empty)",
        "entries": selected,
        "count": len(selected),
        "truncated": len(all_lines) > len(selected),
    }


def network_connections(arguments: dict[str, Any]) -> dict[str, Any]:
    binary = shutil.which("ss")
    if binary is None:
        raise RuntimeError("ss is not installed in this container")
    limit = bounded_int(
        arguments.get("limit"), DEFAULT_CONNECTION_LIMIT, MAX_CONNECTION_LIMIT
    )
    try:
        completed = subprocess.run(
            [binary, "-H", "-tuna"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Cannot list network connections: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip() or "unknown error"
        raise RuntimeError(f"ss failed: {detail}")
    raw = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    selected = raw[:limit]
    return {
        "output": "\n".join(selected) or "No active connections",
        "connections": selected,
        "count": len(selected),
        "truncated": len(raw) > len(selected),
    }
