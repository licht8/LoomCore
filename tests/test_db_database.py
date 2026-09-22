"""Tests for the async engine's prepared-statement hardening and session typing."""

import importlib
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db import database as database_module

_EXPECTED_CONNECT_ARGS = {
    "statement_cache_size": 0,
    "prepared_statement_cache_size": 0,
}


def test_get_db_is_annotated_async_generator():
    assert (
        database_module.get_db.__annotations__["return"]
        == AsyncGenerator[AsyncSession, None]
    )


def test_engine_connect_args_constant():
    assert database_module.ENGINE_CONNECT_ARGS == _EXPECTED_CONNECT_ARGS


def test_engine_disables_prepared_statement_caches(monkeypatch):
    captured: dict = {}

    def fake_create_async_engine(*args, **kwargs):
        captured["url"] = args[0] if args else kwargs.get("url")
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(
        "sqlalchemy.ext.asyncio.create_async_engine", fake_create_async_engine
    )
    try:
        reloaded = importlib.reload(database_module)
        assert reloaded.ENGINE_CONNECT_ARGS == _EXPECTED_CONNECT_ARGS
        assert captured["url"] == settings.database_url
        assert captured["echo"] == settings.debug
        assert captured["connect_args"] == _EXPECTED_CONNECT_ARGS
    finally:
        monkeypatch.undo()
        importlib.reload(database_module)
