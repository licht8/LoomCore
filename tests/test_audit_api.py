"""Tests for the read-only audit routes (no DB, no auth)."""

from types import SimpleNamespace

import pytest

from src.api import audit as audit_api
from src.core.audit import GENESIS_HASH, compute_entry_hash, entry_payload


@pytest.fixture(autouse=True)
def _disable_limiter(monkeypatch):
    from src.core.rate_limit import limiter

    monkeypatch.setattr(limiter, "enabled", False)


class _DummyContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc) -> bool:
        return False


def _payload(**overrides):
    base = {
        "created_at": "2026-01-01T00:00:00+00:00",
        "actor": "admin",
        "session_id": "sess",
        "plan_id": "pl_1",
        "revision": 1,
        "tool": "ops_run_command",
        "risk": "dangerous",
        "arguments": {"command": "uptime"},
        "status": "success",
        "error": None,
    }
    base.update(overrides)
    return entry_payload(**base)


def _entry(prev_hash, entry_id="a1", **overrides):
    payload = _payload(**overrides)
    return SimpleNamespace(
        id=entry_id,
        **payload,
        prev_hash=prev_hash,
        entry_hash=compute_entry_hash(prev_hash, payload),
    )


def _patch_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        audit_api, "require_admin_token_from_request", lambda *a, **k: None
    )


def _patch_entries(monkeypatch: pytest.MonkeyPatch, entries: list) -> None:
    async def fake_list(db, *, limit, offset):
        return entries[offset : offset + limit]

    async def fake_count(db):
        return len(entries)

    async def fake_all(db):
        return entries

    monkeypatch.setattr(audit_api, "async_session_factory", lambda: _DummyContext())
    monkeypatch.setattr(audit_api, "list_audit_entries", fake_list)
    monkeypatch.setattr(audit_api, "count_audit_entries", fake_count)
    monkeypatch.setattr(audit_api, "all_audit_entries", fake_all)


@pytest.mark.asyncio
async def test_list_audit_serializes_without_hashes(monkeypatch):
    _patch_auth(monkeypatch)
    _patch_entries(monkeypatch, [_entry(GENESIS_HASH)])

    result = await audit_api.list_audit(request=None, limit=50, offset=0)

    assert result["total"] == 1
    entry = result["entries"][0]
    assert entry["tool"] == "ops_run_command"
    assert entry["arguments"] == {"command": "uptime"}
    assert "prev_hash" not in entry
    assert "entry_hash" not in entry


@pytest.mark.asyncio
async def test_verify_audit_accepts_valid_chain(monkeypatch):
    _patch_auth(monkeypatch)
    first = _entry(GENESIS_HASH)
    second = _entry(first.entry_hash, entry_id="a2", tool="ops_disk_usage")
    _patch_entries(monkeypatch, [first, second])

    result = await audit_api.verify_audit(request=None)

    assert result == {"valid": True, "count": 2, "head": second.entry_hash}


@pytest.mark.asyncio
async def test_verify_audit_detects_tampering(monkeypatch):
    _patch_auth(monkeypatch)
    first = _entry(GENESIS_HASH)
    tampered = _entry(first.entry_hash, entry_id="a2")
    tampered.status = "failed"
    _patch_entries(monkeypatch, [first, tampered])

    result = await audit_api.verify_audit(request=None)

    assert result["valid"] is False
    assert result["count"] == 2


@pytest.mark.asyncio
async def test_verify_audit_empty_chain(monkeypatch):
    _patch_auth(monkeypatch)
    _patch_entries(monkeypatch, [])

    result = await audit_api.verify_audit(request=None)

    assert result == {"valid": True, "count": 0, "head": None}


@pytest.mark.asyncio
async def test_audit_routes_require_admin(monkeypatch):
    def deny(request, authorization):
        raise PermissionError("nope")

    monkeypatch.setattr(audit_api, "require_admin_token_from_request", deny)

    with pytest.raises(PermissionError):
        await audit_api.list_audit(request=None, limit=50, offset=0)
    with pytest.raises(PermissionError):
        await audit_api.verify_audit(request=None)
