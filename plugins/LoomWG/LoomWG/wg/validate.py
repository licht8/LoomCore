"""Validate WireGuard configuration without starting the server.

Structural checks (sections, keys, CIDRs, duplicates, overlaps) plus an
optional `wg-quick strip` parse check when validating a real config file.
"""

from __future__ import annotations

import contextlib
import errno
import os
import re
import stat
import tempfile
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Any

from ..results import text_result
from .config_parser import Section, entries_values, parse_sections
from .device import CommandError, config_directory, run_command, validate_interface
from .keys import validate_public_key

_ENDPOINT_RE = re.compile(r"^(\[[0-9A-Fa-f:]+\]|[^\s:]+):(\d{1,5})$")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)


def _config_path(raw: str) -> Path:
    """Resolve a caller-supplied config path inside the WireGuard config dir.

    ``wg_check_interface_config`` is a safe (unapproved) tool that echoes config
    fragments back in its errors, so accepting an arbitrary path would let a
    caller read any file the plugin can access. Relative paths are resolved
    against the config directory; anything that escapes it (including through a
    symlink) is rejected.
    """
    base = config_directory().resolve()
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = base / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"configuration not found: {raw}") from exc
    if base not in resolved.parents:
        raise ValueError(
            "path must point to a WireGuard config file in the config directory"
        )
    return resolved


def _read_config_text(path: Path) -> str:
    """Read a confined config file without following a symlink at open time.

    ``_config_path`` resolves the path first, but the file is opened later, so a
    component swapped for a symlink in between could escape the config
    directory. Mirror LoomOps ``ops/confine.py``: lstat every component below the
    config directory, open the leaf with ``O_NOFOLLOW`` (plus ``O_NONBLOCK`` so a
    FIFO cannot stall the serial plugin loop) where supported, and require a
    regular file. This is best effort — a swap after the last lstat and before
    ``open`` cannot be ruled out without ``dir_fd`` support.
    """
    base = config_directory().resolve()
    try:
        relative = path.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"Path '{path}' is outside the config directory") from exc
    current = base
    try:
        for part in relative.parts:
            current = current / part
            if stat.S_ISLNK(os.lstat(current).st_mode):
                raise ValueError(f"Refusing to follow symlink: {current}")
        descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW | _NONBLOCK)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError(f"Refusing to follow symlink: {path}") from exc
        raise
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"Not a regular file: {path}")
        opened = os.lstat(path)
        if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError(f"Refusing to follow symlink: {path}")
    except BaseException:
        os.close(descriptor)
        raise
    try:
        handle = os.fdopen(descriptor, "r", encoding="utf-8")
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(descriptor)
        raise
    with handle:
        return handle.read()


def _check_key(value: str, label: str, errors: list[str]) -> None:
    try:
        validate_public_key(value)
    except ValueError:
        errors.append(f"{label}: invalid WireGuard key")


def _present_values(section: Section, key: str) -> list[str]:
    """Return the non-blank values recorded for ``key``, in order.

    A key present with a blank value (``Key =``) counts as absent, so repeated
    and single-valued keys share the same presence semantics.
    """
    return [value for value in entries_values(section, key) if value.strip()]


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
        with os.fdopen(descriptor, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(text)
        run_command(["wg-quick", "strip", str(tmp_path)], sensitive=True)
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
        path = _config_path(str(arguments["path"]))
        source = str(path)
        try:
            text = _read_config_text(path)
        except (OSError, UnicodeDecodeError) as exc:
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
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            errors.append(f"cannot read {path}: {exc}")
            return text_result(
                f"Config check: INVALID ({source})\n  ERROR: {errors[-1]}",
                valid=False,
                source=source,
                errors=errors,
                warnings=warnings,
            )

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
        private_keys = _present_values(section, "privatekey")
        if not private_keys:
            errors.append("[Interface] PrivateKey missing")
        else:
            for private_key in private_keys:
                _check_key(private_key, "[Interface] PrivateKey", errors)
        addresses = _present_values(section, "address")
        if not addresses:
            errors.append("[Interface] Address missing")
        else:
            _check_cidrs(
                [a.strip() for value in addresses for a in value.split(",")],
                "[Interface] Address",
                errors,
            )
        for listen_port in _present_values(section, "listenport"):
            try:
                if not 1 <= int(listen_port) <= 65535:
                    errors.append("[Interface] ListenPort out of range")
            except ValueError:
                errors.append("[Interface] ListenPort is not an integer")
        for dns_value in _present_values(section, "dns"):
            for dns in dns_value.split(","):
                try:
                    ip_address(dns.strip())
                except ValueError:
                    warnings.append(f"[Interface] DNS '{dns.strip()}' is not an IP address")
        if not _present_values(section, "postup") and not _present_values(section, "postdown"):
            warnings.append("no PostUp/PostDown (NAT/forwarding may be missing)")

    seen_peers: set[str] = set()
    peer_allowed: list[tuple[str, Any]] = []
    for index, section in enumerate(peer_sections, start=1):
        public_keys = _present_values(section, "publickey")
        public = public_keys[-1] if public_keys else None
        if not public_keys:
            errors.append(f"[Peer #{index}] PublicKey missing")
        else:
            for candidate in public_keys:
                _check_key(candidate, f"[Peer #{index}] PublicKey", errors)
            if any(candidate in seen_peers for candidate in public_keys):
                errors.append(f"[Peer #{index}] duplicate PublicKey")
            seen_peers.update(public_keys)
        for preshared_key in _present_values(section, "presharedkey"):
            _check_key(preshared_key, f"[Peer #{index}] PresharedKey", errors)
        allowed_values = _present_values(section, "allowedips")
        if not allowed_values:
            errors.append(f"[Peer #{index}] AllowedIPs missing")
        else:
            for network in _check_cidrs(
                [a.strip() for value in allowed_values for a in value.split(",")],
                f"[Peer #{index}] AllowedIPs",
                errors,
            ):
                peer_allowed.append((public or f"#{index}", network))
        for endpoint in _present_values(section, "endpoint"):
            if not _ENDPOINT_RE.match(endpoint):
                errors.append(f"[Peer #{index}] invalid Endpoint '{endpoint}'")
        for keepalive in _present_values(section, "persistentkeepalive"):
            if keepalive.lower() != "off":
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
            run_command(["wg-quick", "strip", interface_name], sensitive=True)
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
