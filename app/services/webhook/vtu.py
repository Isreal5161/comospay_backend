from __future__ import annotations

import logging
from typing import Any

from app.services.airtime_service import AirtimeService
from app.services.data_service import DataService
from app.services.electricity_service import ElectricityService
from app.services.provider_service import ProviderService
from app.services.tv_service import TVService
from app.utils.exceptions import ValidationException


class VTUWebhookService:
    """Handle VTU provider callbacks and reconciliation updates."""

    def __init__(
        self,
        *,
        airtime_service: AirtimeService | None = None,
        data_service: DataService | None = None,
        electricity_service: ElectricityService | None = None,
        tv_service: TVService | None = None,
        provider_service: ProviderService | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.airtime_service = airtime_service
        self.data_service = data_service
        self.electricity_service = electricity_service
        self.tv_service = tv_service
        self.provider_service = provider_service
        self.logger = logger or logging.getLogger(__name__)

    async def process_airtime_callback(self, *, provider_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_airtime_service()
        return await self.airtime_service.reconcile_transaction(reference=self._reference(payload), provider_name=provider_name)

    async def process_data_callback(self, *, provider_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_data_service()
        return await self.data_service.reconcile_transaction(reference=self._reference(payload), provider_name=provider_name)

    async def process_electricity_callback(self, *, provider_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_electricity_service()
        return await self.electricity_service.reconcile_transaction(reference=self._reference(payload), provider_name=provider_name)

    async def process_tv_callback(self, *, provider_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_tv_service()
        return await self.tv_service.reconcile_transaction(reference=self._reference(payload), provider_name=provider_name)

    async def handle_provider_delivery_notification(self, *, provider_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_provider_service()
        return await self.provider_service.execute_provider(
            category="VTU",
            operation=self._noop_provider_operation,
            service_type="delivery",
            payload={"provider_name": provider_name, "payload": payload},
        )

    async def handle_reconciliation_callback(self, *, provider_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_provider_service()
        return await self.provider_service.execute_provider(
            category="VTU",
            operation=self._noop_provider_operation,
            service_type="reconciliation",
            payload={"provider_name": provider_name, "payload": payload},
        )

    async def handle_failed_transaction_callback(self, *, provider_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_airtime_service()
        return await self.airtime_service.reconcile_failed_transactions(reference=self._reference(payload), provider_name=provider_name)

    async def _noop_provider_operation(self, provider: Any) -> dict[str, Any]:
        return {"provider": getattr(provider, "name", "unknown")}

    def _require_airtime_service(self) -> AirtimeService:
        if self.airtime_service is None:
            raise ValidationException("AirtimeService dependency is required for VTU webhook processing.")
        return self.airtime_service

    def _require_data_service(self) -> DataService:
        if self.data_service is None:
            raise ValidationException("DataService dependency is required for VTU data callbacks.")
        return self.data_service

    def _require_electricity_service(self) -> ElectricityService:
        if self.electricity_service is None:
            raise ValidationException("ElectricityService dependency is required for VTU electricity callbacks.")
        return self.electricity_service

    def _require_tv_service(self) -> TVService:
        if self.tv_service is None:
            raise ValidationException("TVService dependency is required for VTU TV callbacks.")
        return self.tv_service

    def _require_provider_service(self) -> ProviderService:
        if self.provider_service is None:
            raise ValidationException("ProviderService dependency is required for VTU provider callbacks.")
        return self.provider_service

    def _reference(self, payload: dict[str, Any]) -> str:
        return str(payload.get("reference") or payload.get("transaction_reference") or payload.get("data", {}).get("reference") or "")
