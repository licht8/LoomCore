"""Tests for the read-only session-list route (no DB, no auth)."""

import pytest

from src.api import sessions as sessions_api


@pytest.fixture(autouse=True)
def _disable_limiter(monkeypatch):
    from src.core.rate_limit import limiter

    monkeypatch.setattr(limiter, "enabled", False)


class _DummyContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc) -> bool:
        return False


def _patch_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sessions_api, "require_admin_token_from_request", lambda *a, **k: None
    )


def _patch_sessions(monkeypatch: pytest.MonkeyPatch, rows: list) -> dict:
    captured: dict = {}

    async def fake_list(db, limit=100):
        captured["limit"] = limit
        return rows

    monkeypatch.setattr(sessions_api, "async_session_factory", lambda: _DummyContext())
    monkeypatch.setattr(sessions_api, "list_sessions", fake_list)
    return captured


@pytest.mark.asyncio
async def test_list_sessions_returns_rows(monkeypatch):
    _patch_auth(monkeypatch)
    rows = [
        {
            "session_id": "s2",
            "title": "second",
            "preview": "latest",
            "message_count": 2,
            "last_message_at": "2026-02-01T00:00:00+00:00",
        },
        {
            "session_id": "s1",
            "title": "first",
            "preview": "older",
            "message_count": 1,
            "last_message_at": "2026-01-01T00:00:00+00:00",
        },
    ]
    captured = _patch_sessions(monkeypatch, rows)

    result = await sessions_api.list_sessions_route(request=None, limit=50)

    assert result == {"sessions": rows}
    assert captured["limit"] == 50


@pytest.mark.asyncio
async def test_list_sessions_empty(monkeypatch):
    _patch_auth(monkeypatch)
    _patch_sessions(monkeypatch, [])

    result = await sessions_api.list_sessions_route(request=None, limit=100)

    assert result == {"sessions": []}


@pytest.mark.asyncio
async def test_sessions_route_requires_admin(monkeypatch):
    def deny(request, authorization):
        raise PermissionError("nope")

    monkeypatch.setattr(sessions_api, "require_admin_token_from_request", deny)

    with pytest.raises(PermissionError):
        await sessions_api.list_sessions_route(request=None, limit=100)
