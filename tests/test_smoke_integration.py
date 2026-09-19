"""Integration smoke test: Postgres availability.

Only runs when LOOMCORE_INTEGRATION=1 (otherwise skipped), since it needs a DB.
"""

import os

import pytest
from alembic.config import Config
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from src.config import settings
from src.db.models import Session
from src.db.session import ensure_session

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("LOOMCORE_INTEGRATION") != "1",
        reason="A database is required; set LOOMCORE_INTEGRATION=1",
    ),
]

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="session")
def migrated_database():
    """Bring the test database up to head via Alembic before integration tests."""
    config = Config(os.path.join(_REPO_ROOT, "alembic.ini"))
    command.upgrade(config, "head")
    yield


@pytest.mark.asyncio
async def test_postgres_available():
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text("SELECT 1"))
            assert result.scalar() == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ensure_session_accepts_wide_ids(migrated_database):
    """A 37-char ``sess_<32 hex>`` and a 64-char id must persist (migration 006)."""
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_ids = ["sess_" + "a" * 32, "b" * 64]
    try:
        async with factory() as db:
            for session_id in session_ids:
                assert await ensure_session(db, session_id) == session_id
            await db.execute(delete(Session).where(Session.id.in_(session_ids)))
            await db.commit()
    finally:
        await engine.dispose()
