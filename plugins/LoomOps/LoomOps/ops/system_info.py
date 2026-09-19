"""Read-only system information: disk usage and network status."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

PROC_NET_DEV = Path("/proc/net/dev")

logger = logging.getLogger(__name__)


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
        # The field-count check does not guarantee the counters are numeric
        # (/proc lines can be partial); skip a malformed line instead of
        # aborting the whole scan.
        try:
            receive_bytes = int(fields[0])
            transmit_bytes = int(fields[8])
        except ValueError:
            continue
        interfaces.append(
            {
                "name": name,
                "addresses": [],
                "receive_bytes": receive_bytes,
                "transmit_bytes": transmit_bytes,
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
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        # Distinguish a broken address lookup from a host that has no addresses.
        logger.warning("Failed to read IP addresses via `ip -j addr`: %s", exc)
        return {}
    if not isinstance(data, list):
        logger.warning("`ip -j addr` returned %s, expected a list", type(data).__name__)
        return {}
    addresses: dict[str, list[str]] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("ifname") or "")
        addr_info = entry.get("addr_info") or []
        if not isinstance(addr_info, list):
            logger.warning(
                "`ip -j addr` returned %s addr_info for %s, expected a list",
                type(addr_info).__name__,
                name,
            )
            addr_info = []
        if name:
            addresses[name] = [
                str(item["local"])
                for item in addr_info
                if isinstance(item, dict) and item.get("local")
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
