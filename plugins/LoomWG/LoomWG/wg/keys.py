"""WireGuard key validation helpers."""

from __future__ import annotations

import base64

from .device import CommandError, run_command


def validate_public_key(value: str) -> str:
    key = value.strip()
    try:
        decoded = base64.b64decode(key, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("Invalid WireGuard public key") from exc
    if len(decoded) != 32:
        raise ValueError("WireGuard public key must encode 32 bytes")
    return key


def generate_keypair() -> dict[str, str]:
    private_key = run_command(["wg", "genkey"], sensitive=True)
    public_key = run_command(["wg", "pubkey"], timeout=5, input_text=private_key)
    if not private_key or not public_key:
        raise CommandError("WireGuard did not return keys")
    return {"private_key": private_key, "public_key": public_key}
