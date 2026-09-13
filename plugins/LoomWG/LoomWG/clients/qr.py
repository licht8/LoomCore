"""Generate PNG QR codes for WireGuard client configs."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ..wg.device import CommandError


def generate_qr(client_config: str, qr_path: Path) -> None:
    """Render a client config to a PNG QR code (mode 600)."""
    qr_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        qrencode = subprocess.run(
            ["qrencode", "-o", str(qr_path), "-t", "png"],
            input=client_config,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except OSError as exc:
        raise CommandError(f"qrencode is not available: {exc}") from exc
    if qrencode.returncode != 0:
        raise CommandError(qrencode.stderr.strip() or "qrencode failed")
    os.chmod(qr_path, 0o600)
