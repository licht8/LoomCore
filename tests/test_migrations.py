"""Regression tests for Alembic migration schema definitions."""

import importlib.util
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

_VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"


class _CreateTableRecorder:
    def __init__(self) -> None:
        self.tables: list[tuple[str, tuple[Any, ...]]] = []

    def create_table(self, name: str, *args: Any) -> None:
        self.tables.append((name, args))


def _render_ddl(filename: str) -> dict[str, str]:
    path = _VERSIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    recorder = _CreateTableRecorder()
    module.op = recorder
    module.upgrade()

    metadata = sa.MetaData()
    for name, args in recorder.tables:
        sa.Table(name, metadata, *args)

    return {
        name: str(
            CreateTable(metadata.tables[name]).compile(dialect=postgresql.dialect())
        )
        for name, _ in recorder.tables
    }


def test_init_migration_declares_single_foreign_key_per_table():
    ddl = _render_ddl("001_init.py")

    assert ddl["sessions"].count("FOREIGN KEY") == 1
    assert ddl["messages"].count("FOREIGN KEY") == 1
    assert "FOREIGN KEY(user_id) REFERENCES users (id)" in ddl["sessions"]
    assert "FOREIGN KEY(session_id) REFERENCES sessions (id)" in ddl["messages"]
