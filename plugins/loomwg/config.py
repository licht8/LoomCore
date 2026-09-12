"""Safe persistence of WireGuard server peer blocks."""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

from keys import validate_public_key
from wireguard import config_directory


def config_path(interface: str) -> Path:
    return config_directory() / f"{interface}.conf"


def iter_peers(interface: str) -> list[dict]:
    """Return parsed peer blocks from the server config."""
    path = config_path(interface)
    if not path.is_file():
        return []
    peers: list[dict] = []
    current: dict | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if current is not None:
                peers.append(current)
            current = (
                {"public_key": "", "allowed_ips": [], "preshared_key": None}
                if stripped.lower() == "[peer]"
                else None
            )
            continue
        if current is None:
            continue
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        value = value.strip()
        if key == "PublicKey":
            current["public_key"] = value
        elif key == "AllowedIPs":
            current["allowed_ips"] = [
                item.strip() for item in value.split(",") if item.strip()
            ]
        elif key == "PresharedKey":
            current["preshared_key"] = value
    if current is not None:
        peers.append(current)
    return [peer for peer in peers if peer["public_key"]]


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content.rstrip() + "\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def _peer_block(public_key: str, allowed_ips: list[str], preshared_key: str | None) -> str:
    lines = ["[Peer]", f"PublicKey = {public_key}", f"AllowedIPs = {', '.join(allowed_ips)}"]
    if preshared_key:
        lines.append(f"PresharedKey = {preshared_key}")
    return "\n".join(lines)


def add_peer(
    interface: str,
    public_key: str,
    allowed_ips: list[str],
    preshared_key: str | None = None,
) -> None:
    path = config_path(interface)
    if not path.is_file():
        raise FileNotFoundError(f"Configuration {path} not found")
    content = path.read_text(encoding="utf-8")
    validate_public_key(public_key)
    if f"PublicKey = {public_key}" in content:
        raise ValueError("A peer with this public key already exists")
    updated = content.rstrip() + "\n\n" + _peer_block(public_key, allowed_ips, preshared_key) + "\n"
    _write(path, updated)


def _blocks(content: str) -> list[list[str]]:
    """Split config text into section blocks (starting at each [Section])."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if current:
                blocks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append(current)
    return blocks


def upsert_peer(
    interface: str,
    public_key: str,
    allowed_ips: list[str],
    preshared_key: str | None = None,
) -> bool:
    """Add or update a peer block. Returns True if the config changed."""
    path = config_path(interface)
    if not path.is_file():
        raise FileNotFoundError(f"Configuration {path} not found")
    validate_public_key(public_key)

    blocks = _blocks(path.read_text(encoding="utf-8"))
    found = False
    updated_blocks: list[list[str]] = []
    for block in blocks:
        is_peer = block and block[0].strip().lower() == "[peer]"
        is_target = is_peer and any(
            line.strip() == f"PublicKey = {public_key}" for line in block
        )
        if not is_target:
            updated_blocks.append(block)
            continue

        found = True
        current_allowed: list[str] = []
        current_psk: str | None = None
        for line in block[1:]:
            key, separator, value = line.partition("=")
            if not separator:
                continue
            key = key.strip().lower()
            if key == "allowedips":
                current_allowed = [item.strip() for item in value.split(",") if item.strip()]
            elif key == "presharedkey":
                current_psk = value.strip()

        if current_allowed == allowed_ips and (
            preshared_key is None or preshared_key == current_psk
        ):
            return False

        effective_psk = preshared_key if preshared_key is not None else current_psk
        updated_blocks.append(_peer_block(public_key, allowed_ips, effective_psk).splitlines())

    if not found:
        updated_blocks.append(_peer_block(public_key, allowed_ips, preshared_key).splitlines())

    _write(path, "\n\n".join("\n".join(block) for block in updated_blocks))
    return True


def remove_peer(interface: str, public_key: str) -> None:
    path = config_path(interface)
    if not path.is_file():
        raise FileNotFoundError(f"Configuration {path} not found")
    lines = path.read_text(encoding="utf-8").splitlines()
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines + ["[end]"]:
        if line.strip() == "[Peer]" or line.strip().startswith("[") and line.strip() != "[end]":
            if current:
                blocks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append(current)
    retained = []
    removed = False
    for block in blocks:
        is_target = any(line.strip() == f"PublicKey = {public_key}" for line in block)
        if is_target:
            removed = True
        else:
            retained.extend(block)
    if not removed:
        raise ValueError("No peer with this public key found in the configuration")
    _write(path, "\n".join(retained))
