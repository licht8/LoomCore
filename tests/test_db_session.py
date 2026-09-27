"""Unit tests for DB session helpers using a fake AsyncSession."""

from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

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
    def __init__(self, results=None, fail_commit_on=None, commit_error=None):
        self._results = list(results or [])
        self._fail_commit_on = fail_commit_on
        self._commit_error = commit_error or RuntimeError("commit failed")
        self.added = []
        self.commit_count = 0
        self.refreshed = []
        self.rolled_back = 0

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commit_count += 1
        if self._fail_commit_on == self.commit_count:
            raise self._commit_error

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
        commit_error=IntegrityError("INSERT", {}, Exception("duplicate key")),
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
        commit_error=IntegrityError("INSERT", {}, Exception("duplicate key")),
    )

    with pytest.raises(IntegrityError):
        await db_session.ensure_session(db, "sess-4")

    assert db.rolled_back == 1


@pytest.mark.asyncio
async def test_ensure_session_propagates_non_integrity_error():
    db = _FakeSession(
        results=[
            _FakeResult(scalar=SimpleNamespace(id="u1")),
            _FakeResult(scalar=None),
        ],
        fail_commit_on=1,
    )

    with pytest.raises(RuntimeError, match="commit failed"):
        await db_session.ensure_session(db, "sess-5")

    assert db.rolled_back == 0


@pytest.mark.asyncio
async def test_ensure_session_recovers_when_user_insert_races():
    winner = User(id="u-race", identifier="default", provider="web")
    db = _FakeSession(
        results=[
            _FakeResult(scalar=None),
            _FakeResult(scalar=winner),
            _FakeResult(scalar=None),
        ],
        fail_commit_on=1,
        commit_error=IntegrityError("INSERT", {}, Exception("duplicate key")),
    )

    session_id = await db_session.ensure_session(db, "sess-race")

    assert session_id == "sess-race"
    assert db.rolled_back == 1
    assert db.commit_count == 2
    assert any(isinstance(obj, Session) for obj in db.added)


@pytest.mark.asyncio
async def test_ensure_session_reraises_when_user_recovery_finds_nothing():
    db = _FakeSession(
        results=[_FakeResult(scalar=None), _FakeResult(scalar=None)],
        fail_commit_on=1,
        commit_error=IntegrityError("INSERT", {}, Exception("duplicate key")),
    )

    with pytest.raises(IntegrityError):
        await db_session.ensure_session(db, "sess-user-race")

    assert db.rolled_back == 1


@pytest.mark.asyncio
async def test_list_sessions_shapes_rows_with_title_and_preview():
    rows = [
        SimpleNamespace(
            session_id="s1", message_count=2, last_message_at="2026-02-01T00:00:00+00:00"
        )
    ]
    db = _FakeSession(
        results=[
            _FakeResult(scalars=rows),
            _FakeResult(scalars=[("s1", "first user message")]),
            _FakeResult(scalars=[("s1", "latest user message")]),
        ]
    )

    result = await db_session.list_sessions(db, limit=10)

    assert result == [
        {
            "session_id": "s1",
            "title": "first user message",
            "preview": "latest user message",
            "message_count": 2,
            "last_message_at": "2026-02-01T00:00:00+00:00",
        }
    ]


@pytest.mark.asyncio
async def test_list_sessions_empty_returns_early():
    db = _FakeSession(results=[_FakeResult(scalars=[])])

    assert await db_session.list_sessions(db) == []


@pytest.mark.asyncio
async def test_list_sessions_truncates_long_bodies():
    long_body = "x" * 200
    rows = [SimpleNamespace(session_id="s1", message_count=1, last_message_at="t")]
    db = _FakeSession(
        results=[
            _FakeResult(scalars=rows),
            _FakeResult(scalars=[("s1", long_body)]),
            _FakeResult(scalars=[("s1", long_body)]),
        ]
    )

    result = await db_session.list_sessions(db)

    assert len(result[0]["title"]) == 120
    assert len(result[0]["preview"]) == 120


def _plan_row(plan_id, revision, risks, created_at):
    return SimpleNamespace(
        session_id="sess",
        content=f"goal {revision}",
        created_at=created_at,
        tool_calls=[
            {
                "plan_id": plan_id,
                "revision": revision,
                "steps": [{"risk": risk} for risk in risks],
            }
        ],
    )


@pytest.mark.asyncio
async def test_list_pending_plans_dedupes_to_newest_revision():
    newest = _plan_row("pl_1", 2, ["dangerous", "safe"], "2026-02-01T00:00:00+00:00")
    other = _plan_row("pl_2", 1, ["moderate"], "2026-01-15T00:00:00+00:00")
    older = _plan_row("pl_1", 1, ["safe"], "2026-01-01T00:00:00+00:00")
    db = _FakeSession(results=[_FakeResult(scalars=[newest, other, older])])

    result = await db_session.list_pending_plans(db)

    assert [plan["plan_id"] for plan in result] == ["pl_1", "pl_2"]
    assert result[0]["revision"] == 2
    assert result[0]["step_count"] == 2
    assert result[0]["risks"] == ["dangerous", "safe"]
    assert result[0]["goal"] == "goal 2"


@pytest.mark.asyncio
async def test_list_pending_plans_skips_rows_without_plan_id():
    bad = SimpleNamespace(
        session_id="sess", content="x", created_at="t", tool_calls=[{}]
    )
    db = _FakeSession(results=[_FakeResult(scalars=[bad])])

    assert await db_session.list_pending_plans(db) == []
