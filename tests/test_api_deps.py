"""Unit tests for shared API dependencies."""

import hashlib
from types import SimpleNamespace

from src.api.deps import chat_key_func, extract_admin_token
from src.auth import SESSION_COOKIE

_DEFAULT_CLIENT = SimpleNamespace(host="1.2.3.4")


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


def test_extract_admin_token_prefers_cookie_over_header():
    request = _request(cookies={SESSION_COOKIE: "cookie"})
    assert extract_admin_token(request, "Bearer header") == "cookie"


def test_extract_admin_token_reads_bearer_header():
    request = _request()
    assert extract_admin_token(request, "Bearer header") == "header"


def test_extract_admin_token_ignores_non_bearer_header():
    request = _request()
    assert extract_admin_token(request, "Basic abc") is None
    assert extract_admin_token(request, None) is None


def test_chat_key_func_uses_credential_not_session_id():
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


def test_chat_key_func_falls_back_to_client_ip():
    request = _request()
    assert chat_key_func(request) == "ip:1.2.3.4"


def test_chat_key_func_without_client():
    request = _request(client=None)
    assert chat_key_func(request) == "ip:unknown"
