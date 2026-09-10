"""Safe persistence of WireGuard server peer blocks."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from keys import validate_public_key
from wireguard import CONFIG_DIR


def config_path(interface: str) -> Path:
    return CONFIG_DIR / f"{interface}.conf"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content.rstrip() + "\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
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
        raise FileNotFoundError(f"Конфигурация {path} не найдена")
    content = path.read_text(encoding="utf-8")
    validate_public_key(public_key)
    if f"PublicKey = {public_key}" in content:
        raise ValueError("Пир с таким public key уже существует")
    updated = content.rstrip() + "\n\n" + _peer_block(public_key, allowed_ips, preshared_key) + "\n"
    _write(path, updated)


def remove_peer(interface: str, public_key: str) -> None:
    path = config_path(interface)
    if not path.is_file():
        raise FileNotFoundError(f"Конфигурация {path} не найдена")
    lines = path.read_text(encoding="utf-8").splitlines()
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines + ["[end]"]:
        if line.strip() == "[Peer]":
            if current:
                blocks.append(current)
            current = [line]
        elif line.strip().startswith("[") and line.strip() != "[end]":
            if current:
                blocks.append(current)
            current = [line]
        else:
            current.append(line)
    retained = []
    removed = False
    for block in blocks:
        is_target = any(line.strip() == f"PublicKey = {public_key}" for line in block)
        if is_target:
            removed = True
        else:
            retained.extend(block)
    if not removed:
        raise ValueError("Пир с таким public key не найден в конфигурации")
    _write(path, "\n".join(retained))
