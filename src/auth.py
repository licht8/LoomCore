from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import logging
import os
import secrets
import struct
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import jwt

try:  # pragma: no cover - advisory file locking is POSIX-only
    import fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl
    fcntl = None

from src.config import settings

logger = logging.getLogger("loomcore.auth")

SESSION_COOKIE = "loomcore_session"
# Simple blocklist for revoked tokens (token_id → revoke timestamp).
_revoked: dict[str, float] = {}
_revoked_loaded = False
_revoked_signature: tuple[int, int] | None = None
_revoked_lock = threading.RLock()
# Well above the 310_000 rounds used by generate_password_hash; anything larger
# is treated as an attack on the verifier rather than a real hash.
_MAX_PBKDF2_ROUNDS = 10_000_000
# Revoked tokens only need to outlive the token itself (8 h). Keeping three
# token lifetimes (24 h) bounds the store while never reviving a still-valid
# token, and the extra margin absorbs clock skew between writers.
_REVOKE_RETENTION_SECONDS = 86400


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


def _revoked_file_signature(path: Path) -> tuple[int, int] | None:
    """Return ``(st_mtime_ns, st_size)`` for the store, or ``None`` if unreadable."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _parse_revoked(data: object) -> dict[str, float]:
    """Extract ``{token_id: timestamp}`` pairs from a decoded store payload.

    An entry whose timestamp cannot be converted is skipped rather than aborting
    the whole parse, so one tampered value cannot disable the store.
    """
    if not isinstance(data, dict):
        return {}
    parsed: dict[str, float] = {}
    for token_id, timestamp in data.items():
        if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool):
            continue
        try:
            parsed[str(token_id)] = float(timestamp)
        except (TypeError, ValueError, OverflowError):
            continue
    return parsed


def _read_revoked_file(path: Path) -> dict[str, float] | None:
    """Read and parse the store; ``None`` means missing, unreadable or corrupt."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning(f"Cannot read revoked-session store '{path}': {exc}")
        return None
    if not raw.strip():
        # An empty file is a valid empty store, not corruption: returning an
        # empty mapping lets _load_revoked latch the signature and stop warning
        # on every decode.
        return {}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        logger.warning(f"Cannot read revoked-session store '{path}': {exc}")
        return None
    return _parse_revoked(data)


def _prune_revoked(now: float | None = None) -> None:
    """Drop revocation entries older than the retention window (in place)."""
    cutoff = (time.time() if now is None else now) - _REVOKE_RETENTION_SECONDS
    for token_id in [tid for tid, revoked_at in _revoked.items() if revoked_at < cutoff]:
        del _revoked[token_id]


@contextlib.contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    """Best-effort advisory lock around a read-merge-write of the store.

    Uses ``fcntl.flock`` on a ``<store>.lock`` sibling when available; on
    platforms without ``fcntl`` (Windows) it is a no-op, so callers still work.
    """
    if fcntl is None:
        yield
        return
    try:
        lock_path = path.with_name(path.name + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    except (OSError, ValueError):
        yield
        return
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_NB)
        except OSError:
            # Another process (or a slow filesystem) holds the lock. Waiting
            # would stall the event loop for a best-effort persist, so degrade to
            # the same no-lock path used on platforms without ``fcntl``.
            logger.warning(
                "Revoked-session store lock is busy; writing without it."
            )
            yield
            return
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        with contextlib.suppress(OSError):
            os.close(descriptor)


def _load_revoked() -> None:
    """(Re)load revocations from disk when the configured store changes.

    The store is read at most once per distinct ``(st_mtime_ns, st_size)``
    signature. The signature is captured *before* the read and the loaded state
    is latched only after a successful parse, so a missing, corrupt or unreadable
    file never disables a later reload attempt.
    """
    global _revoked_loaded, _revoked_signature
    path = _revoked_path()
    if path is None:
        return
    with _revoked_lock:
        signature = _revoked_file_signature(path)
        if _revoked_loaded and signature is not None and signature == _revoked_signature:
            return
        parsed = _read_revoked_file(path)
        if parsed is None:
            return
        _revoked.update(parsed)
        _revoked_loaded = True
        _revoked_signature = signature


def _save_revoked() -> None:
    """Persist revocations atomically; never fail the request on a write error."""
    # Prune before the file-less early return so the in-memory-only store is
    # bounded too (it never reaches the write path).
    _prune_revoked()
    path = _revoked_path()
    if path is None:
        return
    with _revoked_lock, _file_lock(path):
        on_disk = _read_revoked_file(path)
        if on_disk is not None:
            _revoked.update(on_disk)
        _prune_revoked()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temp_name = tempfile.mkstemp(
                dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
            )
        except OSError as exc:
            logger.warning(f"Cannot persist revoked sessions to '{path}': {exc}")
            return
        temporary = Path(temp_name)
        try:
            try:
                handle = os.fdopen(descriptor, "w", encoding="utf-8")
            except OSError:
                # fdopen did not take ownership of the descriptor; close it so
                # the failing call cannot leak the raw file descriptor.
                with contextlib.suppress(OSError):
                    os.close(descriptor)
                raise
            with handle:
                handle.write(json.dumps(_revoked))
            os.replace(temporary, path)
        except OSError as exc:
            logger.warning(f"Cannot persist revoked sessions to '{path}': {exc}")
        finally:
            # Always drop the staging file: on success os.replace moved it away
            # (unlink is then a no-op on a missing path), on any failure it is
            # garbage. Runs even for non-OSError escapes (e.g. KeyboardInterrupt).
            with contextlib.suppress(OSError):
                temporary.unlink()


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds_text, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        rounds = int(rounds_text)
        if rounds <= 0 or rounds > _MAX_PBKDF2_ROUNDS:
            return False
        salt = base64.urlsafe_b64decode(salt_text + "===")
        expected = base64.urlsafe_b64decode(digest_text + "===")
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    except (ValueError, TypeError, OverflowError):
        return False
    return hmac.compare_digest(actual, expected)


def verify_totp(code: str, secret: str) -> bool:
    # An empty secret decodes to an empty HMAC key, which would happily verify a
    # code derived from that key; fail closed instead.
    if not secret or not secret.strip():
        return False
    normalized_code = "".join(code.split())
    if len(normalized_code) != 6 or not normalized_code.isdigit():
        return False
    try:
        key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
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
    """Verify and decode a JWT token. Returns payload dict or None.

    Fails closed on a missing token or an empty signing key: an HMAC keyed with
    ``""`` is not a key, so an empty secret must never validate a token.
    """
    if not token or not secret:
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
        with _revoked_lock:
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
