"""Read-only WireGuard operations backed by host command-line tools."""

from __future__ import annotations

import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

INTERFACE_RE = re.compile(r"^[A-Za-z0-9]{1,15}$")
CONFIG_DIR = Path(os.environ.get("LOOMWG_CONFIG_DIR", "/etc/wireguard"))


class CommandError(RuntimeError):
    """Raised when a WireGuard host command fails."""


def run_command(
    command: list[str],
    timeout: int = 15,
    input_text: str | None = None,
) -> str:
    # Выполняем напрямую — контейнер имеет NET_ADMIN capability
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            input=input_text,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CommandError(f"Не удалось выполнить {' '.join(command)}: {exc}") from exc

    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip() or "нет деталей"
        raise CommandError(
            f"Команда {' '.join(command)} завершилась с кодом "
            f"{result.returncode}: {details}"
        )
    return result.stdout.strip()


def validate_interface(arguments: dict[str, Any]) -> str:
    interface = str(arguments.get("interface", "wg0"))
    if not INTERFACE_RE.fullmatch(interface):
        raise ValueError("Некорректное имя WireGuard-интерфейса")
    return interface


def active_interfaces() -> list[str]:
    output = run_command(["wg", "show", "interfaces"])
    return output.split() if output else []


def configured_interfaces() -> list[str]:
    if not CONFIG_DIR.is_dir():
        return []
    return sorted(
        path.stem
        for path in CONFIG_DIR.glob("*.conf")
        if INTERFACE_RE.fullmatch(path.stem)
    )


def config_directory() -> Path:
    return CONFIG_DIR


def _parse_timestamp(value: str) -> str | None:
    if not value or value == "0":
        return None
    try:
        return datetime.fromtimestamp(
            int(value), tz=UTC
        ).isoformat()
    except ValueError:
        return None


def read_interface(interface: str) -> dict[str, Any]:
    dump_output = run_command(["wg", "show", interface, "dump"])
    lines = dump_output.splitlines()
    if not lines:
        raise CommandError(f"Интерфейс {interface} не вернул данные")

    interface_parts = lines[0].split()
    if len(interface_parts) < 3:
        raise CommandError(f"Неожиданный формат вывода wg для {interface}")

    peers: list[dict[str, Any]] = []
    for line in lines[1:]:
        # `wg show dump` normally uses tabs, but some wrappers normalize
        # whitespace before returning command output.
        parts = line.split()
        if len(parts) < 8:
            continue
        peers.append(
            {
                "public_key": parts[0],
                "endpoint": None if parts[2] == "(none)" else parts[2],
                "allowed_ips": parts[3].split(",") if parts[3] else [],
                "latest_handshake": _parse_timestamp(parts[4]),
                "receive_bytes": int(parts[5]),
                "transmit_bytes": int(parts[6]),
                "persistent_keepalive": int(parts[7]),
            }
        )

    if not peers:
        peers = _read_human_peers(interface)

    return {
        "interface": interface,
        "public_key": interface_parts[1],
        "listen_port": int(interface_parts[2]),
        "peers": peers,
    }


def _read_human_peers(interface: str) -> list[dict[str, Any]]:
    """Parse peer blocks when a platform does not expose dump rows reliably."""
    lines = run_command(["wg", "show", interface]).splitlines()
    peers: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("peer: "):
            if current is not None:
                peers.append(current)
            current = {
                "public_key": line[6:].strip(),
                "endpoint": None,
                "allowed_ips": [],
                "latest_handshake": None,
                "receive_bytes": 0,
                "transmit_bytes": 0,
                "persistent_keepalive": 0,
            }
        elif current is None:
            continue
        elif line.startswith("endpoint: "):
            endpoint = line[10:].strip()
            current["endpoint"] = None if endpoint == "(none)" else endpoint
        elif line.startswith("allowed ips: "):
            current["allowed_ips"] = [
                value.strip() for value in line[13:].split(",") if value.strip()
            ]
        elif line.startswith("latest handshake: "):
            # Human-readable durations are not compatible with the ISO
            # timestamp format used by peer_is_online().
            current["latest_handshake"] = None
        elif line.startswith("transfer: "):
            values = line[10:].split(",")
            if len(values) == 2:
                current["receive_bytes"] = _parse_transfer_bytes(values[0])
                current["transmit_bytes"] = _parse_transfer_bytes(values[1])
        elif line.startswith("persistent keepalive: "):
            value = line[22:].strip()
            current["persistent_keepalive"] = (
                int(value.split()[0]) if value and value.split()[0].isdigit() else 0
            )
    if current is not None:
        peers.append(current)
    return peers


def _parse_transfer_bytes(value: str) -> int:
    match = re.search(r"([\d.]+)\s*([KMGTP]i?B)?", value)
    if not match:
        return 0
    amount = float(match.group(1))
    units = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}
    return int(amount * units.get(match.group(2) or "B", 1))


def peer_is_online(peer: dict[str, Any], timeout_seconds: int = 180) -> bool:
    handshake = peer["latest_handshake"]
    if not handshake:
        return False
    timestamp = datetime.fromisoformat(handshake).timestamp()
    return datetime.now(tz=UTC).timestamp() - timestamp < timeout_seconds


def has_link(interface: str) -> bool:
    try:
        run_command(["ip", "link", "show", interface])
    except CommandError:
        return False
    return True
