"""Generate PNG QR codes for WireGuard client configs."""

from __future__ import annotations

import contextlib
import os
import subprocess
from pathlib import Path

from ..wg.device import CommandError


def _discard(qr_path: Path) -> None:
    """Remove a half-written QR code so the next request regenerates it."""
    with contextlib.suppress(OSError):
        qr_path.unlink()


def generate_qr(client_config: str, qr_path: Path) -> None:
    """Render a client config to a PNG QR code (mode 600)."""
    qr_path.parent.mkdir(parents=True, exist_ok=True)
    # A regeneration may run while a valid QR code is already on disk; a
    # failure that happens before qrencode writes anything must not destroy it.
    previous = qr_path.exists()
    try:
        # The PNG embeds the client private key, so create the file with
        # restrictive permissions first: qrencode truncates an existing file,
        # which keeps the mode we set here for the whole write.
        descriptor = os.open(qr_path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(descriptor)
        os.chmod(qr_path, 0o600)
    except OSError as exc:
        if not previous:
            _discard(qr_path)
        raise CommandError(f"Cannot create QR code file {qr_path}: {exc}") from exc
    try:
        qrencode = subprocess.run(
            ["qrencode", "-o", str(qr_path), "-t", "png"],
            input=client_config,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except FileNotFoundError as exc:
        if not previous:
            _discard(qr_path)
        raise CommandError(f"qrencode is not available: {exc}") from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        _discard(qr_path)
        raise CommandError(f"qrencode failed: {exc}") from exc
    if qrencode.returncode != 0:
        _discard(qr_path)
        raise CommandError(qrencode.stderr.strip() or "qrencode failed")
    try:
        # Defense in depth: if a future qrencode writes through a temporary
        # file and renames it into place, the PNG would otherwise inherit the
        # umask instead of the mode set above.
        os.chmod(qr_path, 0o600)
    except OSError as exc:
        _discard(qr_path)
        raise CommandError(f"Cannot secure QR code file {qr_path}: {exc}") from exc
