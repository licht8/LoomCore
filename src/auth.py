from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time

SESSION_COOKIE = "loomhub_session"
_sessions: dict[str, float] = {}


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


def create_session(secret: str) -> str:
    token = secrets.token_urlsafe(48)
    _sessions[token] = time.time() + 28800
    return token


def verify_session(value: str | None, secret: str, max_age: int = 28800) -> bool:
    if not value:
        return False
    expires_at = _sessions.get(value)
    if expires_at is None or expires_at < time.time():
        _sessions.pop(value, None)
        return False
    return True


def revoke_session(value: str | None) -> None:
    if value:
        _sessions.pop(value, None)


def generate_password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    rounds = 310_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    encode = lambda value: base64.urlsafe_b64encode(value).decode().rstrip("=")
    return f"pbkdf2_sha256${rounds}${encode(salt)}${encode(digest)}"
