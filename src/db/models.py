from uuid import uuid4

from sqlalchemy import (
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    identifier: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String, default="web")  # 'web', 'telegram'
    role: Mapped[str] = mapped_column(String, default="user")  # 'admin', 'user'

    sessions: Mapped[list["Session"]] = relationship(
        "Session", back_populates="user", cascade="all, delete-orphan"
    )


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))

    status: Mapped[str] = mapped_column(String, default="active")  # 'active', 'paused'

    user: Mapped["User"] = relationship("User", back_populates="sessions")
    messages: Mapped[list["Message"]] = relationship(
        "Message", back_populates="session", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"))

    role: Mapped[str] = mapped_column(String)  # 'user', 'assistant', 'system', 'tool'
    content: Mapped[str] = mapped_column(Text)
    tool_calls: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    tool_result: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[str] = mapped_column(String, nullable=False)  # UTC isoformat

    session: Mapped["Session"] = relationship("Session", back_populates="messages")


class AuditEntry(Base):
    """Append-only, hash-chained record of every mutation the agent performs.

    Each row commits to the previous row's hash, so removing or editing an
    entry breaks the chain and is detectable. The table has no foreign keys so
    records survive session/message deletion.
    """

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    created_at: Mapped[str] = mapped_column(String, nullable=False)  # UTC isoformat
    actor: Mapped[str] = mapped_column(String, default="admin")
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    plan_id: Mapped[str | None] = mapped_column(String, nullable=True)
    revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tool: Mapped[str] = mapped_column(String, nullable=False)
    risk: Mapped[str | None] = mapped_column(String, nullable=True)
    arguments: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)  # 'success' | 'failed'
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False)
