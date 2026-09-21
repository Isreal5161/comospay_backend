from __future__ import annotations

import logging
from typing import Any

from app.config.redis import get_redis
from app.config.settings import settings
from app.utils.exceptions import ValidationException


class IdempotencyService:
    """Redis-backed idempotency for webhook events.

    Stores a per-provider event key using NX semantics to ensure only the
    first processor accepts the event. Releases mark completion but do not
    remove the historical key immediately to prevent replay.
    """

    PREFIX = "webhook:idempotency"

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)

    def _cache_key(self, provider_name: str, event_id: str) -> str:
        return f"{self.PREFIX}:{provider_name.lower()}:{event_id}"

    def _ttl(self) -> int:
        value = getattr(settings, "idempotency_ttl_seconds", None)
        if value is None:
            value = getattr(settings, "redis_cache_ttl", 86400)
        return int(value)

    def _fail_open(self) -> bool:
        value = getattr(settings, "idempotency_fail_open", None)
        return True if value is None else bool(value)

    async def check_idempotency(self, *, event_id: str | None, provider_name: str | None) -> bool:
        if not event_id:
            return True
        if not provider_name:
            raise ValidationException("Provider name is required for idempotency checks.")

        try:
            redis_client = await get_redis()
        except Exception as exc:
            self.logger.warning("idempotency_redis_unavailable", exc_info=exc)
            return self._fail_open()

        key = self._cache_key(provider_name, event_id)
        try:
            # Try to set a processing marker with NX. If it already exists, it's a duplicate.
            was_set = await redis_client.set(name=key, value="processing", nx=True, ex=self._ttl())
            return bool(was_set)
        except Exception as exc:
            self.logger.warning("idempotency_redis_error", exc_info=exc)
            return self._fail_open()

    async def acquire_lock(self, *, event_id: str | None, provider_name: str | None) -> bool:
        return await self.check_idempotency(event_id=event_id, provider_name=provider_name)

    async def release_lock(self, *, event_id: str | None, provider_name: str | None) -> None:
        if not event_id or not provider_name:
            return None
        try:
            redis_client = await get_redis()
        except Exception:
            return None

        key = self._cache_key(provider_name, event_id)
        try:
            # Mark completed; keep record for TTL to prevent replay.
            await redis_client.set(name=key, value="done", ex=self._ttl())
        except Exception:
            return None
