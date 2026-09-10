"""Shared context and validation helpers for WireGuard interfaces."""
from __future__ import annotations

import re
from ipaddress import ip_network
from pathlib import Path


DEFAULT_INTERFACE = "wg0"
_selected_interface = DEFAULT_INTERFACE


def validate_interface_name(name: str) -> bool:
    """Return whether a name is safe for WireGuard and systemd operations."""
    return bool(re.fullmatch(r"[A-Za-z0-9]{1,15}", name))


def get_selected_interface() -> str:
    """Return the interface currently selected in the CLI."""
    return _selected_interface


def set_selected_interface(name: str) -> None:
    """Select an interface for subsequent CLI operations."""
    if not validate_interface_name(name):
        raise ValueError("Invalid WireGuard interface name")
    global _selected_interface
    _selected_interface = name


def config_path(interface: str = DEFAULT_INTERFACE) -> Path:
    """Return the isolated persistent config path for an interface."""
    return Path("/etc/wireguard") / f"{interface}.conf"


def configured_interfaces() -> list[str]:
    """Return configured interface names, preserving wg0 as the first default."""
    directory = Path("/etc/wireguard")
    names = {path.stem for path in directory.glob("*.conf")} if directory.exists() else set()
    names.add(DEFAULT_INTERFACE)
    return sorted(name for name in names if validate_interface_name(name))


def networks_overlap(first: str, second: str) -> bool:
    """Return whether two IPv4 or two IPv6 networks overlap."""
    left = ip_network(first, strict=False)
    right = ip_network(second, strict=False)
    return left.version == right.version and left.overlaps(right)
