import asyncio
import logging
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from src.db import models

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Created after fileConfig(), which disables pre-existing loggers.
logger = logging.getLogger(__name__)

# Take DATABASE_URL from settings (.env via pydantic-settings) or the environment.
# configparser applies '%' interpolation when options are read back, so escape
# literal '%' (e.g. percent-encoded credentials) before storing the URL.
try:
    from src.config import Settings

    config.set_main_option("sqlalchemy.url", Settings().database_url.replace("%", "%%"))
except Exception as exc:
    logger.warning("Could not load settings, falling back to DATABASE_URL: %s", exc)
    _database_url = os.environ.get("DATABASE_URL")
    if not _database_url:
        raise RuntimeError(
            "Could not determine the database URL: importing src.config.Settings "
            "failed and DATABASE_URL is not set"
        ) from exc
    config.set_main_option("sqlalchemy.url", _database_url.replace("%", "%%"))

# Set target_metadata to SQLAlchemy's Base.metadata
# so Alembic can see all tables and autogenerate migrations.
target_metadata = models.Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a DBAPI)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    """Create an async engine and run migrations on its connection."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    try:
        async with connectable.connect() as connection:
            await connection.run_sync(_do_run_migrations)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against an async engine."""
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
