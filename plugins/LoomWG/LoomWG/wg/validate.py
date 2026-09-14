"""Validate WireGuard configuration without starting the server.

Structural checks (sections, keys, CIDRs, duplicates, overlaps) plus an
optional `wg-quick strip` parse check when validating a real config file.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Any

from ..results import text_result
from .config_parser import entries_map, parse_sections
from .device import CommandError, config_directory, run_command, validate_interface
from .keys import validate_public_key

_ENDPOINT_RE = re.compile(r"^(\[[0-9A-Fa-f:]+\]|[^\s:]+):(\d{1,5})$")


def _check_key(value: str, label: str, errors: list[str]) -> None:
    try:
        validate_public_key(value)
    except ValueError:
        errors.append(f"{label}: invalid WireGuard key")


def _check_cidrs(values: list[str], label: str, errors: list[str]) -> list:
    networks = []
    for value in values:
        try:
            networks.append(ip_network(value, strict=False))
        except ValueError:
            errors.append(f"{label}: invalid CIDR '{value}'")
    return networks


def validate_config_text(text: str) -> None:
    """Raise CommandError if ``wg-quick strip`` cannot parse the config text."""
    descriptor, tmp_name = tempfile.mkstemp(suffix=".conf")
    tmp_path = Path(tmp_name)
    try:
        os.chmod(tmp_path, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(text)
        run_command(["wg-quick", "strip", str(tmp_path)])
    finally:
        with contextlib.suppress(OSError):
            tmp_path.unlink()


def check_config(arguments: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    interface_name: str | None = None

    if arguments.get("content"):
        text = str(arguments["content"])
        source = "content"
    elif arguments.get("path"):
        path = Path(str(arguments["path"]))
        source = str(path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"cannot read {path}: {exc}")
            return text_result(
                f"Config check: INVALID ({source})\n  ERROR: {errors[-1]}",
                valid=False,
                source=source,
                errors=errors,
                warnings=warnings,
            )
    else:
        interface_name = validate_interface(arguments)
        path = config_directory() / f"{interface_name}.conf"
        source = str(path)
        if not path.is_file():
            return text_result(
                f"Config check: INVALID ({source})\n  ERROR: configuration not found",
                valid=False,
                source=source,
                errors=["configuration not found"],
                warnings=warnings,
            )
        text = path.read_text(encoding="utf-8")

    sections = parse_sections(text)
    interface_sections = [s for s in sections if s.name.lower() == "interface"]
    peer_sections = [s for s in sections if s.name.lower() == "peer"]

    for section in sections:
        if section.name.lower() not in {"interface", "peer"}:
            errors.append(f"unknown section [{section.name}]")

    if not interface_sections:
        errors.append("[Interface] section missing")
    if len(interface_sections) > 1:
        errors.append("multiple [Interface] sections")
    if sections and sections[0].name.lower() != "interface":
        warnings.append("[Interface] is not the first section")

    for section in interface_sections:
        keys = entries_map(section)
        if "privatekey" not in keys:
            errors.append("[Interface] PrivateKey missing")
        else:
            _check_key(keys["privatekey"], "[Interface] PrivateKey", errors)
        if "address" not in keys:
            errors.append("[Interface] Address missing")
        else:
            _check_cidrs([a.strip() for a in keys["address"].split(",")], "[Interface] Address", errors)
        if "listenport" in keys:
            try:
                if not 1 <= int(keys["listenport"]) <= 65535:
                    errors.append("[Interface] ListenPort out of range")
            except ValueError:
                errors.append("[Interface] ListenPort is not an integer")
        if "dns" in keys:
            for dns in keys["dns"].split(","):
                try:
                    ip_address(dns.strip())
                except ValueError:
                    warnings.append(f"[Interface] DNS '{dns.strip()}' is not an IP address")
        if "postup" not in keys and "postdown" not in keys:
            warnings.append("no PostUp/PostDown (NAT/forwarding may be missing)")

    seen_peers: set[str] = set()
    peer_allowed: list[tuple[str, Any]] = []
    for index, section in enumerate(peer_sections, start=1):
        keys = entries_map(section)
        public = keys.get("publickey")
        if not public:
            errors.append(f"[Peer #{index}] PublicKey missing")
        else:
            _check_key(public, f"[Peer #{index}] PublicKey", errors)
            if public in seen_peers:
                errors.append(f"[Peer #{index}] duplicate PublicKey")
            seen_peers.add(public)
        if "presharedkey" in keys:
            _check_key(keys["presharedkey"], f"[Peer #{index}] PresharedKey", errors)
        allowed = keys.get("allowedips")
        if not allowed:
            errors.append(f"[Peer #{index}] AllowedIPs missing")
        else:
            for network in _check_cidrs(
                [a.strip() for a in allowed.split(",")], f"[Peer #{index}] AllowedIPs", errors
            ):
                peer_allowed.append((public or f"#{index}", network))
        endpoint = keys.get("endpoint")
        if endpoint and not _ENDPOINT_RE.match(endpoint):
            errors.append(f"[Peer #{index}] invalid Endpoint '{endpoint}'")
        keepalive = keys.get("persistentkeepalive")
        if keepalive and keepalive.lower() != "off":
            try:
                int(keepalive)
            except ValueError:
                errors.append(f"[Peer #{index}] invalid PersistentKeepalive")

    for i in range(len(peer_allowed)):
        for j in range(i + 1, len(peer_allowed)):
            owner_a, network_a = peer_allowed[i]
            owner_b, network_b = peer_allowed[j]
            if owner_a != owner_b and network_a.overlaps(network_b):
                warnings.append(
                    f"AllowedIPs overlap between {owner_a} and {owner_b}: "
                    f"{network_a} <-> {network_b}"
                )

    if interface_name:
        try:
            run_command(["wg-quick", "strip", interface_name])
        except CommandError as exc:
            errors.append(f"wg-quick strip failed: {exc}")

    valid = not errors
    lines = [f"Config check: {'VALID' if valid else 'INVALID'} ({source})"]
    lines.extend(f"  ERROR: {error}" for error in errors)
    lines.extend(f"  WARN: {warning}" for warning in warnings)
    return text_result(
        "\n".join(lines),
        valid=valid,
        source=source,
        errors=errors,
        warnings=warnings,
    )
