"""Integration smoke test: Postgres availability.

Only runs when LOOMCORE_INTEGRATION=1 (otherwise skipped), since it needs a DB.
"""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import settings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("LOOMCORE_INTEGRATION") != "1",
        reason="A database is required; set LOOMCORE_INTEGRATION=1",
    ),
]


@pytest.mark.asyncio
async def test_postgres_available():
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text("SELECT 1"))
            assert result.scalar() == 1
    finally:
        await engine.dispose()
