from __future__ import annotations

import logging
from typing import Any


class WebhookEventService:
    """Prepare webhook-derived events for asynchronous background processing."""

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)

    async def dispatch_event(self, *, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "event_type": event_type,
            "payload": payload,
            "status": "queued",
        }

    async def publish_event(self, *, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.dispatch_event(event_type=event_type, payload=payload)
