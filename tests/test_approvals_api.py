"""Tests for the read-only approvals route (no DB, no auth)."""

import pytest

from src.api import approvals as approvals_api


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
        approvals_api, "require_admin_token_from_request", lambda *a, **k: None
    )


def _patch_plans(monkeypatch: pytest.MonkeyPatch, rows: list) -> dict:
    captured: dict = {}

    async def fake_list(db, limit=100):
        captured["limit"] = limit
        return rows

    monkeypatch.setattr(approvals_api, "async_session_factory", lambda: _DummyContext())
    monkeypatch.setattr(approvals_api, "list_pending_plans", fake_list)
    return captured


@pytest.mark.asyncio
async def test_list_approvals_returns_rows(monkeypatch):
    _patch_auth(monkeypatch)
    rows = [
        {
            "plan_id": "pl_1",
            "session_id": "sess",
            "goal": "do the thing",
            "revision": 2,
            "step_count": 2,
            "risks": ["dangerous", "safe"],
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    ]
    captured = _patch_plans(monkeypatch, rows)

    result = await approvals_api.list_approvals(request=None, limit=25)

    assert result == {"approvals": rows}
    assert captured["limit"] == 25


@pytest.mark.asyncio
async def test_list_approvals_empty(monkeypatch):
    _patch_auth(monkeypatch)
    _patch_plans(monkeypatch, [])

    result = await approvals_api.list_approvals(request=None, limit=100)

    assert result == {"approvals": []}


@pytest.mark.asyncio
async def test_approvals_requires_admin(monkeypatch):
    def deny(request, authorization):
        raise PermissionError("nope")

    monkeypatch.setattr(approvals_api, "require_admin_token_from_request", deny)

    with pytest.raises(PermissionError):
        await approvals_api.list_approvals(request=None, limit=100)
