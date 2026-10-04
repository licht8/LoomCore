from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import LlmUsage, Message, Session, User


async def add_message(
    session_db: AsyncSession,
    session_id: str,
    role: str,
    content: str,
    tool_calls: list | None = None,
    tool_result: str | None = None,
) -> Message:
    """Append a message to the session history."""
    now = datetime.now(UTC).isoformat()
    message = Message(
        id=str(uuid4()),
        session_id=session_id,
        role=role,
        content=content,
        tool_calls=tool_calls,
        tool_result=tool_result,
        created_at=now,
    )
    session_db.add(message)
    await _touch_session(
        session_db, session_id, now, title=content if role == "user" else None
    )
    await session_db.commit()
    await session_db.refresh(message)
    return message


async def _touch_session(
    session_db: AsyncSession,
    session_id: str,
    now: str,
    *,
    title: str | None = None,
) -> None:
    """Bump ``updated_at``; set ``title`` once, from the first user message."""
    values: dict = {"updated_at": now}
    if title is not None:
        values["title"] = func.coalesce(func.nullif(Session.title, ""), _truncate(title))
    await session_db.execute(
        update(Session).where(Session.id == session_id).values(**values)
    )


async def add_user_message(
    session_db: AsyncSession,
    session_id: str,
    content: str,
) -> Message:
    """Append a user message to the history."""
    return await add_message(session_db, session_id, "user", content)


async def add_assistant_message(
    session_db: AsyncSession,
    session_id: str,
    content: str,
    tool_calls: list | None = None,
) -> Message:
    """Append an assistant message to the history."""
    return await add_message(
        session_db, session_id, "assistant", content, tool_calls=tool_calls
    )


async def add_plan_message(
    session_db: AsyncSession,
    session_id: str,
    plan: dict,
) -> Message:
    """Append an agent plan to the history (role "plan", stored in tool_calls)."""
    return await add_message(
        session_db,
        session_id,
        "plan",
        str(plan.get("goal") or ""),
        tool_calls=[plan],
    )


async def set_pending(
    session_db: AsyncSession,
    session_id: str,
    plan: dict,
    user_text: str,
) -> None:
    """Persist the plan awaiting the user's textual approval.

    Overwriting an existing record is what makes a newer proposal *supersede*
    the earlier one — the old ``plan_id`` can no longer be claimed.
    """
    record = {
        "plan": plan,
        "user_text": user_text,
        "created_at": datetime.now(UTC).isoformat(),
    }
    await session_db.execute(
        update(Session).where(Session.id == session_id).values(pending=record)
    )
    await session_db.commit()


async def get_pending(session_db: AsyncSession, session_id: str) -> dict | None:
    """Read the persisted pending plan for a session, if any."""
    result = await session_db.execute(
        select(Session.pending).where(Session.id == session_id)
    )
    return result.scalar_one_or_none()


async def claim_pending(
    session_db: AsyncSession,
    session_id: str,
    plan_id: str,
) -> dict | None:
    """Atomically consume the pending record that matches ``plan_id``.

    The row is locked (``SELECT ... FOR UPDATE``) and then NULLed, so two
    connections (or a retry) cannot both claim the same plan. Returns the
    claimed record, or ``None`` when it was already consumed or superseded.
    """
    matched = (
        await session_db.execute(
            select(Session.pending)
            .where(
                Session.id == session_id,
                Session.pending["plan"]["plan_id"].as_string() == plan_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if matched is None:
        return None
    await session_db.execute(
        update(Session).where(Session.id == session_id).values(pending=None)
    )
    await session_db.commit()
    return matched


async def replace_pending(
    session_db: AsyncSession,
    session_id: str,
    plan_id: str,
    plan: dict,
    user_text: str,
) -> bool:
    """Atomically replace the pending plan with a new revision.

    Only the record whose ``plan_id`` still matches is replaced, so an edit
    cannot resurrect a plan that was already consumed or superseded. Returns
    ``True`` when the row was replaced.
    """
    record = {
        "plan": plan,
        "user_text": user_text,
        "created_at": datetime.now(UTC).isoformat(),
    }
    result = await session_db.execute(
        update(Session)
        .where(
            Session.id == session_id,
            Session.pending["plan"]["plan_id"].as_string() == plan_id,
        )
        .values(pending=record)
    )
    await session_db.commit()
    return result.rowcount == 1


async def clear_pending(session_db: AsyncSession, session_id: str) -> None:
    """Drop the persisted pending plan for a session."""
    await session_db.execute(
        update(Session).where(Session.id == session_id).values(pending=None)
    )
    await session_db.commit()


async def set_memory(
    session_db: AsyncSession,
    session_id: str,
    text: str,
    updated_at: str,
) -> None:
    """Store the compacted conversation memory and its coverage marker."""
    await session_db.execute(
        update(Session)
        .where(Session.id == session_id)
        .values(memory=text, memory_updated_at=updated_at)
    )
    await session_db.commit()


async def get_memory(
    session_db: AsyncSession,
    session_id: str,
) -> tuple[str | None, str | None]:
    """Read ``(memory, memory_updated_at)`` for a session.

    Returns ``(None, None)`` when the session is unknown or has no memory yet.
    """
    result = await session_db.execute(
        select(Session.memory, Session.memory_updated_at).where(
            Session.id == session_id
        )
    )
    row = result.one_or_none()
    if row is None:
        return (None, None)
    return (row.memory, row.memory_updated_at)


async def ensure_session(
    session_db: AsyncSession,
    session_id: str,
    user_identifier: str = "default",
    provider: str = "web",
) -> str:
    """Ensure the session exists.

    1. Find the user by ``(provider, identifier)`` (or create them).
    2. Find the session by id (or create it).
    3. Return session_id.

    Identity is scoped to ``provider`` so a web identity and a Telegram identity
    that share the same string do not collide. If the session already exists —
    return its id unchanged. If the user does not exist — create them with
    default parameters; a concurrent insert that wins the race is recovered by
    re-reading after the rollback.
    """
    # 1. Find or create the user
    result = await session_db.execute(
        select(User).where(
            User.provider == provider,
            User.identifier == user_identifier,
        )
    )
    user = result.scalar_one_or_none()

    if user is None:
        user = User(
            id=str(uuid4()),
            identifier=user_identifier,
            provider=provider,
            role="user",
        )
        session_db.add(user)
        try:
            await session_db.commit()
        except IntegrityError:
            await session_db.rollback()
            # A concurrent insert won the race — re-read the winning row.
            result = await session_db.execute(
                select(User).where(
                    User.provider == provider,
                    User.identifier == user_identifier,
                )
            )
            user = result.scalar_one_or_none()
            if user is None:
                # Not a duplicate-key race: surface the original failure.
                raise
        else:
            await session_db.refresh(user)

    # 2. Find or create the session
    session_result = await session_db.execute(
        select(Session).where(Session.id == session_id)
    )
    session = session_result.scalar_one_or_none()

    if session is None:
        try:
            now = datetime.now(UTC).isoformat()
            session = Session(
                id=session_id,
                user_id=user.id,
                status="active",
                created_at=now,
                updated_at=now,
            )
            session_db.add(session)
            await session_db.commit()
        except IntegrityError:
            await session_db.rollback()
            # The session may have been created concurrently — re-read it.
            session_result = await session_db.execute(
                select(Session).where(Session.id == session_id)
            )
            session = session_result.scalar_one_or_none()
            if session is None:
                # Not a duplicate-key race: surface the original failure.
                raise

    return session.id


async def get_session_history(
    session_db: AsyncSession,
    session_id: str,
    limit: int = 50,
) -> list[Message]:
    """Read the newest ``limit`` messages of a session (oldest first).

    We sort descending to keep the most recent turns, then reverse so callers
    receive them in chronological order.
    """
    stmt = (
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    result = await session_db.execute(stmt)
    return list(reversed(list(result.scalars().all())))


async def clear_session_history(
    session_db: AsyncSession,
    session_id: str,
) -> int:
    """Delete every message in a session; return the number removed."""
    result = await session_db.execute(
        delete(Message).where(Message.session_id == session_id)
    )
    await session_db.execute(
        update(Session)
        .where(Session.id == session_id)
        .values(updated_at=datetime.now(UTC).isoformat())
    )
    await session_db.commit()
    return result.rowcount or 0


def _truncate(value: str | None, limit: int = 120) -> str:
    """Clamp a message body to a one-line-ish preview."""
    text = value or ""
    return text if len(text) <= limit else text[:limit]


async def list_sessions(session_db: AsyncSession, limit: int = 100) -> list[dict]:
    """List sessions that have at least one user message (newest activity first).

    ``title`` comes from ``sessions.title`` (set from the first user message) and
    falls back to the last user body; ``preview`` is the last user message. Both
    are clamped to 120 chars; ``message_count`` counts user messages. Ordering is
    by ``sessions.updated_at``, so any activity (not just user turns) moves a
    conversation up. The last user body is picked with a window function instead
    of a timestamp-equality join, so equal ``created_at`` values cannot drop rows.
    """
    counts = (
        select(
            Message.session_id.label("session_id"),
            func.count().label("message_count"),
        )
        .where(Message.role == "user")
        .group_by(Message.session_id)
        .subquery()
    )
    last_user = (
        select(
            Message.session_id.label("session_id"),
            Message.content.label("content"),
            Message.created_at.label("created_at"),
            func.row_number()
            .over(
                partition_by=Message.session_id,
                order_by=(Message.created_at.desc(), Message.id.desc()),
            )
            .label("rn"),
        )
        .where(Message.role == "user")
        .subquery()
    )
    stmt = (
        select(
            Session.id.label("session_id"),
            Session.title.label("title"),
            Session.updated_at.label("updated_at"),
            counts.c.message_count,
            last_user.c.content.label("preview"),
            last_user.c.created_at.label("last_message_at"),
        )
        .join(counts, counts.c.session_id == Session.id)
        .join(
            last_user,
            and_(last_user.c.session_id == Session.id, last_user.c.rn == 1),
        )
        .order_by(Session.updated_at.desc())
        .limit(limit)
    )
    rows = (await session_db.execute(stmt)).all()
    return [
        {
            "session_id": row.session_id,
            "title": _truncate(row.title) or _truncate(row.preview),
            "preview": _truncate(row.preview),
            "message_count": row.message_count,
            "last_message_at": row.last_message_at,
        }
        for row in rows
    ]


async def list_pending_plans(session_db: AsyncSession, limit: int = 100) -> list[dict]:
    """List plans awaiting a decision, one pending plan per session, newest first.

    The authority is the single ``sessions.pending`` slot (the same record the
    WebSocket reads, claims and replaces), so a superseded plan is not listed —
    a newer ``set_pending`` overwrote its slot. A pending record without a
    ``plan_id`` is skipped.
    """
    stmt = (
        select(Session.id, Session.pending)
        .where(Session.pending.is_not(None))
        .order_by(Session.pending["created_at"].as_string().desc())
        .limit(limit)
    )
    rows = (await session_db.execute(stmt)).all()

    pending: list[dict] = []
    for row in rows:
        record = row.pending or {}
        plan = record.get("plan") or {}
        plan_id = plan.get("plan_id")
        if not plan_id:
            continue
        steps = plan.get("steps") or []
        pending.append(
            {
                "plan_id": plan_id,
                "session_id": row.id,
                "goal": plan.get("goal") or "",
                "revision": plan.get("revision"),
                "step_count": len(steps),
                "risks": sorted(
                    {step.get("risk") for step in steps if step.get("risk")}
                ),
                "created_at": record.get("created_at"),
            }
        )
    return pending


async def add_llm_usage(
    session_db: AsyncSession,
    *,
    provider: str,
    base_url: str,
    model: str,
    purpose: str,
    session_id: str | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    total_tokens: int | None,
    latency_ms: int | None,
    attempts: int,
    status: str,
    error: str | None = None,
) -> LlmUsage:
    """Append one LLM usage row. Best-effort: callers swallow any failure."""
    row = LlmUsage(
        id=str(uuid4()),
        created_at=datetime.now(UTC).isoformat(),
        provider=provider,
        base_url=base_url,
        model=model,
        purpose=purpose,
        session_id=session_id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        latency_ms=latency_ms,
        attempts=attempts,
        status=status,
        error=error,
    )
    session_db.add(row)
    await session_db.commit()
    await session_db.refresh(row)
    return row


async def llm_usage_totals(session_db: AsyncSession, since_iso: str) -> dict:
    """Aggregate usage for every row at/after ``since_iso`` (raw sums)."""
    stmt = select(
        func.count(LlmUsage.id),
        func.sum(LlmUsage.prompt_tokens),
        func.sum(LlmUsage.completion_tokens),
        func.sum(LlmUsage.total_tokens),
        func.avg(LlmUsage.latency_ms),
    ).where(LlmUsage.created_at >= since_iso)
    row = (await session_db.execute(stmt)).one()
    return {
        "calls": row[0],
        "prompt_tokens": row[1],
        "completion_tokens": row[2],
        "total_tokens": row[3],
        "avg_latency_ms": row[4],
    }


async def llm_usage_by_model(session_db: AsyncSession, since_iso: str) -> list[dict]:
    """Aggregate usage per (provider, model) at/after ``since_iso``."""
    calls = func.count(LlmUsage.id)
    stmt = (
        select(
            LlmUsage.provider,
            LlmUsage.model,
            calls,
            func.sum(LlmUsage.prompt_tokens),
            func.sum(LlmUsage.completion_tokens),
            func.sum(LlmUsage.total_tokens),
            func.avg(LlmUsage.latency_ms),
        )
        .where(LlmUsage.created_at >= since_iso)
        .group_by(LlmUsage.provider, LlmUsage.model)
        .order_by(calls.desc(), LlmUsage.model)
    )
    rows = (await session_db.execute(stmt)).all()
    return [
        {
            "provider": row[0],
            "model": row[1],
            "calls": row[2],
            "prompt_tokens": row[3],
            "completion_tokens": row[4],
            "total_tokens": row[5],
            "avg_latency_ms": row[6],
        }
        for row in rows
    ]


async def llm_usage_by_day(session_db: AsyncSession, since_iso: str) -> list[dict]:
    """Aggregate usage per calendar day (UTC) at/after ``since_iso``."""
    day = func.substr(LlmUsage.created_at, 1, 10).label("day")
    stmt = (
        select(
            day,
            func.count(LlmUsage.id),
            func.sum(LlmUsage.prompt_tokens),
            func.sum(LlmUsage.completion_tokens),
            func.sum(LlmUsage.total_tokens),
        )
        .where(LlmUsage.created_at >= since_iso)
        .group_by(day)
        .order_by(day)
    )
    rows = (await session_db.execute(stmt)).all()
    return [
        {
            "day": row[0],
            "calls": row[1],
            "prompt_tokens": row[2],
            "completion_tokens": row[3],
            "total_tokens": row[4],
        }
        for row in rows
    ]
