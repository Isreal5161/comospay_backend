from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import AsyncSessionFactory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a managed async database session for request-scoped usage."""
    async with AsyncSessionFactory() as session:
        yield session


async def close_session(session: AsyncSession) -> None:
    """Close an existing async database session safely."""
    await session.close()
