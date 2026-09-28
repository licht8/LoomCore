"""Rate limits on session history, artifacts and logout (no DB).

Each route function is called directly with a real ``Request`` (slowapi's
decorator rejects anything that is not a ``starlette.requests.Request``). A
unique non-loopback client IP per test keeps the buckets independent, and the
autouse fixture resets the shared limiter so state cannot leak.
"""

import base64

import pytest
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request

from src.core.rate_limit import limiter


@pytest.fixture(autouse=True)
def _reset_limiter():
    limiter.reset()
    yield
    limiter.reset()


class _DummyContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc) -> bool:
        return False


def _request(host: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "client": (host, 50000),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )


@pytest.mark.asyncio
async def test_get_messages_is_rate_limited(monkeypatch):
    from src.api import sessions as sessions_api

    monkeypatch.setattr(
        sessions_api, "require_admin_token_from_request", lambda *a, **k: None
    )
    monkeypatch.setattr(sessions_api, "async_session_factory", lambda: _DummyContext())

    async def fake_history(db, session_id, *, limit):
        return []

    monkeypatch.setattr(sessions_api, "get_session_history", fake_history)

    request = _request("203.0.113.10")
    for _ in range(60):
        await sessions_api.get_messages("sess", _request(request.client.host), 200)
    with pytest.raises(RateLimitExceeded):
        await sessions_api.get_messages("sess", _request(request.client.host), 200)


@pytest.mark.asyncio
async def test_delete_messages_is_rate_limited(monkeypatch):
    from src.api import sessions as sessions_api

    monkeypatch.setattr(
        sessions_api, "require_admin_token_from_request", lambda *a, **k: None
    )
    monkeypatch.setattr(sessions_api, "async_session_factory", lambda: _DummyContext())

    async def fake_clear(db, session_id):
        return 0

    monkeypatch.setattr(sessions_api, "clear_session_history", fake_clear)

    request = _request("203.0.113.20")
    for _ in range(10):
        await sessions_api.delete_messages("sess", _request(request.client.host))
    with pytest.raises(RateLimitExceeded):
        await sessions_api.delete_messages("sess", _request(request.client.host))


@pytest.mark.asyncio
async def test_download_artifact_is_rate_limited(monkeypatch):
    from src.api import artifacts as artifacts_api

    monkeypatch.setattr(
        artifacts_api, "require_admin_token_from_request", lambda *a, **k: None
    )

    async def fake_exec(tool, arguments):
        return {
            "data": {
                "content_base64": base64.b64encode(b"x").decode(),
                "filename": "alice.conf",
                "media_type": "text/plain",
            }
        }

    monkeypatch.setattr(
        artifacts_api.plugin_manager, "execute_internal_tool", fake_exec
    )

    request = _request("203.0.113.30")
    for _ in range(60):
        await artifacts_api.download_artifact("alice", "config", _request(request.client.host))
    with pytest.raises(RateLimitExceeded):
        await artifacts_api.download_artifact("alice", "config", _request(request.client.host))


@pytest.mark.asyncio
async def test_logout_is_rate_limited(monkeypatch):
    from src.api import auth as auth_api

    monkeypatch.setattr(auth_api, "decode_jwt_token", lambda *a, **k: None)

    request = _request("203.0.113.40")
    for _ in range(10):
        await auth_api.logout(_request(request.client.host))
    with pytest.raises(RateLimitExceeded):
        await auth_api.logout(_request(request.client.host))
