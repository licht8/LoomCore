"""systemd service inspection and control.

The hub runs without `pid: host`, so systemd is frequently unavailable inside the
container. Every entry point returns an actionable error instead of failing with a
raw `systemctl: command not found`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import audit_event

SYSTEMD_MARKER = Path("/run/systemd/system")

# A restart runs the unit's ExecStop/ExecStart hooks, so it legitimately takes
# longer than the inspection calls; give it a budget of its own.
_RESTART_TIMEOUT = 60

# systemd unit names: letters, digits, and the separators systemd itself uses
# (``.``, ``-``, ``_``, ``@``). Anything else is rejected, so a value like
# ``--all`` can never be passed through as a systemctl option.
_SERVICE_RE = re.compile(r"^[A-Za-z0-9_.@-]+$")


def _require_systemd() -> str:
    binary = shutil.which("systemctl")
    if binary is None or not SYSTEMD_MARKER.exists():
        raise RuntimeError(
            "systemd is not available in this container; manage host services "
            "directly or grant systemd access to the plugin"
        )
    return binary


def _service(arguments: dict[str, Any]) -> str:
    service = str(arguments.get("service") or "").strip()
    if not service or any(char.isspace() for char in service):
        raise ValueError("service must be a non-empty name without spaces")
    if service.startswith("-"):
        raise ValueError("service must be a unit name, not an option")
    if not _SERVICE_RE.fullmatch(service):
        raise ValueError("service contains invalid characters")
    return service


def _systemctl(binary: str, *args: str, timeout: int = 15) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"systemctl {' '.join(args)} timed out") from exc
    except OSError as exc:
        raise RuntimeError(f"Cannot run systemctl: {exc}") from exc


def systemctl_status(arguments: dict[str, Any]) -> dict[str, Any]:
    binary = _require_systemd()
    service = _service(arguments)
    active = _systemctl(binary, "is-active", service)
    status = _systemctl(binary, "status", "--no-pager", "--lines=20", service)
    state = (active.stdout or "").strip() or "unknown"
    output = status.stdout.strip() or status.stderr.strip()
    return {
        "output": output or f"{service}: {state}",
        "service": service,
        "active": state == "active",
        "state": state,
    }


def failed_units() -> list[str]:
    """Return the names of failed systemd units (empty when none)."""
    binary = _require_systemd()
    completed = _systemctl(binary, "--failed", "--no-legend", "--plain")
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        raise RuntimeError(f"Cannot list failed units: {detail}")
    units: list[str] = []
    for line in (completed.stdout or "").splitlines():
        stripped = line.strip()
        if stripped:
            units.append(stripped.split()[0])
    return units


def failed_units_handler(arguments: dict[str, Any]) -> dict[str, Any]:
    units = failed_units()
    lines = [f"{unit}" for unit in units] or ["No failed systemd units"]
    return {"output": "\n".join(lines), "units": units, "count": len(units)}


def systemctl_restart(arguments: dict[str, Any]) -> dict[str, Any]:
    binary = _require_systemd()
    service = _service(arguments)
    try:
        completed = _systemctl(binary, "restart", service, timeout=_RESTART_TIMEOUT)
    except RuntimeError as exc:
        audit_event(
            "ops_systemctl_restart", service=service, exit_code=None, error=str(exc)
        )
        raise
    audit_error = audit_event(
        "ops_systemctl_restart", service=service, exit_code=completed.returncode
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        raise RuntimeError(f"Failed to restart {service}: {detail}")
    return {
        "output": f"Restarted {service}",
        "service": service,
        "exit_code": 0,
        "audit_error": audit_error,
    }
