"""Интеграционный smoke-тест: доступность Postgres.

Запускается только при LOOMHUB_INTEGRATION=1 (иначе skip), т.к. требует БД.
"""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import settings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("LOOMHUB_INTEGRATION") != "1",
        reason="Требуется БД; задайте LOOMHUB_INTEGRATION=1",
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
