"""Safe persistence of WireGuard server peer blocks."""

from __future__ import annotations

from pathlib import Path

from ..util.files import atomic_write_text
from ..wg.config_parser import sections_named
from ..wg.device import config_directory
from ..wg.keys import validate_public_key
from ..wg.validate import validate_config_text


def config_path(interface: str) -> Path:
    return config_directory() / f"{interface}.conf"


def iter_peers(interface: str) -> list[dict]:
    """Return parsed peer blocks from the server config."""
    path = config_path(interface)
    if not path.is_file():
        return []
    peers: list[dict] = []
    for section in sections_named(path.read_text(encoding="utf-8"), "peer"):
        peer = {"public_key": "", "allowed_ips": [], "preshared_key": None}
        for key, value in section.entries:
            if value is None:
                continue
            name = key.strip().lower()
            if name == "publickey":
                peer["public_key"] = value
            elif name == "allowedips":
                peer["allowed_ips"] = [
                    item.strip() for item in value.split(",") if item.strip()
                ]
            elif name == "presharedkey":
                peer["preshared_key"] = value
        peers.append(peer)
    return [peer for peer in peers if peer["public_key"]]


def _write(path: Path, content: str) -> None:
    text = content.rstrip() + "\n"
    validate_config_text(text)
    atomic_write_text(path, text, mode=0o600)


def _peer_public_key(block: list[str]) -> str | None:
    """Return the active PublicKey of a ``[Peer]`` block, or None.

    Only the first non-comment ``PublicKey`` entry is considered, so the same
    line in another section (or a commented-out one) is never mistaken for a
    peer, and spacing variants such as ``PublicKey=<key>`` are recognized.
    """
    if not block or block[0].strip().lower() != "[peer]":
        return None
    for line in block[1:]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition("=")
        if separator and key.strip().lower() == "publickey":
            return value.strip()
    return None


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
    if any(_peer_public_key(block) == public_key for block in _blocks(content)):
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
        if _peer_public_key(block) != public_key:
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

        if sorted(current_allowed) == sorted(allowed_ips) and (
            preshared_key is None or preshared_key == current_psk
        ):
            return False

        effective_psk = preshared_key if preshared_key is not None else current_psk
        updated_blocks.append(_peer_block(public_key, allowed_ips, effective_psk).splitlines())

    if not found:
        updated_blocks.append(_peer_block(public_key, allowed_ips, preshared_key).splitlines())

    _write(
        path,
        "\n\n".join("\n".join(block).strip() for block in updated_blocks),
    )
    return True


def remove_peer(interface: str, public_key: str) -> None:
    validate_public_key(public_key)
    path = config_path(interface)
    if not path.is_file():
        raise FileNotFoundError(f"Configuration {path} not found")
    blocks = _blocks(path.read_text(encoding="utf-8"))
    retained = []
    removed = False
    for block in blocks:
        if _peer_public_key(block) == public_key:
            removed = True
        else:
            retained.extend(block)
    if not removed:
        raise ValueError("No peer with this public key found in the configuration")
    _write(path, "\n".join(retained))
