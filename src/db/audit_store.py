"""Read helpers for the hash-chained audit log (RULES 10)."""

from collections.abc import AsyncIterator

from sqlalchemy import and_, func, or_, select
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


async def latest_entry_hash(session_db: AsyncSession) -> str | None:
    """The ``entry_hash`` of the newest entry, or ``None`` when the log is empty."""
    stmt = (
        select(AuditEntry.entry_hash)
        .order_by(AuditEntry.created_at.desc(), AuditEntry.id.desc())
        .limit(1)
    )
    result = await session_db.execute(stmt)
    return result.scalar_one_or_none()


async def iter_audit_entries(
    session_db: AsyncSession, *, batch_size: int = 1000
) -> AsyncIterator[AuditEntry]:
    """Yield every audit entry in chain order (oldest first), page by page.

    Keyset pagination on ``(created_at, id)`` lets ``GET /api/audit/verify``
    stream the chain without materialising the full ``audit_log`` at once.
    """
    last_created: str | None = None
    last_id: str | None = None
    while True:
        stmt = select(AuditEntry).order_by(
            AuditEntry.created_at.asc(), AuditEntry.id.asc()
        )
        if last_created is not None and last_id is not None:
            stmt = stmt.where(
                or_(
                    AuditEntry.created_at > last_created,
                    and_(
                        AuditEntry.created_at == last_created,
                        AuditEntry.id > last_id,
                    ),
                )
            )
        result = await session_db.execute(stmt.limit(batch_size))
        rows = list(result.scalars().all())
        if not rows:
            return
        for row in rows:
            yield row
        if len(rows) < batch_size:
            return
        last_created = rows[-1].created_at
        last_id = rows[-1].id
