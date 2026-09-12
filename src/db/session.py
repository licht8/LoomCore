from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Message, Session, User


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
    message = Message(
        id=str(uuid4()),
        session_id=session_id,
        role="user",
        content=content,
        created_at=datetime.now(UTC).isoformat(),
    )
    session_db.add(message)
    await session_db.commit()
    await session_db.refresh(message)
    return message


async def add_assistant_message(
    session_db: AsyncSession,
    session_id: str,
    content: str,
    tool_calls: list | None = None,
) -> Message:
    """Append an assistant message to the history."""
    message = Message(
        id=str(uuid4()),
        session_id=session_id,
        role="assistant",
        content=content,
        tool_calls=tool_calls,
        created_at=datetime.now(UTC).isoformat(),
    )
    session_db.add(message)
    await session_db.commit()
    await session_db.refresh(message)
    return message


async def ensure_session(
    session_db: AsyncSession,
    session_id: str,
    user_identifier: str = "default",
) -> str:
    """Ensure the session exists.

    1. Find the user by identifier (or create them).
    2. Find the session by id (or create it).
    3. Return session_id.

    If the session already exists — return its id unchanged.
    If the user does not exist — create them with default parameters.
    """
    # 1. Find or create the user
    result = await session_db.execute(
        select(User).where(User.identifier == user_identifier)
    )
    user = result.scalar_one_or_none()

    if user is None:
        user = User(
            id=str(uuid4()),
            identifier=user_identifier,
            provider="web",
            role="user",
        )
        session_db.add(user)
        await session_db.commit()
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
        except Exception:
            await session_db.rollback()
            # The session already exists — return it
            session_result = await session_db.execute(
                select(Session).where(Session.id == session_id)
            )
            session = session_result.scalar_one_or_none()

    return session.id


async def get_session_history(
    session_db: AsyncSession,
    session_id: str,
    limit: int = 50,
) -> list[Message]:
    """Read the last N messages of a session."""
    stmt = (
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at)
        .limit(limit)
    )
    result = await session_db.execute(stmt)
    return list(result.scalars().all())
