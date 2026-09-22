"""Unit tests for password, TOTP and JWT helpers."""

import base64
import hashlib
import hmac
import json
import logging
import struct
import threading
import time
from pathlib import Path

import pytest

import src.auth as auth
from src.auth import (
    create_jwt_token,
    decode_jwt_token,
    generate_password_hash,
    revoke_jwt_token,
    verify_password,
    verify_totp,
)
from src.config import settings

SECRET = "test-session-secret-long-enough-for-hs256-0123456789"
TOTP_SECRET = base64.b32encode(b"12345678901234567890").decode()


@pytest.fixture(autouse=True)
def _isolated_revocation(monkeypatch):
    """Keep the revocation store in-memory unless a test opts into persistence."""
    monkeypatch.setattr(settings, "loomcore_revoked_file", "")
    monkeypatch.setattr(auth, "_revoked", {})
    monkeypatch.setattr(auth, "_revoked_loaded", False)
    monkeypatch.setattr(auth, "_revoked_signature", None)


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


def test_verify_totp_rejects_empty_secret():
    counter = int(time.time()) // 30
    empty_key_code = _totp_code("", counter)
    assert verify_totp(empty_key_code, "") is False
    assert verify_totp(empty_key_code, "   ") is False


def test_verify_totp_requires_six_digit_code():
    counter = int(time.time()) // 30
    code = _totp_code(TOTP_SECRET, counter)
    assert verify_totp(code[:5], TOTP_SECRET) is False
    assert verify_totp(code + "0", TOTP_SECRET) is False
    assert verify_totp("abcdef", TOTP_SECRET) is False


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


def test_decode_jwt_rejects_empty_secret():
    token = create_jwt_token({"username": "admin"}, SECRET)

    assert decode_jwt_token(token, "") is None


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


# === persistent revocation store ===

def test_revocation_persists_when_store_configured(monkeypatch, tmp_path):
    store = tmp_path / "revoked.json"
    monkeypatch.setattr(settings, "loomcore_revoked_file", str(store))
    token = create_jwt_token({"username": "admin", "jti": "persist-me"}, SECRET)
    assert decode_jwt_token(token, SECRET) is not None

    revoke_jwt_token("persist-me")

    assert store.is_file()

    # Simulate a fresh process: drop the in-memory cache and reload from disk.
    monkeypatch.setattr(auth, "_revoked", {})
    monkeypatch.setattr(auth, "_revoked_loaded", False)

    assert decode_jwt_token(token, SECRET) is None


def test_revocation_without_store_stays_in_memory(monkeypatch):
    token = create_jwt_token({"username": "admin", "jti": "memory-only"}, SECRET)
    revoke_jwt_token("memory-only")

    assert decode_jwt_token(token, SECRET) is None
    assert auth._revoked_path() is None


def test_revocation_store_rereads_changed_file(monkeypatch, tmp_path):
    store = tmp_path / "revoked.json"
    monkeypatch.setattr(settings, "loomcore_revoked_file", str(store))
    store.write_text(json.dumps({"a": 1}), encoding="utf-8")

    token_a = create_jwt_token({"username": "admin", "jti": "a"}, SECRET)
    assert decode_jwt_token(token_a, SECRET) is None

    # A different-length payload adds a new revocation, so the signature differs.
    store.write_text(json.dumps({"a": 1, "b": 2}), encoding="utf-8")
    token_b = create_jwt_token({"username": "admin", "jti": "b"}, SECRET)
    assert decode_jwt_token(token_b, SECRET) is None


def test_revocation_load_failure_is_retried(monkeypatch, tmp_path):
    store = tmp_path / "revoked.json"
    monkeypatch.setattr(settings, "loomcore_revoked_file", str(store))
    store.write_text("{not json", encoding="utf-8")

    token = create_jwt_token({"username": "admin", "jti": "retry-me"}, SECRET)
    assert decode_jwt_token(token, SECRET) is not None
    assert auth._revoked_loaded is False

    store.write_text(json.dumps({"retry-me": 1}), encoding="utf-8")
    assert decode_jwt_token(token, SECRET) is None


def test_revocation_store_survives_huge_timestamp(monkeypatch, tmp_path):
    store = tmp_path / "revoked.json"
    monkeypatch.setattr(settings, "loomcore_revoked_file", str(store))
    store.write_text(json.dumps({"a": 10**400, "ok": 1}), encoding="utf-8")

    unrelated = create_jwt_token({"username": "admin", "jti": "unrelated"}, SECRET)
    assert decode_jwt_token(unrelated, SECRET) is not None

    huge = create_jwt_token({"username": "admin", "jti": "a"}, SECRET)
    assert decode_jwt_token(huge, SECRET) is not None

    revoked = create_jwt_token({"username": "admin", "jti": "ok"}, SECRET)
    assert decode_jwt_token(revoked, SECRET) is None


def test_save_revoked_cleans_temp_on_unexpected_error(monkeypatch, tmp_path):
    store = tmp_path / "revoked.json"
    monkeypatch.setattr(settings, "loomcore_revoked_file", str(store))

    def boom(*args, **kwargs):
        raise RuntimeError("serialize failed")

    monkeypatch.setattr(auth.json, "dumps", boom)

    with pytest.raises(RuntimeError):
        auth._save_revoked()

    assert list(tmp_path.glob("*.tmp")) == []


def test_verify_password_rejects_absurd_rounds(monkeypatch):
    encoded = generate_password_hash("pw")
    _, _, salt_text, digest_text = encoded.split("$", 3)

    def with_rounds(rounds: int) -> str:
        return f"pbkdf2_sha256${rounds}${salt_text}${digest_text}"

    assert verify_password("pw", with_rounds(0)) is False
    assert verify_password("pw", with_rounds(10**20)) is False

    def boom(*args, **kwargs):
        raise OverflowError("too many rounds")

    monkeypatch.setattr(auth.hashlib, "pbkdf2_hmac", boom)
    assert verify_password("pw", encoded) is False


def test_concurrent_revocations_are_not_lost(monkeypatch, tmp_path):
    store = tmp_path / "revoked.json"
    monkeypatch.setattr(settings, "loomcore_revoked_file", str(store))
    ids = [f"jti-{index}" for index in range(8)]

    threads = [
        threading.Thread(target=revoke_jwt_token, args=(token_id,)) for token_id in ids
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    persisted = json.loads(store.read_text(encoding="utf-8"))
    assert set(ids) <= set(persisted)
    assert list(tmp_path.glob("*.tmp")) == []


# === retention, empty store and file locking ===

def test_empty_revocation_store_reads_as_empty(monkeypatch, tmp_path):
    store = tmp_path / "revoked.json"
    store.write_text("", encoding="utf-8")
    monkeypatch.setattr(settings, "loomcore_revoked_file", str(store))
    monkeypatch.setattr(auth, "_revoked", {})
    monkeypatch.setattr(auth, "_revoked_loaded", False)

    token = create_jwt_token({"username": "admin", "jti": "x"}, SECRET)
    assert decode_jwt_token(token, SECRET) is not None
    # A blank file is a valid (empty) store, so the signature is latched.
    assert auth._revoked_loaded is True


def test_revocation_pruning_drops_old_entries_keeps_fresh(monkeypatch, tmp_path):
    store = tmp_path / "revoked.json"
    monkeypatch.setattr(settings, "loomcore_revoked_file", str(store))
    now = time.time()
    monkeypatch.setattr(
        auth,
        "_revoked",
        {
            "old": now - auth._REVOKE_RETENTION_SECONDS - 1,
            "fresh": now,
        },
    )

    auth._save_revoked()

    persisted = json.loads(store.read_text(encoding="utf-8"))
    assert "fresh" in persisted
    assert "old" not in persisted


def test_file_lock_is_noop_without_fcntl(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "fcntl", None)
    store = tmp_path / "revoked.json"

    ran = False
    with auth._file_lock(store):
        ran = True

    assert ran is True
    assert not (tmp_path / "revoked.json.lock").exists()


def test_file_lock_degrades_when_busy(monkeypatch, tmp_path, caplog):
    class _BusyFcntl:
        LOCK_NB = 1
        LOCK_UN = 2

        def flock(self, descriptor, operation):
            if operation == self.LOCK_NB:
                raise OSError("lock is held elsewhere")

    monkeypatch.setattr(auth, "fcntl", _BusyFcntl())
    store = tmp_path / "revoked.json"

    ran = False
    with (
        caplog.at_level(logging.WARNING, logger="loomcore.auth"),
        auth._file_lock(store),
    ):
        ran = True

    assert ran is True
    assert any("lock is busy" in message for message in caplog.messages)


def test_file_lock_tolerates_unnamable_store(monkeypatch):
    class _FakeFcntl:
        LOCK_NB = 1
        LOCK_UN = 2

        def flock(self, descriptor, operation):
            return None

    monkeypatch.setattr(auth, "fcntl", _FakeFcntl())

    ran = False
    with auth._file_lock(Path(".")):
        ran = True

    assert ran is True
