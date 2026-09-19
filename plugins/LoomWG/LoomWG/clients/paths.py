"""Filesystem layout for generated WireGuard client artifacts."""

from __future__ import annotations

from pathlib import Path

from ..wg.device import config_directory

CLIENTS_DIR = config_directory() / "clients"
CONF_DIR_NAME = "conf"
QR_DIR_NAME = "qr_code"


def clients_directory() -> Path:
    """Return the directory holding all generated client artifacts."""
    return CLIENTS_DIR


def client_paths(name: str) -> tuple[Path, Path]:
    """Return the (config, QR) artifact paths for a client name."""
    directory = clients_directory()
    return (
        directory / CONF_DIR_NAME / f"{name}.conf",
        directory / QR_DIR_NAME / f"{name}.png",
    )


def migrate_client_layout() -> dict[str, int]:
    """Move legacy flat ``clients/*.conf`` and ``*.png`` into the split dirs."""
    directory = clients_directory()
    moved = {"conf": 0, "qr": 0}
    if not directory.is_dir():
        return moved

    conf_dir = directory / CONF_DIR_NAME
    qr_dir = directory / QR_DIR_NAME
    for conf_path in directory.glob("*.conf"):
        conf_dir.mkdir(parents=True, exist_ok=True)
        destination = conf_dir / conf_path.name
        if destination.exists():
            # Never clobber an already-migrated artifact (partial migration,
            # recreated client, restored backup): the newer file wins.
            continue
        conf_path.replace(destination)
        moved["conf"] += 1
    for qr_path in directory.glob("*.png"):
        qr_dir.mkdir(parents=True, exist_ok=True)
        destination = qr_dir / qr_path.name
        if destination.exists():
            continue
        qr_path.replace(destination)
        moved["qr"] += 1
    return moved
