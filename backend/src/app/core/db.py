"""Async SQLAlchemy engine + session factory.

Everything is async end-to-end (asyncpg). A request gets one session via the
``get_db`` FastAPI dependency; the session is committed on success and rolled
back on error, then always closed.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=1800,
    echo=False,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: a transactional session per request."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def session_scope() -> AsyncSession:
    """A standalone session for non-request contexts (e.g. the Celery worker).

    Caller is responsible for commit/rollback/close (use ``async with``).
    """
    return SessionLocal()
