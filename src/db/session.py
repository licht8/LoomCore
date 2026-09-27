from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import and_, delete, func, select
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
    message = Message(
        id=str(uuid4()),
        session_id=session_id,
        role=role,
        content=content,
        tool_calls=tool_calls,
        tool_result=tool_result,
        created_at=datetime.now(UTC).isoformat(),
    )
    session_db.add(message)
    await session_db.commit()
    await session_db.refresh(message)
    return message


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
            session = Session(
                id=session_id,
                user_id=user.id,
                status="active",
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
    await session_db.commit()
    return result.rowcount or 0


def _truncate(value: str | None, limit: int = 120) -> str:
    """Clamp a message body to a one-line-ish preview."""
    text = value or ""
    return text if len(text) <= limit else text[:limit]


async def list_sessions(session_db: AsyncSession, limit: int = 100) -> list[dict]:
    """List sessions that have at least one user message (newest first).

    ``title`` is the first user message and ``preview`` the last, each clamped
    to 120 chars; ``message_count`` counts the user messages. The aggregate is
    bounded by ``limit`` sessions; the first/last bodies are fetched with two
    small aggregate joins (no per-session round trip).
    """
    aggregate = (
        select(
            Message.session_id.label("session_id"),
            func.count().label("message_count"),
            func.max(Message.created_at).label("last_message_at"),
        )
        .where(Message.role == "user")
        .group_by(Message.session_id)
        .order_by(func.max(Message.created_at).desc())
        .limit(limit)
    )
    rows = (await session_db.execute(aggregate)).all()
    if not rows:
        return []

    session_ids = [row.session_id for row in rows]
    bounds = (
        select(
            Message.session_id.label("sid"),
            func.min(Message.created_at).label("first_at"),
            func.max(Message.created_at).label("last_at"),
        )
        .where(Message.role == "user", Message.session_id.in_(session_ids))
        .group_by(Message.session_id)
        .subquery()
    )

    async def _bodies(column) -> dict[str, str]:
        result = await session_db.execute(
            select(Message.session_id, Message.content)
            .join(
                bounds,
                and_(
                    Message.session_id == bounds.c.sid,
                    Message.created_at == column,
                ),
            )
            .where(Message.role == "user")
        )
        return {session_id: content for session_id, content in result.all()}

    titles = await _bodies(bounds.c.first_at)
    previews = await _bodies(bounds.c.last_at)

    return [
        {
            "session_id": row.session_id,
            "title": _truncate(titles.get(row.session_id)),
            "preview": _truncate(previews.get(row.session_id)),
            "message_count": row.message_count,
            "last_message_at": row.last_message_at,
        }
        for row in rows
    ]


async def list_pending_plans(session_db: AsyncSession, limit: int = 100) -> list[dict]:
    """List plans awaiting approval, newest revision per ``plan_id`` first.

    Considers only ``role="plan"`` rows whose stored JSONB status is
    ``"proposed"`` (Postgres JSONB operators; the project targets Postgres).
    A plan re-asked into a new revision leaves older ``proposed`` rows behind,
    so rows are ordered newest-first and deduped by ``plan_id``.
    """
    status_prop = Message.tool_calls[0]["status"]
    stmt = (
        select(Message)
        .where(Message.role == "plan", status_prop.as_string() == "proposed")
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    rows = (await session_db.execute(stmt)).scalars().all()

    seen: set[str] = set()
    pending: list[dict] = []
    for row in rows:
        plan = (row.tool_calls or [{}])[0]
        plan_id = plan.get("plan_id")
        if not plan_id or plan_id in seen:
            continue
        seen.add(plan_id)
        steps = plan.get("steps") or []
        pending.append(
            {
                "plan_id": plan_id,
                "session_id": row.session_id,
                "goal": row.content,
                "revision": plan.get("revision"),
                "step_count": len(steps),
                "risks": sorted(
                    {step.get("risk") for step in steps if step.get("risk")}
                ),
                "created_at": row.created_at,
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
