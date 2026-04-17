"""Alembic environment configuration.

We pull the database URL from the application Settings (which loads .env)
rather than from alembic.ini, so the single source of truth stays app/config.py.

The URL is converted from asyncpg form ('postgresql+asyncpg://...') to
sync psycopg form ('postgresql+psycopg://...') because Alembic's migration
engine is synchronous.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from app.config import get_settings
from app.db.models import Base   # noqa: F401 — import registers all models

config = context.config

if config.config_file_name:
    fileConfig(config.config_file_name)

_settings = get_settings()
_schema = _settings.db_schema
_sync_url = _settings.database_url.replace("+asyncpg", "+psycopg")
config.set_main_option("sqlalchemy.url", _sync_url)

target_metadata = Base.metadata


def _configure_search_path(connection) -> None:
    """Create the schema if it doesn't exist and set search_path for this session."""
    connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{_schema}"'))
    connection.execute(text(f'SET search_path TO "{_schema}"'))


def run_migrations_offline() -> None:
    """Generate SQL without needing a DB connection."""
    context.configure(
        url=_sync_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        version_table_schema=_schema,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _configure_search_path(connection)
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_schemas=True,
            version_table_schema=_schema,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
