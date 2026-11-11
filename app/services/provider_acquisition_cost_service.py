from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider import Provider
from app.models.provider_acquisition_cost import ProviderAcquisitionCost, ProviderCostSnapshot
from app.models.transaction import Transaction
from app.repositories.provider_acquisition_cost_repository import (
    ProviderAcquisitionCostRepository,
    _PROVIDER_CATEGORIES,
    _SERVICE_TRANSACTION_TYPES,
    _SUCCESS_STATUSES,
)

_MONEY = Decimal("0.01")
_SUPPORTED_CONFIG_SERVICES = frozenset(_PROVIDER_CATEGORIES)


class ProviderCostSnapshotService:
    """Apply configured cost rules without treating them as verified provider debits."""

    def __init__(self, repository: ProviderAcquisitionCostRepository) -> None:
        self.repository = repository

    async def capture_successful_transaction(self, transaction: Transaction) -> ProviderCostSnapshot | None:
        if str(transaction.status or "").strip().lower() not in _SUCCESS_STATUSES:
            return None

        service_type, network_identifier, product_identifier = self.repository.service_details(transaction)
        if transaction.transaction_type not in _SERVICE_TRANSACTION_TYPES.get(service_type, set()):
            service_type = str(transaction.category or transaction.transaction_type).strip().lower()
        if service_type not in _SERVICE_TRANSACTION_TYPES:
            return None

        existing = await self.repository.get_transaction_snapshot(transaction.id)
        if existing is not None:
            return existing

        provider_identifier = str(transaction.provider_name or "").strip() or None
        provider = await self._resolve_provider(provider_identifier, service_type)
        timestamp = datetime.now(timezone.utc)
        configuration = None
        reason = None
        if provider is None:
            reason = "The successful transaction has no unambiguous matching provider record."
        else:
            try:
                configuration = await self.repository.find_matching_configuration(
                    provider_id=provider.id,
                    service_type=service_type,
                    network_identifier=network_identifier,
                    product_identifier=product_identifier,
                    currency=str(transaction.currency).strip().upper(),
                    at=timestamp,
                )
            except ValueError as exc:
                reason = str(exc)
            if configuration is None:
                reason = reason or "No effective admin-configured acquisition cost matches the fulfilling provider and transaction product."

        cost_amount: Decimal | None = None
        gross_margin: Decimal | None = None
        source_class = "UNAVAILABLE"
        configuration_id = None
        if configuration is not None:
            cost_amount = self._calculate_cost(configuration, Decimal(transaction.amount))
            gross_margin = (Decimal(transaction.amount) - cost_amount).quantize(_MONEY, rounding=ROUND_HALF_UP)
            source_class = "ADMIN_CONFIGURED"
            configuration_id = configuration.id

        snapshot = ProviderCostSnapshot(
            transaction_id=transaction.id,
            provider_id=provider.id if provider is not None else None,
            configuration_id=configuration_id,
            provider_identifier=provider_identifier,
            service_type=service_type,
            network_identifier=network_identifier,
            product_identifier=product_identifier,
            provider_reference=str(
                transaction.provider_reference or transaction.provider_transaction_id or ""
            ).strip() or None,
            selling_amount=Decimal(transaction.amount).quantize(_MONEY, rounding=ROUND_HALF_UP),
            currency=str(transaction.currency).strip().upper(),
            cost_amount=cost_amount,
            gross_margin=gross_margin,
            source_class=source_class,
            cost_snapshot_at=timestamp,
            reason=reason,
        )
        return await self.repository.create_snapshot(snapshot)

    async def _resolve_provider(self, identifier: str | None, service_type: str) -> Provider | None:
        if not identifier:
            return None
        expected_category = _PROVIDER_CATEGORIES.get(service_type)
        candidates = [
            provider
            for provider in await self.repository.list_providers()
            if str(provider.category or "").strip().lower() == expected_category
            and identifier.lower()
            in {
                str(provider.name or "").strip().lower(),
                str(provider.code or "").strip().lower(),
            }
        ]
        return candidates[0] if len(candidates) == 1 else None

    @staticmethod
    def _calculate_cost(configuration: ProviderAcquisitionCost, selling_amount: Decimal) -> Decimal:
        if configuration.cost_method == "fixed_amount":
            value = Decimal(configuration.cost_value)
        elif configuration.cost_method == "percentage_of_selling_amount":
            value = selling_amount * Decimal(configuration.cost_value) / Decimal("100")
        else:
            raise ValueError(f"Unsupported provider cost method: {configuration.cost_method}")
        return value.quantize(_MONEY, rounding=ROUND_HALF_UP)


class ProviderAcquisitionCostAdministrationService:
    """Administrative validation and lifecycle for configured provider costs."""

    def __init__(self, session: AsyncSession, repository: ProviderAcquisitionCostRepository) -> None:
        self.session = session
        self.repository = repository

    async def list_providers(self) -> list[dict[str, Any]]:
        return [
            {
                "id": str(provider.id),
                "name": provider.name,
                "code": provider.code,
                "category": str(provider.category).strip().lower(),
                "is_active": bool(provider.is_active),
            }
            for provider in await self.repository.list_providers()
        ]

    async def list_configurations(self, **filters: Any) -> dict[str, Any]:
        page = filters.pop("page", 1)
        page_size = filters.pop("page_size", 20)
        rows, total = await self.repository.list_configurations(
            page=page,
            page_size=page_size,
            **filters,
        )
        return {
            "items": [self._configuration_data(configuration, provider) for configuration, provider in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
        }

    async def get_configuration(self, config_id: UUID) -> dict[str, Any]:
        configuration = await self.repository.get_configuration(config_id)
        if configuration is None:
            raise ValueError("Provider acquisition-cost configuration was not found.")
        provider = await self.repository.get_provider(configuration.provider_id)
        if provider is None:
            raise ValueError("The provider linked to this configuration was not found.")
        return self._configuration_data(configuration, provider)

    async def create_configuration(self, *, admin_id: UUID, payload: dict[str, Any]) -> dict[str, Any]:
        async with self.session.begin():
            provider_id = payload["provider_id"]
            provider = await self.repository.get_provider(provider_id)
            service_type = self._validate_service(provider, payload["service_type"])
            values = self._normalized_payload(payload)
            values["service_type"] = service_type
            values["provider_id"] = provider.id
            await self.repository.lock_configuration_scope(
                provider_id=values["provider_id"],
                service_type=values["service_type"],
                network_identifier=values["network_identifier"],
                product_identifier=values["product_identifier"],
                currency=values["currency"],
            )
            if await self.repository.overlapping_configuration(**values):
                raise ValueError("An active provider-cost configuration overlaps this provider, service, currency, and product scope.")
            now = datetime.now(timezone.utc)
            configuration = ProviderAcquisitionCost(
                **values,
                is_active=True,
                evidence_note=payload["evidence_note"].strip(),
                created_by=admin_id,
                updated_by=admin_id,
                created_at=now,
                updated_at=now,
            )
            await self.repository.create_configuration(configuration)
        return self._configuration_data(configuration, provider)

    async def update_configuration(
        self,
        config_id: UUID,
        *,
        admin_id: UUID,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        async with self.session.begin():
            current = await self.repository.get_configuration(config_id, lock=True)
            if current is None:
                raise ValueError("Provider acquisition-cost configuration was not found.")
            effective_from = payload.get("effective_from") or datetime.now(timezone.utc)
            effective_from = self._normalize_datetime(effective_from)
            if effective_from <= self._normalize_datetime(current.effective_from):
                raise ValueError("A new configuration version must start after the current version.")
            provider_id = payload.get("provider_id", current.provider_id)
            provider = await self.repository.get_provider(provider_id)
            service_type = self._validate_service(provider, payload.get("service_type", current.service_type))
            merged = {
                "provider_id": provider_id,
                "service_type": service_type,
                "network_identifier": payload.get("network_identifier", current.network_identifier),
                "product_identifier": payload.get("product_identifier", current.product_identifier),
                "currency": payload.get("currency", current.currency),
                "cost_method": payload.get("cost_method", current.cost_method),
                "cost_value": payload.get("cost_value", current.cost_value),
                "effective_from": effective_from,
                "effective_until": payload.get("effective_until"),
            }
            values = self._normalized_payload(merged)
            await self.repository.lock_configuration_scope(
                provider_id=values["provider_id"],
                service_type=values["service_type"],
                network_identifier=values["network_identifier"],
                product_identifier=values["product_identifier"],
                currency=values["currency"],
            )
            if await self.repository.overlapping_configuration(**values, exclude_id=current.id):
                raise ValueError("The new version overlaps another active configuration for the same scope.")
            current_end = self._normalize_datetime(current.effective_until) if current.effective_until else None
            if current_end is None or effective_from < current_end:
                current.effective_until = effective_from
                current.is_active = False
                current.updated_by = admin_id
                current.updated_at = datetime.now(timezone.utc)
                await self.repository.save_configuration(current)
            now = datetime.now(timezone.utc)
            revised = ProviderAcquisitionCost(
                **values,
                is_active=True,
                evidence_note=payload.get("evidence_note", current.evidence_note).strip(),
                created_by=admin_id,
                updated_by=admin_id,
                created_at=now,
                updated_at=now,
            )
            await self.repository.create_configuration(revised)
        return self._configuration_data(revised, provider)

    async def deactivate_configuration(self, config_id: UUID, *, admin_id: UUID) -> dict[str, Any]:
        async with self.session.begin():
            configuration = await self.repository.get_configuration(config_id, lock=True)
            if configuration is None:
                raise ValueError("Provider acquisition-cost configuration was not found.")
            configuration.is_active = False
            configuration.updated_by = admin_id
            configuration.updated_at = datetime.now(timezone.utc)
            await self.repository.save_configuration(configuration)
            provider = await self.repository.get_provider(configuration.provider_id)
        if provider is None:
            raise ValueError("The provider linked to this configuration was not found.")
        return self._configuration_data(configuration, provider)

    async def list_history(self, config_id: UUID, *, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        current = await self.repository.get_configuration(config_id)
        if current is None:
            raise ValueError("Provider acquisition-cost configuration was not found.")
        history, total = await self.repository.list_configurations(
            page=page,
            page_size=page_size,
            provider_id=current.provider_id,
            service_type=current.service_type,
            network_identifier=current.network_identifier,
            product_identifier=current.product_identifier,
            currency=current.currency,
        )
        return {
            "items": [self._configuration_data(configuration, provider) for configuration, provider in history],
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
        }

    async def transactions_for_configuration(
        self, config_id: UUID, *, page: int = 1, page_size: int = 20
    ) -> dict[str, Any]:
        configuration = await self.repository.get_configuration(config_id)
        if configuration is None:
            raise ValueError("Provider acquisition-cost configuration was not found.")
        rows, total = await self.repository.list_snapshots(
            page=page,
            page_size=page_size,
            configuration_id=config_id,
        )
        return {
            "items": [self._snapshot_data(snapshot, transaction) for snapshot, transaction in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
        }

    async def list_awaiting(self, **filters: Any) -> dict[str, Any]:
        page = filters.pop("page", 1)
        page_size = filters.pop("page_size", 20)
        rows, total = await self.repository.list_awaiting_transactions(
            page=page, page_size=page_size, **filters
        )
        items = []
        for transaction, snapshot in rows:
            if snapshot is None:
                reason = "No transaction-time cost snapshot exists."
            elif snapshot.source_class == "ADMIN_CONFIGURED":
                reason = "Admin-configured cost is not verified provider-debit evidence."
            else:
                reason = snapshot.reason or "No verified transaction-time cost evidence is available."
            items.append(
                {
                    "transaction_id": str(transaction.id),
                    "reference": transaction.reference,
                    "service_type": self.repository.service_details(transaction)[0],
                    "provider_identifier": transaction.provider_name,
                    "provider_reference": transaction.provider_reference or transaction.provider_transaction_id,
                    "selling_amount": str(Decimal(transaction.amount).quantize(_MONEY)),
                    "currency": transaction.currency,
                    "status": transaction.status,
                    "source_class": snapshot.source_class if snapshot is not None else "UNAVAILABLE",
                    "reason": reason,
                    "created_at": transaction.created_at.isoformat() if transaction.created_at else None,
                }
            )
        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
        }

    async def report(self, **filters: Any) -> list[dict[str, Any]]:
        rows = await self.repository.finance_breakdown(**filters)
        return [
            {
                **row,
                "selling_amount": self._money(row["selling_amount"]),
                "provider_cost": self._money(row["provider_cost"]) if row["provider_cost"] is not None else None,
                "margin": self._money(row["margin"]) if row["margin"] is not None else None,
            }
            for row in rows
        ]

    @staticmethod
    def _validate_service(provider: Provider | None, service_type: str) -> str:
        normalized_service = str(service_type).strip().lower()
        if normalized_service not in _SUPPORTED_CONFIG_SERVICES:
            raise ValueError("This service does not support acquisition-cost configuration.")
        if provider is None:
            raise ValueError("Provider was not found.")
        expected_category = _PROVIDER_CATEGORIES[normalized_service]
        if str(provider.category or "").strip().lower() != expected_category:
            raise ValueError("The selected provider is not registered for this service category.")
        return normalized_service

    @staticmethod
    def _normalized_payload(payload: dict[str, Any]) -> dict[str, Any]:
        value = Decimal(str(payload["cost_value"]))
        if not value.is_finite() or value <= 0:
            raise ValueError("Provider cost must be a positive finite Decimal.")
        cost_method = str(payload["cost_method"]).strip().lower()
        if cost_method not in {"fixed_amount", "percentage_of_selling_amount"}:
            raise ValueError("Unsupported provider-cost calculation method.")
        if cost_method == "fixed_amount" and value.quantize(_MONEY) <= 0:
            raise ValueError("Fixed provider cost must be at least one minor currency unit.")
        currency = str(payload["currency"]).strip().upper()
        if len(currency) < 3 or len(currency) > 10:
            raise ValueError("A valid currency code is required.")
        effective_from = ProviderAcquisitionCostAdministrationService._normalize_datetime(
            payload["effective_from"]
        )
        effective_until = payload.get("effective_until")
        if effective_until is not None:
            effective_until = ProviderAcquisitionCostAdministrationService._normalize_datetime(effective_until)
            if effective_until <= effective_from:
                raise ValueError("Effective-until must be later than effective-from.")
        return {
            "provider_id": payload["provider_id"],
            "service_type": str(payload["service_type"]).strip().lower(),
            "network_identifier": ProviderAcquisitionCostAdministrationService._normalize_identifier(
                payload.get("network_identifier")
            ),
            "product_identifier": ProviderAcquisitionCostAdministrationService._normalize_identifier(
                payload.get("product_identifier")
            ),
            "currency": currency,
            "cost_method": cost_method,
            "cost_value": value,
            "effective_from": effective_from,
            "effective_until": effective_until,
        }

    @staticmethod
    def _normalize_identifier(value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @staticmethod
    def _normalize_datetime(value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Effective dates must include a timezone.")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _money(value: Decimal) -> str:
        return str(Decimal(value).quantize(_MONEY, rounding=ROUND_HALF_UP))

    @classmethod
    def _configuration_data(
        cls, configuration: ProviderAcquisitionCost, provider: Provider
    ) -> dict[str, Any]:
        return {
            "id": str(configuration.id),
            "provider_id": str(provider.id),
            "provider_name": provider.name,
            "provider_code": provider.code,
            "provider_category": provider.category,
            "service_type": configuration.service_type,
            "network_identifier": configuration.network_identifier,
            "product_identifier": configuration.product_identifier,
            "currency": configuration.currency,
            "cost_method": configuration.cost_method,
            "cost_value": str(configuration.cost_value),
            "source_class": "ADMIN_CONFIGURED",
            "is_active": configuration.is_active,
            "effective_from": configuration.effective_from.isoformat(),
            "effective_until": configuration.effective_until.isoformat() if configuration.effective_until else None,
            "evidence_note": configuration.evidence_note,
            "created_by": str(configuration.created_by),
            "updated_by": str(configuration.updated_by),
            "created_at": configuration.created_at.isoformat(),
            "updated_at": configuration.updated_at.isoformat(),
        }

    @classmethod
    def _snapshot_data(cls, snapshot: ProviderCostSnapshot, transaction: Transaction) -> dict[str, Any]:
        return {
            "id": str(snapshot.id),
            "transaction_id": str(transaction.id),
            "reference": transaction.reference,
            "provider_identifier": snapshot.provider_identifier,
            "provider_reference": snapshot.provider_reference,
            "service_type": snapshot.service_type,
            "network_identifier": snapshot.network_identifier,
            "product_identifier": snapshot.product_identifier,
            "selling_amount": cls._money(snapshot.selling_amount),
            "currency": snapshot.currency,
            "cost_amount": cls._money(snapshot.cost_amount) if snapshot.cost_amount is not None else None,
            "gross_margin": cls._money(snapshot.gross_margin) if snapshot.gross_margin is not None else None,
            "source_class": snapshot.source_class,
            "reason": snapshot.reason,
            "configuration_id": str(snapshot.configuration_id) if snapshot.configuration_id else None,
            "cost_snapshot_at": snapshot.cost_snapshot_at.isoformat(),
            "created_at": transaction.created_at.isoformat() if transaction.created_at else None,
        }
