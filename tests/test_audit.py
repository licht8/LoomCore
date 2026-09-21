"""Unit tests for the central hash-chained audit log (no database)."""

from types import SimpleNamespace

import pytest
from sqlalchemy import UniqueConstraint

from src.core.audit import (
    GENESIS_HASH,
    _record_mutation,
    compute_entry_hash,
    entry_payload,
    verify_chain,
)
from src.db.models import AuditEntry


def _payload(**overrides):
    base = {
        "created_at": "2026-01-01T00:00:00+00:00",
        "actor": "admin",
        "session_id": "sess",
        "plan_id": "pl_1",
        "revision": 1,
        "tool": "wg_remove_peer",
        "risk": "dangerous",
        "arguments": {"interface": "wg0"},
        "status": "success",
        "error": None,
    }
    base.update(overrides)
    return entry_payload(**base)


def _entry(prev_hash, **overrides):
    payload = _payload(**overrides)
    return SimpleNamespace(
        **payload,
        prev_hash=prev_hash,
        entry_hash=compute_entry_hash(prev_hash, payload),
    )


def test_compute_entry_hash_is_deterministic():
    payload = _payload()
    assert compute_entry_hash(GENESIS_HASH, payload) == compute_entry_hash(
        GENESIS_HASH, payload
    )

    changed = _payload(status="failed")
    assert compute_entry_hash(GENESIS_HASH, changed) != compute_entry_hash(
        GENESIS_HASH, payload
    )


def test_verify_chain_accepts_valid_chain():
    first = _entry(GENESIS_HASH)
    second = _entry(first.entry_hash, tool="wg_interface_restart")

    assert verify_chain([first, second]) is True


def test_verify_chain_detects_tampering():
    first = _entry(GENESIS_HASH)
    second = _entry(first.entry_hash, tool="wg_interface_restart")
    second.status = "failed"

    assert verify_chain([first, second]) is False


def test_verify_chain_detects_broken_link():
    first = _entry(GENESIS_HASH)
    second = _entry("f" * 64, tool="wg_interface_restart")

    assert verify_chain([first, second]) is False


def test_verify_chain_detects_truncation_with_expected_count():
    first = _entry(GENESIS_HASH)
    second = _entry(first.entry_hash, tool="wg_interface_restart")

    assert verify_chain([first, second], expected_count=2) is True
    assert verify_chain([first], expected_count=2) is False


def test_verify_chain_detects_truncation_with_expected_head():
    first = _entry(GENESIS_HASH)
    second = _entry(first.entry_hash, tool="wg_interface_restart")

    assert verify_chain([first, second], expected_head=second.entry_hash) is True
    assert verify_chain([first], expected_head=second.entry_hash) is False


def test_verify_chain_empty_is_true_without_anchor_and_false_with_expected_head():
    assert verify_chain([]) is True
    assert verify_chain([], expected_head="f" * 64) is False


def test_audit_log_prev_hash_is_unique():
    unique = [
        constraint
        for constraint in AuditEntry.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    ]

    assert sorted(
        (constraint.name, [column.name for column in constraint.columns])
        for constraint in unique
    ) == [
        ("uq_audit_log_entry_hash", ["entry_hash"]),
        ("uq_audit_log_prev_hash", ["prev_hash"]),
    ]


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, last=None):
        self._last = last
        self.added = []
        self.committed = False
        self.rolled_back = False
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _FakeResult(self._last)

    def add(self, entry):
        self.added.append(entry)

    async def commit(self):
        self.committed = True

    async def refresh(self, entry):
        return None

    async def rollback(self):
        self.rolled_back = True


@pytest.mark.asyncio
async def test_record_mutation_chains_to_previous():
    prior = _entry(GENESIS_HASH)
    session = _FakeSession(last=prior)

    entry = await _record_mutation(
        session,
        tool="wg_remove_peer",
        arguments={"interface": "wg0"},
        risk="dangerous",
        status="success",
        session_id="sess",
    )

    assert entry is not None
    assert session.committed is True
    assert entry.prev_hash == prior.entry_hash
    assert entry.entry_hash == compute_entry_hash(
        prior.entry_hash,
        entry_payload(
            created_at=entry.created_at,
            actor=entry.actor,
            session_id=entry.session_id,
            plan_id=entry.plan_id,
            revision=entry.revision,
            tool=entry.tool,
            risk=entry.risk,
            arguments=entry.arguments,
            status=entry.status,
            error=entry.error,
        ),
    )


@pytest.mark.asyncio
async def test_record_mutation_uses_genesis_as_first_entry():
    session = _FakeSession(last=None)

    entry = await _record_mutation(session, tool="core_reload_plugin", status="success")

    assert entry is not None
    assert entry.prev_hash == GENESIS_HASH


@pytest.mark.asyncio
async def test_record_mutation_serializes_appends_with_a_chain_lock():
    session = _FakeSession(last=None)

    entry = await _record_mutation(session, tool="ops_write_file", status="success")

    assert entry is not None
    assert len(session.statements) == 2
    assert "pg_advisory_xact_lock" in str(session.statements[0])
    assert "audit_log" in str(session.statements[1])


@pytest.mark.asyncio
async def test_record_mutation_swallows_failures():
    class _Boom:
        async def execute(self, statement):
            raise RuntimeError("database down")

        async def rollback(self):
            return None

    assert await _record_mutation(_Boom(), tool="ops_write_file", status="success") is None
