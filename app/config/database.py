from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config.settings import settings


def _build_async_database_url() -> str:
    """Normalize the configured database URL to use asyncpg for async SQLAlchemy access."""
    database_url = settings.database_url
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if database_url.startswith("postgresql+psycopg://"):
        return database_url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
    return database_url


# Engine: reusable async SQLAlchemy engine created once for the application lifecycle.
engine: AsyncEngine = create_async_engine(
    _build_async_database_url(),
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout,
    pool_recycle=settings.db_pool_recycle,
    pool_pre_ping=True,
    future=True,
    echo=settings.debug,
)


# Session Factory: async session factory used to create per-request database sessions.
AsyncSessionFactory = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session for the duration of a request and close it automatically."""
    async with AsyncSessionFactory() as session:
        yield session
