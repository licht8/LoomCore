from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config import settings

# Disable both prepared-statement caches. A long-running hub pools asyncpg
# connections; if Alembic alters a table while such a connection holds a plan
# for it, Postgres rejects re-execution with InvalidCachedStatementError
# (SQLSTATE 0A000) and the request fails. statement_cache_size is asyncpg's
# cache, prepared_statement_cache_size is SQLAlchemy's; disabling both means
# every execution prepares fresh, so no stale plan can be reused. The extra
# preparation cost is negligible for this workload.
ENGINE_CONNECT_ARGS = {"statement_cache_size": 0, "prepared_statement_cache_size": 0}

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    connect_args=dict(ENGINE_CONNECT_ARGS),
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency returning an async DB session."""
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()
