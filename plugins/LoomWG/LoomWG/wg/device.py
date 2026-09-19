"""Read-only WireGuard operations backed by host command-line tools."""

from __future__ import annotations

import logging
import os
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

INTERFACE_RE = re.compile(r"^[A-Za-z0-9]{1,15}$")
CONFIG_DIR = Path(os.environ.get("LOOMWG_CONFIG_DIR", "/etc/wireguard"))

logger = logging.getLogger("LoomWG.wg")


class CommandError(RuntimeError):
    """Raised when a WireGuard host command fails."""


def run_command(
    command: list[str],
    timeout: int = 15,
    input_text: str | None = None,
    *,
    sensitive: bool = False,
) -> str:
    """Run a host command and return its stdout.

    When ``sensitive`` is true the command's output may contain key material
    (``wg genkey``, ``wg genpsk``, ``wg showconf``); it is never written to the
    logs and is redacted from error details as well.
    """
    # Run directly — the container has the NET_ADMIN capability
    rendered = " ".join(command)
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
        logger.warning("command failed: %s: %s", rendered, exc)
        raise CommandError(f"Failed to run {rendered}: {exc}") from exc

    if result.returncode != 0:
        if sensitive:
            details = "output redacted"
        else:
            details = result.stderr.strip() or result.stdout.strip() or "no details"
        logger.warning("command exited %s: %s: %s", result.returncode, rendered, details)
        raise CommandError(
            f"Command {rendered} exited with code "
            f"{result.returncode}: {details}"
        )
    output = result.stdout.strip()
    if sensitive:
        logger.info("%s -> (output redacted, %d chars)", rendered, len(output))
    else:
        logger.info("%s -> %s", rendered, output[:500] if output else "(empty)")
    return output


def validate_interface(arguments: dict[str, Any]) -> str:
    interface = str(arguments.get("interface", "wg0"))
    if not INTERFACE_RE.fullmatch(interface):
        raise ValueError("Invalid WireGuard interface name")
    return interface


def active_interfaces() -> list[str]:
    output = run_command(["wg", "show", "interfaces"])
    names = output.split() if output else []
    if not names:
        logger.info(
            "`wg show interfaces` reported no active interfaces (raw output=%r)",
            output,
        )
    return names


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


def _parse_int(value: str, description: str) -> int:
    """Parse a numeric ``wg`` output field, raising ``CommandError`` on garbage."""
    try:
        return int(value)
    except ValueError as exc:
        raise CommandError(
            f"Unexpected wg output: {description} is not a number: {value!r}"
        ) from exc


def read_interface(interface: str) -> dict[str, Any]:
    dump_output = run_command(["wg", "show", interface, "dump"], sensitive=True)
    lines = dump_output.splitlines()
    if not lines:
        raise CommandError(f"Interface {interface} returned no data")

    interface_parts = lines[0].split()
    if len(interface_parts) < 3:
        raise CommandError(f"Unexpected wg output format for {interface}")

    peers: list[dict[str, Any]] = []
    unparsed_peers = False
    for line in lines[1:]:
        # `wg show dump` normally uses tabs, but some wrappers normalize
        # whitespace before returning command output.
        parts = line.split()
        if len(parts) < 8:
            unparsed_peers = True
            continue
        allowed_ips = parts[3]
        keepalive = parts[7]
        peers.append(
            {
                "public_key": parts[0],
                "endpoint": None if parts[2] == "(none)" else parts[2],
                "allowed_ips": [] if allowed_ips == "(none)" else allowed_ips.split(","),
                "latest_handshake": _parse_timestamp(parts[4]),
                "receive_bytes": _parse_int(parts[5], "receive bytes"),
                "transmit_bytes": _parse_int(parts[6], "transmit bytes"),
                # wg prints "off" for the keepalive interval when it is unset.
                "persistent_keepalive": int(keepalive) if keepalive.isdigit() else 0,
            }
        )

    # Some platforms/wrappers emit dump rows that cannot be split; only then do
    # we pay for a second `wg show` call. An interface with no peers at all is
    # a legitimate result and must not trigger the fallback.
    if not peers and unparsed_peers:
        peers = _read_human_peers(interface)

    return {
        "interface": interface,
        "public_key": interface_parts[1],
        "listen_port": _parse_int(interface_parts[2], "listen port"),
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
            # Convert the human-readable age ("1 minute, 21 seconds ago")
            # back into an ISO timestamp peer_is_online() can compare.
            current["latest_handshake"] = _parse_human_age(line[18:].strip())
        elif line.startswith("transfer: "):
            values = line[10:].split(",")
            if len(values) == 2:
                current["receive_bytes"] = _parse_transfer_bytes(values[0])
                current["transmit_bytes"] = _parse_transfer_bytes(values[1])
        elif line.startswith("persistent keepalive: "):
            # Human output reads "every 25 seconds"; "off" means disabled.
            value = line[22:].strip()
            match = re.search(r"(\d+)", value)
            current["persistent_keepalive"] = int(match.group(1)) if match else 0
    if current is not None:
        peers.append(current)
    return peers


_TRANSFER_UNITS = {
    "b": 1,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
    "pib": 1024**5,
}

_HUMAN_AGE_UNITS = {
    "year": 365 * 24 * 3600,
    "day": 24 * 3600,
    "hour": 3600,
    "minute": 60,
    "second": 1,
}


def _parse_transfer_bytes(value: str) -> int:
    match = re.search(r"([\d.]+)\s*([A-Za-z]*)", value)
    if not match:
        return 0
    unit = match.group(2).lower()
    if unit:
        factor = _TRANSFER_UNITS.get(unit)
        if factor is None:
            # An unrecognised unit must not be silently read as raw bytes.
            return 0
    else:
        factor = 1
    return int(float(match.group(1)) * factor)


def _parse_human_age(value: str) -> str | None:
    """Convert a ``wg show`` handshake age ("1 minute, 21 seconds ago") to ISO."""
    total_seconds = 0
    matched = False
    for amount, unit in re.findall(r"(\d+)\s*([A-Za-z]+)", value):
        factor = _HUMAN_AGE_UNITS.get(unit.rstrip("s").lower())
        if factor is None:
            return None
        total_seconds += int(amount) * factor
        matched = True
    if not matched:
        return None
    return (datetime.now(tz=UTC) - timedelta(seconds=total_seconds)).isoformat()


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
