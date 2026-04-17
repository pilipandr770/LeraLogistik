"""Async database engine and session factory."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    echo=_settings.app_debug,
    pool_pre_ping=True,        # avoid "server closed connection" after idle
    pool_size=5,
    max_overflow=10,
    # Route all unqualified table references to our isolated schema.
    # Generates SQL like `lera_logistics.loads` instead of bare `loads`,
    # so isolation works even when search_path is not set on the connection.
    execution_options={"schema_translate_map": {None: _settings.db_schema}},
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a database session.

    Example usage:

        @router.get("/loads")
        async def list_loads(session: AsyncSession = Depends(get_session)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
