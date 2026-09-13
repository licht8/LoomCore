"""Tests for the session history REST API (no DB, no auth)."""

import pytest

from src.api import sessions as sessions_api


class _FakeMessage:
    """Stand-in for a stored Message ORM row."""

    def __init__(
        self,
        message_id: str,
        role: str,
        content: str,
        created_at: str = "2026-01-01T00:00:00+00:00",
        tool_calls=None,
        tool_result=None,
    ):
        self.id = message_id
        self.role = role
        self.content = content
        self.created_at = created_at
        self.tool_calls = tool_calls
        self.tool_result = tool_result


class _DummyContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc) -> bool:
        return False


def _patch_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sessions_api, "extract_admin_token", lambda *a, **k: None)
    monkeypatch.setattr(sessions_api, "require_admin_token", lambda token: None)


def _patch_history(monkeypatch: pytest.MonkeyPatch, messages: list) -> None:
    async def fake_history(db, session_id, limit=50):
        return messages

    monkeypatch.setattr(sessions_api, "async_session_factory", lambda: _DummyContext())
    monkeypatch.setattr(sessions_api, "get_session_history", fake_history)


@pytest.mark.asyncio
async def test_get_messages_serializes_rows(monkeypatch):
    _patch_auth(monkeypatch)
    _patch_history(
        monkeypatch,
        [
            _FakeMessage("m1", "user", "hello"),
            _FakeMessage("m2", "assistant", "hi"),
            _FakeMessage(
                "m3",
                "tool",
                "ok",
                tool_calls=[
                    {
                        "name": "wg_interface_status",
                        "status": "success",
                        "summary": "Done",
                    }
                ],
            ),
        ],
    )

    result = await sessions_api.get_messages("sess", request=None, limit=200)

    assert result["session_id"] == "sess"
    assert [m["role"] for m in result["messages"]] == ["user", "assistant", "tool"]
    assert result["messages"][2]["tool_calls"][0]["name"] == "wg_interface_status"
    assert result["messages"][0]["created_at"] == "2026-01-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_get_messages_empty(monkeypatch):
    _patch_auth(monkeypatch)
    _patch_history(monkeypatch, [])

    result = await sessions_api.get_messages("sess", request=None, limit=200)

    assert result == {"session_id": "sess", "messages": []}


@pytest.mark.asyncio
async def test_delete_messages_returns_count(monkeypatch):
    _patch_auth(monkeypatch)
    captured: dict = {}

    async def fake_clear(db, session_id):
        captured["session_id"] = session_id
        return 3

    monkeypatch.setattr(sessions_api, "async_session_factory", lambda: _DummyContext())
    monkeypatch.setattr(sessions_api, "clear_session_history", fake_clear)

    result = await sessions_api.delete_messages("sess", request=None)

    assert result == {"session_id": "sess", "deleted": 3}
    assert captured["session_id"] == "sess"
