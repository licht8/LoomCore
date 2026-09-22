"""Unit tests for shared API dependencies."""

import hashlib
from types import SimpleNamespace

from src.api.deps import chat_key_func, extract_admin_token
from src.auth import SESSION_COOKIE, create_jwt_token
from src.config import settings

_DEFAULT_CLIENT = SimpleNamespace(host="1.2.3.4")
_SESSION_SECRET = "test-session-secret-long-enough-for-hs256-123456"


def _request(*, cookies=None, query=None, headers=None, client=_DEFAULT_CLIENT):
    return SimpleNamespace(
        cookies=cookies or {},
        query_params=query or {},
        headers=headers or {},
        client=client,
    )


def test_extract_admin_token_ignores_query():
    # Query-string tokens leak via logs/history, so they are not accepted.
    request = _request(cookies={SESSION_COOKIE: "cookie"}, query={"access_token": "query"})
    assert extract_admin_token(request) == "cookie"

    bare = _request(query={"access_token": "query"})
    assert extract_admin_token(bare) is None


def test_extract_admin_token_prefers_valid_cookie_over_header(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "both")
    monkeypatch.setattr(settings, "admin_token", "static")
    monkeypatch.setattr(settings, "session_secret", _SESSION_SECRET)
    monkeypatch.setattr(settings, "admin_password_hash", "hash")
    monkeypatch.setattr(settings, "admin_totp_secret", "totp")
    jwt_cookie = create_jwt_token({"username": "admin"}, _SESSION_SECRET)
    request = _request(cookies={SESSION_COOKIE: jwt_cookie})
    assert extract_admin_token(request, "Bearer static") == jwt_cookie


def test_extract_admin_token_falls_through_to_header_when_cookie_invalid(
    monkeypatch,
):
    monkeypatch.setattr(settings, "auth_mode", "token")
    monkeypatch.setattr(settings, "admin_token", "good")
    request = _request(cookies={SESSION_COOKIE: "junk"})
    assert extract_admin_token(request, "Bearer good") == "good"


def test_extract_admin_token_returns_cookie_when_nothing_validates(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "")
    monkeypatch.setattr(settings, "admin_token", "")
    monkeypatch.setattr(settings, "admin_password_hash", "")
    monkeypatch.setattr(settings, "admin_totp_secret", "")
    request = _request(cookies={SESSION_COOKIE: "stale-cookie"})
    assert extract_admin_token(request, "Bearer stale-header") == "stale-cookie"


def test_extract_admin_token_reads_bearer_header():
    request = _request()
    assert extract_admin_token(request, "Bearer header") == "header"


def test_extract_admin_token_ignores_non_bearer_header():
    request = _request()
    assert extract_admin_token(request, "Basic abc") is None
    assert extract_admin_token(request, None) is None


def test_extract_admin_token_reads_header_from_request_when_arg_omitted():
    # Callers that do not pass the explicit argument must still see the header.
    request = _request(headers={"authorization": "Bearer from-header"})
    assert extract_admin_token(request) == "from-header"


def test_extract_admin_token_bearer_is_case_insensitive():
    request = _request()
    assert extract_admin_token(request, "bearer lower") == "lower"
    assert extract_admin_token(request, "BEARER upper") == "upper"


def test_chat_key_func_uses_credential_not_session_id(monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "token")
    monkeypatch.setattr(settings, "admin_token", "token-a")
    request = _request(
        query={"session_id": "sess-1"},
        headers={"authorization": "Bearer token-a"},
    )
    expected = "token:" + hashlib.sha256(b"token-a").hexdigest()
    assert chat_key_func(request) == expected
    # Rotating the client-supplied session_id must not change the key.
    rotated = _request(
        query={"session_id": "sess-2"},
        headers={"authorization": "Bearer token-a"},
    )
    assert chat_key_func(rotated) == expected


def test_chat_key_func_ignores_a_forged_credential(monkeypatch):
    # An unauthenticated caller must not get a fresh bucket by inventing a cookie.
    monkeypatch.setattr(settings, "auth_mode", "token")
    monkeypatch.setattr(settings, "admin_token", "real-token")
    first = _request(cookies={SESSION_COOKIE: "random-1"})
    assert chat_key_func(first) == "ip:1.2.3.4"
    # A different bogus value maps to the same (IP) bucket.
    second = _request(cookies={SESSION_COOKIE: "random-2"})
    assert chat_key_func(second) == chat_key_func(first)


def test_chat_key_func_ignores_stale_cookie_when_header_valid(monkeypatch):
    # A junk cookie must not drag an authenticated caller into the IP bucket.
    monkeypatch.setattr(settings, "auth_mode", "token")
    monkeypatch.setattr(settings, "admin_token", "real")
    request = _request(
        cookies={SESSION_COOKIE: "stale"},
        headers={"authorization": "Bearer real"},
    )
    expected = "token:" + hashlib.sha256(b"real").hexdigest()
    assert chat_key_func(request) == expected


def test_chat_key_func_falls_back_to_client_ip():
    request = _request()
    assert chat_key_func(request) == "ip:1.2.3.4"


def test_chat_key_func_without_client():
    request = _request(client=None)
    assert chat_key_func(request) == "ip:unknown"
