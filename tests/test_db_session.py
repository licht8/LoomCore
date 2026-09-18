"""Unit tests for DB session helpers using a fake AsyncSession."""

from types import SimpleNamespace

import pytest

from src.db import session as db_session
from src.db.models import Message, Session, User


class _FakeResult:
    def __init__(self, *, scalar=None, scalars=None, rowcount=0):
        self._scalar = scalar
        self._scalars = scalars or []
        self.rowcount = rowcount

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return self._scalars


class _FakeSession:
    def __init__(self, results=None, fail_commit_on=None):
        self._results = list(results or [])
        self._fail_commit_on = fail_commit_on
        self.added = []
        self.commit_count = 0
        self.refreshed = []
        self.rolled_back = 0

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commit_count += 1
        if self._fail_commit_on == self.commit_count:
            raise RuntimeError("commit failed")

    async def rollback(self):
        self.rolled_back += 1

    async def refresh(self, obj):
        self.refreshed.append(obj)

    async def execute(self, stmt):
        return self._results.pop(0)


@pytest.mark.asyncio
async def test_add_message_shapes_row():
    db = _FakeSession()

    message = await db_session.add_message(db, "sess", "user", "hi", tool_calls=[{"a": 1}])

    assert message.role == "user"
    assert message.content == "hi"
    assert message.session_id == "sess"
    assert message.tool_calls == [{"a": 1}]
    assert message.created_at
    assert db.added == [message]
    assert db.refreshed == [message]


@pytest.mark.asyncio
async def test_add_user_and_assistant_helpers():
    db = _FakeSession()

    user_msg = await db_session.add_user_message(db, "sess", "hello")
    assistant_msg = await db_session.add_assistant_message(db, "sess", "world", ["tc"])

    assert user_msg.role == "user"
    assert assistant_msg.role == "assistant"
    assert assistant_msg.tool_calls == ["tc"]


@pytest.mark.asyncio
async def test_get_session_history_reverses_to_chronological():
    rows = [Message(role="assistant"), Message(role="user"), Message(role="user")]
    db = _FakeSession(results=[_FakeResult(scalars=rows)])

    result = await db_session.get_session_history(db, "sess", limit=50)

    assert result == list(reversed(rows))


@pytest.mark.asyncio
async def test_clear_session_history_returns_rowcount():
    db = _FakeSession(results=[_FakeResult(rowcount=3)])

    removed = await db_session.clear_session_history(db, "sess")

    assert removed == 3


@pytest.mark.asyncio
async def test_clear_session_history_handles_none_rowcount():
    db = _FakeSession(results=[_FakeResult(rowcount=None)])

    assert await db_session.clear_session_history(db, "sess") == 0


@pytest.mark.asyncio
async def test_ensure_session_creates_user_and_session():
    db = _FakeSession(results=[_FakeResult(scalar=None), _FakeResult(scalar=None)])

    session_id = await db_session.ensure_session(db, "sess-1")

    assert session_id == "sess-1"
    assert any(isinstance(obj, User) for obj in db.added)
    assert any(isinstance(obj, Session) for obj in db.added)


@pytest.mark.asyncio
async def test_ensure_session_reuses_existing():
    user = User(id="u1", identifier="default")
    existing = Session(id="sess-2", user_id="u1")
    db = _FakeSession(results=[_FakeResult(scalar=user), _FakeResult(scalar=existing)])

    session_id = await db_session.ensure_session(db, "sess-2")

    assert session_id == "sess-2"
    assert db.added == []


@pytest.mark.asyncio
async def test_ensure_session_recovers_from_insert_race():
    existing = Session(id="sess-3", user_id="u1")
    db = _FakeSession(
        results=[
            _FakeResult(scalar=SimpleNamespace(id="u1")),
            _FakeResult(scalar=None),
            _FakeResult(scalar=existing),
        ],
        fail_commit_on=1,
    )

    session_id = await db_session.ensure_session(db, "sess-3")

    assert session_id == "sess-3"
    assert db.rolled_back == 1


@pytest.mark.asyncio
async def test_ensure_session_reraises_when_recovery_finds_no_row():
    db = _FakeSession(
        results=[
            _FakeResult(scalar=SimpleNamespace(id="u1")),
            _FakeResult(scalar=None),
            _FakeResult(scalar=None),
        ],
        fail_commit_on=1,
    )

    with pytest.raises(RuntimeError, match="commit failed"):
        await db_session.ensure_session(db, "sess-4")

    assert db.rolled_back == 1
