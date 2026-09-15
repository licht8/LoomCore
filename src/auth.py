from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import struct
import sys
import time
from pathlib import Path

import jwt

from src.config import settings

logger = logging.getLogger("loomcore.auth")

SESSION_COOKIE = "loomcore_session"
# Simple blocklist for revoked tokens (token_id → revoke timestamp).
_revoked: dict[str, float] = {}
_revoked_loaded = False


def _revoked_path() -> Path | None:
    """Path of the persistent revocation store, or None for in-memory only.

    ``LOOMCORE_REVOKED_FILE`` (``settings.loomcore_revoked_file``) points at a
    file on a persistent volume so logged-out tokens stay invalid across
    restarts; an empty value keeps revocations in this process only.
    """
    configured = (settings.loomcore_revoked_file or "").strip()
    return Path(configured).expanduser() if configured else None


def revocation_store_path() -> Path | None:
    """Public accessor for the configured revocation-store path (startup checks)."""
    return _revoked_path()


def _load_revoked() -> None:
    """Load revocations from disk once, if a store path is configured."""
    global _revoked, _revoked_loaded
    if _revoked_loaded:
        return
    _revoked_loaded = True
    path = _revoked_path()
    if path is None:
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except (OSError, ValueError) as exc:
        logger.warning(f"Cannot read revoked-session store '{path}': {exc}")
        return
    if isinstance(data, dict):
        _revoked = {
            str(token_id): float(timestamp)
            for token_id, timestamp in data.items()
            if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool)
        }


def _save_revoked() -> None:
    """Persist revocations atomically; never fail the request on a write error."""
    path = _revoked_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(json.dumps(_revoked), encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        logger.warning(f"Cannot persist revoked sessions to '{path}': {exc}")


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds_text, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_text + "===")
        expected = base64.urlsafe_b64decode(digest_text + "===")
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(rounds_text))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def verify_totp(code: str, secret: str) -> bool:
    try:
        key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
        normalized_code = "".join(code.split())
        current_counter = int(time.time()) // 30
        for counter in range(current_counter - 1, current_counter + 2):
            digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
            offset = digest[-1] & 15
            value = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
            if hmac.compare_digest(f"{value:06d}", normalized_code):
                return True
        return False
    except (ValueError, TypeError):
        return False


def create_jwt_token(payload: dict, secret: str, expires_in: int = 28800) -> str:
    """Create a signed JWT token (survives container restarts)."""
    now = int(time.time())
    data = {**payload, "iat": now, "exp": now + expires_in}
    return jwt.encode(data, secret, algorithm="HS256")


def decode_jwt_token(token: str | None, secret: str, max_age: int = 28800) -> dict | None:
    """Verify and decode a JWT token. Returns payload dict or None."""
    if not token:
        return None
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        # Check age
        age = int(time.time()) - payload.get("iat", 0)
        if age > max_age:
            return None
        # Check if revoked (loaded from disk on first use, when configured)
        token_id = payload.get("jti")
        _load_revoked()
        if token_id and _revoked.get(token_id, 0) > 0:
            return None
        return payload
    except (jwt.ExpiredSignatureError, jwt.DecodeError, jwt.InvalidTokenError):
        return None


def revoke_jwt_token(token_id: str | None) -> None:
    """Revoke a token by id (persisted when LOOMCORE_REVOKED_FILE is set)."""
    if token_id:
        _load_revoked()
        _revoked[token_id] = int(time.time())
        _save_revoked()


def generate_password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    rounds = 310_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)

    def encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    return f"pbkdf2_sha256${rounds}${encode(salt)}${encode(digest)}"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m src.auth <password>", file=sys.stderr)
        raise SystemExit(2)
    print(generate_password_hash(sys.argv[1]))
