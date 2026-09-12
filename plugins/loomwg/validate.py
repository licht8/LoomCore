"""Validate WireGuard configuration without starting the server.

Structural checks (sections, keys, CIDRs, duplicates, overlaps) plus an
optional `wg-quick strip` parse check when validating a real config file.
"""

from __future__ import annotations

import re
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Any

from keys import validate_public_key
from results import text_result
from wireguard import CommandError, config_directory, run_command, validate_interface

_SECTION_RE = re.compile(r"^\[(.+)\]$")
_ENDPOINT_RE = re.compile(r"^(\[[0-9A-Fa-f:]+\]|[^\s:]+):(\d{1,5})$")


def _parse(text: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _SECTION_RE.match(line)
        if match:
            current = {"name": match.group(1).strip(), "entries": []}
            sections.append(current)
            continue
        if current is None:
            continue
        key, separator, value = line.partition("=")
        if separator:
            current["entries"].append((key.strip(), value.strip()))
        else:
            current["entries"].append((line, None))
    return sections


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


def _entries_by_key(section: dict[str, Any]) -> dict[str, str]:
    return {key.lower(): value for key, value in section["entries"] if value is not None}


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

    sections = _parse(text)
    interface_sections = [s for s in sections if s["name"].lower() == "interface"]
    peer_sections = [s for s in sections if s["name"].lower() == "peer"]

    if not interface_sections:
        errors.append("[Interface] section missing")
    if len(interface_sections) > 1:
        errors.append("multiple [Interface] sections")
    if sections and sections[0]["name"].lower() != "interface":
        warnings.append("[Interface] is not the first section")

    for section in interface_sections:
        keys = _entries_by_key(section)
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
        keys = _entries_by_key(section)
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
