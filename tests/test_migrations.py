"""Regression tests for Alembic migration schema definitions."""

import importlib.util
from pathlib import Path
from typing import Any

import pytest
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


class _FakeResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[Any, ...]]:
        return self._rows


class _FakeBind:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows
        self.statements: list[str] = []

    def execute(self, statement: Any) -> _FakeResult:
        self.statements.append(str(statement))
        return _FakeResult(self._rows)


class _ConstraintRecorder:
    def __init__(self, duplicates: list[tuple[Any, ...]] | None = None) -> None:
        self.constraints: list[tuple[str, str, list[str]]] = []
        self._bind = _FakeBind(duplicates or [])

    def get_bind(self) -> _FakeBind:
        return self._bind

    def create_unique_constraint(
        self, name: str, table_name: str, columns: list[str]
    ) -> None:
        self.constraints.append((name, table_name, columns))


def _load_migration(filename: str) -> Any:
    path = _VERSIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_audit_prev_hash_migration_creates_the_named_constraint():
    module = _load_migration("003_audit_prev_hash_unique.py")
    recorder = _ConstraintRecorder()
    module.op = recorder

    module.upgrade()

    assert recorder.constraints == [
        ("uq_audit_log_prev_hash", "audit_log", ["prev_hash"])
    ]
    statements = recorder.get_bind().statements
    assert len(statements) == 2
    assert "pg_advisory_xact_lock" in statements[0]
    assert "GROUP BY prev_hash" in statements[1]


def test_audit_prev_hash_migration_refuses_a_forked_chain():
    module = _load_migration("003_audit_prev_hash_unique.py")
    recorder = _ConstraintRecorder(duplicates=[("a" * 64,)])
    module.op = recorder

    with pytest.raises(RuntimeError, match="forked chain"):
        module.upgrade()

    assert recorder.constraints == []
