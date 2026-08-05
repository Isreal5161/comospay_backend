from __future__ import annotations

import logging
from typing import Optional

from redis.asyncio import ConnectionPool, Redis

from app.config.settings import settings

logger = logging.getLogger(__name__)


# Redis client: reusable async client shared across the application lifecycle.
redis_client: Optional[Redis] = None
redis_pool: Optional[ConnectionPool] = None
redis_cache_ttl: int = settings.redis_cache_ttl


async def connect_redis() -> Redis:
    """Create and initialize the Redis client if it is not already connected."""
    global redis_client, redis_pool

    if redis_client is not None:
        return redis_client

    if not settings.redis_url:
        raise RuntimeError("REDIS_URL is not configured")

    try:
        redis_pool = ConnectionPool.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=200,
            socket_connect_timeout=settings.connection_timeout,
            socket_timeout=settings.read_timeout,
            health_check_interval=30,
            retry_on_timeout=True,
        )
        redis_client = Redis(connection_pool=redis_pool, decode_responses=True)
        await redis_client.ping()
        logger.info("Redis connection established")
        return redis_client
    except Exception as exc:
        logger.exception("Failed to connect to Redis: %s", exc)
        raise


async def disconnect_redis() -> None:
    """Close the Redis connection gracefully when the application shuts down."""
    global redis_client, redis_pool

    if redis_client is None:
        return

    try:
        await redis_client.close()
        logger.info("Redis connection closed")
    except Exception as exc:
        logger.exception("Failed to close Redis connection: %s", exc)
    finally:
        redis_client = None
        redis_pool = None


async def get_redis() -> Redis:
    """Return the shared Redis client, initializing it on first use when needed."""
    if redis_client is None:
        return await connect_redis()
    return redis_client
