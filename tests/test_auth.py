"""Unit tests for password, TOTP and JWT helpers."""

import base64
import hashlib
import hmac
import struct
import time

from src.auth import (
    create_jwt_token,
    decode_jwt_token,
    generate_password_hash,
    revoke_jwt_token,
    verify_password,
    verify_totp,
)

SECRET = "test-session-secret-long-enough-for-hs256-0123456789"
TOTP_SECRET = base64.b32encode(b"12345678901234567890").decode()


def _totp_code(secret: str, counter: int) -> str:
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    value = (
        struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    ) % 1_000_000
    return f"{value:06d}"


# === password hashing ===

def test_password_hash_roundtrip():
    encoded = generate_password_hash("s3cret-pass")
    assert encoded.startswith("pbkdf2_sha256$")
    assert verify_password("s3cret-pass", encoded) is True


def test_verify_password_rejects_wrong_password():
    encoded = generate_password_hash("s3cret-pass")
    assert verify_password("nope", encoded) is False


def test_verify_password_rejects_malformed_hash():
    assert verify_password("x", "not-a-hash") is False
    assert verify_password("x", "bcrypt$1$abc$def") is False


# === TOTP ===

def test_verify_totp_accepts_current_code():
    counter = int(time.time()) // 30
    code = _totp_code(TOTP_SECRET, counter)
    assert verify_totp(code, TOTP_SECRET) is True


def test_verify_totp_accepts_neighbouring_window():
    counter = int(time.time()) // 30
    previous = _totp_code(TOTP_SECRET, counter - 1)
    assert verify_totp(previous, TOTP_SECRET) is True


def test_verify_totp_rejects_bad_code_and_secret():
    assert verify_totp("000000", TOTP_SECRET) is False
    assert verify_totp("abc", "not-base32-!!!") is False


# === JWT ===

def test_jwt_roundtrip():
    token = create_jwt_token({"username": "admin", "jti": "abc"}, SECRET)

    payload = decode_jwt_token(token, SECRET)

    assert payload is not None
    assert payload["username"] == "admin"
    assert payload["jti"] == "abc"


def test_decode_jwt_rejects_none_and_wrong_secret():
    token = create_jwt_token({"username": "admin"}, SECRET)

    assert decode_jwt_token(None, SECRET) is None
    assert decode_jwt_token(token, "other-session-secret-long-enough-hs256") is None
    assert decode_jwt_token("not-a-token", SECRET) is None


def test_decode_jwt_rejects_expired_token():
    token = create_jwt_token({"username": "admin"}, SECRET, expires_in=-10)
    assert decode_jwt_token(token, SECRET) is None


def test_decode_jwt_rejects_stale_age():
    token = create_jwt_token({"username": "admin"}, SECRET)
    assert decode_jwt_token(token, SECRET, max_age=-1) is None


def test_decode_jwt_rejects_revoked_token():
    token = create_jwt_token({"username": "admin", "jti": "revoke-me"}, SECRET)
    assert decode_jwt_token(token, SECRET) is not None

    revoke_jwt_token("revoke-me")

    assert decode_jwt_token(token, SECRET) is None
    revoke_jwt_token(None)


def test_generate_password_hash_uses_unique_salt():
    first = generate_password_hash("same")
    second = generate_password_hash("same")
    assert first != second
