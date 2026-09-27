"""Read helpers for the hash-chained audit log (RULES 10)."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import AuditEntry


async def list_audit_entries(
    session_db: AsyncSession, *, limit: int, offset: int
) -> list[AuditEntry]:
    """Read a page of audit entries, newest first."""
    stmt = (
        select(AuditEntry)
        .order_by(AuditEntry.created_at.desc(), AuditEntry.id.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await session_db.execute(stmt)
    return list(result.scalars().all())


async def count_audit_entries(session_db: AsyncSession) -> int:
    """Total number of audit entries."""
    result = await session_db.execute(select(func.count(AuditEntry.id)))
    return int(result.scalar_one())


async def all_audit_entries(session_db: AsyncSession) -> list[AuditEntry]:
    """Every audit entry in chain order (oldest first) for verification."""
    stmt = select(AuditEntry).order_by(
        AuditEntry.created_at.asc(), AuditEntry.id.asc()
    )
    result = await session_db.execute(stmt)
    return list(result.scalars().all())
