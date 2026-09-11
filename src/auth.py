from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import sys
import time

import jwt

SESSION_COOKIE = "loomhub_session"
# Simple blocklist for revoked tokens (token_id → revoke timestamp)
_revoked: dict[str, float] = {}


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
        # Check if revoked
        token_id = payload.get("jti")
        if token_id and _revoked.get(token_id, 0) > 0:
            return None
        return payload
    except (jwt.ExpiredSignatureError, jwt.DecodeError, jwt.InvalidTokenError):
        return None


def revoke_jwt_token(token_id: str | None) -> None:
    """Revoke a token by id."""
    if token_id:
        _revoked[token_id] = int(time.time())


def generate_password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    rounds = 310_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)

    def encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    return f"pbkdf2_sha256${rounds}${encode(salt)}${encode(digest)}"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Использование: python -m src.auth <пароль>", file=sys.stderr)
        raise SystemExit(2)
    print(generate_password_hash(sys.argv[1]))
