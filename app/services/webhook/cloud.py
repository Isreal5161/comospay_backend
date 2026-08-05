from __future__ import annotations

import logging
from typing import Any

from app.services.provider_service import ProviderService
from app.utils.exceptions import ValidationException


class CloudWebhookService:
    """Handle cloud storage upload and object lifecycle callbacks."""

    def __init__(self, *, provider_service: ProviderService | None = None, logger: logging.Logger | None = None) -> None:
        self.provider_service = provider_service
        self.logger = logger or logging.getLogger(__name__)

    async def process_upload_callback(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_provider_service()
        return await self.provider_service.execute_provider(
            category="Cloud",
            operation=self._noop_provider_operation,
            service_type="upload",
            payload={"payload": payload},
        )

    async def process_delete_callback(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_provider_service()
        return await self.provider_service.execute_provider(
            category="Cloud",
            operation=self._noop_provider_operation,
            service_type="delete",
            payload={"payload": payload},
        )

    async def process_object_event(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_provider_service()
        return await self.provider_service.execute_provider(
            category="Cloud",
            operation=self._noop_provider_operation,
            service_type="object",
            payload={"payload": payload},
        )

    async def _noop_provider_operation(self, provider: Any) -> dict[str, Any]:
        return {"provider": getattr(provider, "name", "unknown")}

    def _require_provider_service(self) -> ProviderService:
        if self.provider_service is None:
            raise ValidationException("ProviderService dependency is required for cloud webhook processing.")
        return self.provider_service
