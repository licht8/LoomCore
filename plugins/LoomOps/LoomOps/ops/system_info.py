"""Read-only system information: disk usage and network status."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

PROC_NET_DEV = Path("/proc/net/dev")


def _human(size: float) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if value < 1024 or unit == "PiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PiB"


def disk_usage(arguments: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(arguments.get("path") or "/")).expanduser()
    if not path.exists():
        raise ValueError(f"Path not found: {path}")
    try:
        usage = shutil.disk_usage(path)
    except OSError as exc:
        raise RuntimeError(f"Cannot read disk usage for {path}: {exc}") from exc
    total = usage.total or 1
    lines = [
        f"Path: {path}",
        f"Total: {_human(usage.total)}",
        f"Used:  {_human(usage.used)} ({usage.used / total * 100:.1f}%)",
        f"Free:  {_human(usage.free)}",
    ]
    return {
        "output": "\n".join(lines),
        "path": str(path),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
    }


def _proc_interfaces() -> list[dict[str, Any]]:
    path = PROC_NET_DEV
    if not path.is_file():
        return []
    interfaces: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[2:]:
        name, _, counters = line.partition(":")
        name = name.strip()
        fields = counters.split()
        if not name or len(fields) < 16:
            continue
        interfaces.append(
            {
                "name": name,
                "addresses": [],
                "receive_bytes": int(fields[0]),
                "transmit_bytes": int(fields[8]),
            }
        )
    return interfaces


def _ip_addresses() -> dict[str, list[str]]:
    binary = shutil.which("ip")
    if binary is None:
        return {}
    try:
        completed = subprocess.run(
            [binary, "-j", "addr"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            shell=False,
        )
        data = json.loads(completed.stdout or "[]")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {}
    addresses: dict[str, list[str]] = {}
    for entry in data:
        name = str(entry.get("ifname") or "")
        if name:
            addresses[name] = [
                str(item["local"]) for item in entry.get("addr_info", []) if item.get("local")
            ]
    return addresses


def network_status(arguments: dict[str, Any]) -> dict[str, Any]:
    interfaces = _proc_interfaces()
    addresses = _ip_addresses()
    for item in interfaces:
        item["addresses"] = addresses.get(item["name"], [])
    lines = [f"{'INTERFACE':<16} {'RX':>12} {'TX':>12}  ADDRESSES"]
    lines.extend(
        f"{item['name']:<16} {_human(item['receive_bytes']):>12} "
        f"{_human(item['transmit_bytes']):>12}  {', '.join(item['addresses'])}"
        for item in interfaces
    )
    if not interfaces:
        lines.append("No network interfaces found")
    return {
        "output": "\n".join(lines),
        "interfaces": interfaces,
        "count": len(interfaces),
    }
