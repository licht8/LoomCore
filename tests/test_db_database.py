"""Tests for the database session dependency typing."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from src.db import database


def test_get_db_is_annotated_async_generator():
    assert (
        database.get_db.__annotations__["return"]
        == AsyncGenerator[AsyncSession, None]
    )
