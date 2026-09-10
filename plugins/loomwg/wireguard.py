"""Read-only WireGuard operations backed by host command-line tools."""

from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


INTERFACE_RE = re.compile(r"^[A-Za-z0-9]{1,15}$")
CONFIG_DIR = Path("/etc/wireguard")
HOST_CONFIG_DIR = Path("/host/etc/wireguard")


class CommandError(RuntimeError):
    """Raised when a WireGuard host command fails."""


def run_command(
    command: list[str],
    timeout: int = 15,
    input_text: str | None = None,
) -> str:
    host_command = command
    if command and command[0] in {"wg", "ip"}:
        host_command = ["nsenter", "-t", "1", "-n", "--", *command]
    try:
        result = subprocess.run(
            host_command,
            capture_output=True,
            text=True,
            input=input_text,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CommandError(f"Не удалось выполнить {' '.join(host_command)}: {exc}") from exc

    if result.returncode != 0:
        if host_command != command:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                input=input_text,
                timeout=timeout,
                check=False,
            )
        if result.returncode == 0:
            return result.stdout.strip()
        details = result.stderr.strip() or result.stdout.strip() or "нет деталей"
        raise CommandError(
            f"Команда {' '.join(host_command)} завершилась с кодом "
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
    config_dir = CONFIG_DIR if CONFIG_DIR.is_dir() and any(CONFIG_DIR.glob("*.conf")) else HOST_CONFIG_DIR
    if not config_dir.is_dir():
        return []
    return sorted(
        path.stem
        for path in config_dir.glob("*.conf")
        if INTERFACE_RE.fullmatch(path.stem)
    )


def config_directory() -> Path:
    if CONFIG_DIR.is_dir() and any(CONFIG_DIR.glob("*.conf")):
        return CONFIG_DIR
    return HOST_CONFIG_DIR if HOST_CONFIG_DIR.is_dir() else CONFIG_DIR


def _parse_timestamp(value: str) -> str | None:
    if not value or value == "0":
        return None
    try:
        return datetime.fromtimestamp(
            int(value), tz=timezone.utc
        ).isoformat()
    except ValueError:
        return None


def read_interface(interface: str) -> dict[str, Any]:
    lines = run_command(["wg", "show", interface, "dump"]).splitlines()
    if not lines:
        raise CommandError(f"Интерфейс {interface} не вернул данные")

    interface_parts = lines[0].split("\t")
    if len(interface_parts) < 3:
        raise CommandError(f"Неожиданный формат вывода wg для {interface}")

    peers: list[dict[str, Any]] = []
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) < 9:
            continue
        peers.append(
            {
                "public_key": parts[0],
                "endpoint": None if parts[2] == "(none)" else parts[2],
                "allowed_ips": parts[3].split(",") if parts[3] else [],
                "latest_handshake": _parse_timestamp(parts[4]),
                "receive_bytes": int(parts[5]),
                "transmit_bytes": int(parts[6]),
                "persistent_keepalive": int(parts[8]),
            }
        )

    return {
        "interface": interface,
        "public_key": interface_parts[1],
        "listen_port": int(interface_parts[2]),
        "peers": peers,
    }


def peer_is_online(peer: dict[str, Any], timeout_seconds: int = 180) -> bool:
    handshake = peer["latest_handshake"]
    if not handshake:
        return False
    timestamp = datetime.fromisoformat(handshake).timestamp()
    return datetime.now(tz=timezone.utc).timestamp() - timestamp < timeout_seconds


def has_link(interface: str) -> bool:
    try:
        run_command(["ip", "link", "show", interface])
    except CommandError:
        return False
    return True
