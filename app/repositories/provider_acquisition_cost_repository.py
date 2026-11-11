from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from uuid import UUID

from sqlalchemy import Integer, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider import Provider
from app.models.provider_acquisition_cost import ProviderAcquisitionCost, ProviderCostSnapshot
from app.models.transaction import Transaction


_SERVICE_TRANSACTION_TYPES = {
    "airtime": {"airtime_purchase"},
    "data": {"data_purchase"},
    "awoof_data": {"data_purchase"},
    "electricity": {"electricity_purchase"},
    "tv": {"tv_purchase"},
    "education": {"education_purchase"},
    "giftcard_buy": {"giftcard_buy"},
    "giftcard_sell": {"giftcard_sell"},
}
_SUCCESS_STATUSES = {"completed", "posted", "successful", "succeeded", "settled"}
_PROVIDER_CATEGORIES = {
    "airtime": "airtime",
    "data": "data",
    "awoof_data": "data",
    "electricity": "electricity",
    "tv": "tv",
    "education": "education",
    "giftcard_buy": "giftcard",
}


class ProviderAcquisitionCostRepository:
    """Data access and immutable transaction-time snapshots for provider costs."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_provider(self, provider_id: UUID) -> Provider | None:
        return await self.session.get(Provider, provider_id)

    async def list_providers(self) -> list[Provider]:
        result = await self.session.execute(select(Provider).order_by(Provider.name))
        return list(result.scalars().all())

    async def get_configuration(self, config_id: UUID, *, lock: bool = False) -> ProviderAcquisitionCost | None:
        query = select(ProviderAcquisitionCost).where(ProviderAcquisitionCost.id == config_id)
        if lock:
            query = query.with_for_update()
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def list_configurations(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        provider_id: UUID | None = None,
        service_type: str | None = None,
        network_identifier: str | None = None,
        product_identifier: str | None = None,
        currency: str | None = None,
        is_active: bool | None = None,
    ) -> tuple[list[tuple[ProviderAcquisitionCost, Provider]], int]:
        filters: list[Any] = []
        if provider_id is not None:
            filters.append(ProviderAcquisitionCost.provider_id == provider_id)
        if service_type:
            filters.append(ProviderAcquisitionCost.service_type == service_type)
        if network_identifier:
            filters.append(ProviderAcquisitionCost.network_identifier == network_identifier)
        if product_identifier:
            filters.append(ProviderAcquisitionCost.product_identifier == product_identifier)
        if currency:
            filters.append(ProviderAcquisitionCost.currency == currency.upper())
        if is_active is not None:
            filters.append(ProviderAcquisitionCost.is_active.is_(is_active))
        base = select(ProviderAcquisitionCost, Provider).join(
            Provider, Provider.id == ProviderAcquisitionCost.provider_id
        ).where(*filters)
        total_result = await self.session.execute(
            select(func.count()).select_from(
                select(ProviderAcquisitionCost.id)
                .join(Provider, Provider.id == ProviderAcquisitionCost.provider_id)
                .where(*filters)
                .subquery()
            )
        )
        result = await self.session.execute(
            base.order_by(
                Provider.name,
                ProviderAcquisitionCost.service_type,
                ProviderAcquisitionCost.effective_from.desc(),
                ProviderAcquisitionCost.created_at.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.all()), int(total_result.scalar_one() or 0)

    async def overlapping_configuration(
        self,
        *,
        provider_id: UUID,
        service_type: str,
        network_identifier: str | None,
        product_identifier: str | None,
        currency: str,
        effective_from: datetime,
        effective_until: datetime | None,
        exclude_id: UUID | None = None,
    ) -> ProviderAcquisitionCost | None:
        filters: list[Any] = [
            ProviderAcquisitionCost.provider_id == provider_id,
            ProviderAcquisitionCost.service_type == service_type,
            ProviderAcquisitionCost.network_identifier.is_(None)
            if network_identifier is None
            else ProviderAcquisitionCost.network_identifier == network_identifier,
            ProviderAcquisitionCost.product_identifier.is_(None)
            if product_identifier is None
            else ProviderAcquisitionCost.product_identifier == product_identifier,
            ProviderAcquisitionCost.currency == currency,
            ProviderAcquisitionCost.is_active.is_(True),
            or_(ProviderAcquisitionCost.effective_until.is_(None), ProviderAcquisitionCost.effective_until > effective_from),
        ]
        if effective_until is not None:
            filters.append(ProviderAcquisitionCost.effective_from < effective_until)
        if exclude_id is not None:
            filters.append(ProviderAcquisitionCost.id != exclude_id)
        result = await self.session.execute(
            select(ProviderAcquisitionCost).where(*filters).with_for_update()
        )
        return result.scalars().first()

    async def lock_configuration_scope(
        self,
        *,
        provider_id: UUID,
        service_type: str,
        network_identifier: str | None,
        product_identifier: str | None,
        currency: str,
    ) -> None:
        if self.session.get_bind().dialect.name != "postgresql":
            return
        scope = json.dumps(
            [str(provider_id), service_type, network_identifier, product_identifier, currency],
            separators=(",", ":"),
        )
        await self.session.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtext("provider-acquisition-cost"),
                    func.hashtext(scope),
                )
            )
        )

    async def create_configuration(self, configuration: ProviderAcquisitionCost) -> ProviderAcquisitionCost:
        self.session.add(configuration)
        await self.session.flush()
        await self.session.refresh(configuration)
        return configuration

    async def save_configuration(self, configuration: ProviderAcquisitionCost) -> ProviderAcquisitionCost:
        self.session.add(configuration)
        await self.session.flush()
        await self.session.refresh(configuration)
        return configuration

    async def get_transaction_snapshot(self, transaction_id: UUID) -> ProviderCostSnapshot | None:
        result = await self.session.execute(
            select(ProviderCostSnapshot)
            .where(ProviderCostSnapshot.transaction_id == transaction_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def find_matching_configuration(
        self,
        *,
        provider_id: UUID,
        service_type: str,
        network_identifier: str | None,
        product_identifier: str | None,
        currency: str,
        at: datetime,
    ) -> ProviderAcquisitionCost | None:
        filters: list[Any] = [
            ProviderAcquisitionCost.provider_id == provider_id,
            ProviderAcquisitionCost.service_type == service_type,
            ProviderAcquisitionCost.currency == currency,
            ProviderAcquisitionCost.is_active.is_(True),
            ProviderAcquisitionCost.effective_from <= at,
            or_(ProviderAcquisitionCost.effective_until.is_(None), ProviderAcquisitionCost.effective_until > at),
            or_(ProviderAcquisitionCost.network_identifier.is_(None), ProviderAcquisitionCost.network_identifier == network_identifier),
            or_(ProviderAcquisitionCost.product_identifier.is_(None), ProviderAcquisitionCost.product_identifier == product_identifier),
        ]
        result = await self.session.execute(
            select(ProviderAcquisitionCost)
            .where(*filters)
            .order_by(
                (
                    ProviderAcquisitionCost.product_identifier.is_not(None).cast(Integer)
                    + ProviderAcquisitionCost.network_identifier.is_not(None).cast(Integer)
                ).desc(),
                ProviderAcquisitionCost.product_identifier.is_not(None).desc(),
                ProviderAcquisitionCost.network_identifier.is_not(None).desc(),
                ProviderAcquisitionCost.effective_from.desc(),
            )
            .limit(2)
            .with_for_update()
        )
        matches = list(result.scalars().all())
        if len(matches) > 1 and self._specificity(matches[0]) == self._specificity(matches[1]):
            raise ValueError("Multiple equally specific active provider-cost configurations match this transaction.")
        return matches[0] if matches else None

    async def create_snapshot(self, snapshot: ProviderCostSnapshot) -> ProviderCostSnapshot:
        self.session.add(snapshot)
        await self.session.flush()
        return snapshot

    async def list_snapshots(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        configuration_id: UUID | None = None,
        provider_id: UUID | None = None,
        service_type: str | None = None,
        currency: str | None = None,
        source_class: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> tuple[list[tuple[ProviderCostSnapshot, Transaction]], int]:
        filters: list[Any] = []
        if configuration_id is not None:
            filters.append(ProviderCostSnapshot.configuration_id == configuration_id)
        if provider_id is not None:
            filters.append(
                or_(
                    ProviderCostSnapshot.provider_id == provider_id,
                    and_(
                        ProviderCostSnapshot.id.is_(None),
                        or_(
                            func.lower(Provider.name) == func.lower(Transaction.provider_name),
                            func.lower(Provider.code) == func.lower(Transaction.provider_name),
                        ),
                    ),
                )
            )
        if service_type:
            filters.append(ProviderCostSnapshot.service_type == service_type)
        if currency:
            filters.append(ProviderCostSnapshot.currency == currency.upper())
        if source_class:
            filters.append(ProviderCostSnapshot.source_class == source_class)
        if start_date is not None:
            filters.append(Transaction.created_at >= start_date)
        if end_date is not None:
            filters.append(Transaction.created_at <= end_date)
        query = select(ProviderCostSnapshot, Transaction).join(
            Transaction, Transaction.id == ProviderCostSnapshot.transaction_id
        ).where(*filters)
        count_query = select(func.count()).select_from(
            select(ProviderCostSnapshot.id)
            .join(Transaction, Transaction.id == ProviderCostSnapshot.transaction_id)
            .where(*filters)
            .subquery()
        )
        count_result = await self.session.execute(count_query)
        result = await self.session.execute(
            query.order_by(Transaction.created_at.desc(), Transaction.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.all()), int(count_result.scalar_one() or 0)

    async def list_awaiting_transactions(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        provider_id: UUID | None = None,
        provider_identifier: str | None = None,
        service_type: str | None = None,
        currency: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> tuple[list[tuple[Transaction, ProviderCostSnapshot | None]], int]:
        service_types = tuple(
            transaction_type
            for types in _SERVICE_TRANSACTION_TYPES.values()
            for transaction_type in types
        )
        filters: list[Any] = [
            func.lower(Transaction.status).in_(_SUCCESS_STATUSES),
            func.lower(Transaction.transaction_type).in_(service_types),
        ]
        if service_type:
            service_types = _SERVICE_TRANSACTION_TYPES.get(service_type)
            if service_type == "giftcard":
                service_types = _SERVICE_TRANSACTION_TYPES["giftcard_buy"]
            if service_types:
                filters.append(func.lower(Transaction.transaction_type).in_(service_types))
        if currency:
            filters.append(func.upper(Transaction.currency) == currency.upper())
        if provider_identifier:
            filters.append(func.lower(Transaction.provider_name) == provider_identifier.strip().lower())
        if start_date is not None:
            filters.append(Transaction.created_at >= start_date)
        if end_date is not None:
            filters.append(Transaction.created_at <= end_date)
        if provider_id is not None:
            filters.append(
                or_(
                    ProviderCostSnapshot.provider_id == provider_id,
                    and_(ProviderCostSnapshot.id.is_(None), Provider.id == provider_id),
                )
            )
        filters.append(
            or_(
                ProviderCostSnapshot.id.is_(None),
                ProviderCostSnapshot.source_class.in_(("UNAVAILABLE", "ADMIN_CONFIGURED")),
            )
        )
        query = select(Transaction, ProviderCostSnapshot).outerjoin(
            ProviderCostSnapshot, ProviderCostSnapshot.transaction_id == Transaction.id
        ).outerjoin(
            Provider,
            or_(
                Provider.id == ProviderCostSnapshot.provider_id,
                and_(
                    ProviderCostSnapshot.id.is_(None),
                    or_(
                        func.lower(Provider.name) == func.lower(Transaction.provider_name),
                        func.lower(Provider.code) == func.lower(Transaction.provider_name),
                    ),
                ),
            ),
        ).where(*filters)
        count_result = await self.session.execute(
            select(func.count()).select_from(
                select(Transaction.id)
                .outerjoin(ProviderCostSnapshot, ProviderCostSnapshot.transaction_id == Transaction.id)
                .outerjoin(
                    Provider,
                    or_(
                        Provider.id == ProviderCostSnapshot.provider_id,
                        and_(
                            ProviderCostSnapshot.id.is_(None),
                            or_(
                                func.lower(Provider.name) == func.lower(Transaction.provider_name),
                                func.lower(Provider.code) == func.lower(Transaction.provider_name),
                            ),
                        ),
                    ),
                )
                .where(*filters)
                .subquery()
            )
        )
        result = await self.session.execute(
            query.order_by(Transaction.created_at.desc(), Transaction.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.all()), int(count_result.scalar_one() or 0)

    async def finance_breakdown(
        self,
        *,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        provider_id: UUID | None = None,
        provider_identifier: str | None = None,
        service_type: str | None = None,
        currency: str | None = None,
        source_class: str | None = None,
    ) -> list[dict[str, Any]]:
        filters: list[Any] = []
        if start_date is not None:
            filters.append(Transaction.created_at >= start_date)
        if end_date is not None:
            filters.append(Transaction.created_at <= end_date)
        if provider_id is not None:
            filters.append(ProviderCostSnapshot.provider_id == provider_id)
        if provider_identifier:
            filters.append(func.lower(ProviderCostSnapshot.provider_identifier) == provider_identifier.strip().lower())
        if service_type:
            normalized_service = service_type.strip().lower()
            if normalized_service == "data":
                filters.append(ProviderCostSnapshot.service_type.in_(("data", "awoof_data")))
            elif normalized_service == "giftcard":
                filters.append(ProviderCostSnapshot.service_type == "giftcard_buy")
            else:
                filters.append(ProviderCostSnapshot.service_type == normalized_service)
        if currency:
            filters.append(ProviderCostSnapshot.currency == currency.upper())
        if source_class:
            filters.append(ProviderCostSnapshot.source_class == source_class)
        result = await self.session.execute(
            select(
                ProviderCostSnapshot.service_type,
                ProviderCostSnapshot.provider_identifier,
                ProviderCostSnapshot.currency,
                ProviderCostSnapshot.source_class,
                func.sum(ProviderCostSnapshot.selling_amount),
                func.sum(ProviderCostSnapshot.cost_amount),
                func.sum(ProviderCostSnapshot.gross_margin),
                func.count(ProviderCostSnapshot.id),
            )
            .join(Transaction, Transaction.id == ProviderCostSnapshot.transaction_id)
            .where(*filters)
            .group_by(
                ProviderCostSnapshot.service_type,
                ProviderCostSnapshot.provider_identifier,
                ProviderCostSnapshot.currency,
                ProviderCostSnapshot.source_class,
            )
            .order_by(
                ProviderCostSnapshot.service_type,
                ProviderCostSnapshot.provider_identifier,
                ProviderCostSnapshot.currency,
                ProviderCostSnapshot.source_class,
            )
        )
        return [
            {
                "service_type": service,
                "provider_identifier": provider,
                "currency": curr,
                "source_class": source,
                "selling_amount": selling or Decimal("0.00"),
                "provider_cost": cost,
                "margin": margin,
                "transaction_count": int(count or 0),
            }
            for service, provider, curr, source, selling, cost, margin, count in result.all()
        ]

    async def available_currencies(self) -> list[str]:
        result = await self.session.execute(
            select(ProviderCostSnapshot.currency).distinct().order_by(ProviderCostSnapshot.currency)
        )
        return list(result.scalars().all())

    @staticmethod
    def service_details(transaction: Transaction) -> tuple[str, str | None, str | None]:
        transaction_type = str(transaction.transaction_type or "").strip().lower()
        service_type = next(
            (name for name, types in _SERVICE_TRANSACTION_TYPES.items() if transaction_type in types),
            transaction_type,
        )
        metadata = ProviderAcquisitionCostRepository._metadata(transaction.metadata_payload)
        network_keys = {
            "airtime": ("network",),
            "data": ("network",),
            "awoof_data": ("network",),
            "electricity": ("disco",),
            "tv": ("provider",),
            "education": ("provider",),
            "giftcard_buy": ("country",),
            "giftcard_sell": ("country",),
        }
        product_keys = {
            "airtime": (),
            "data": ("bundle_code", "plan_id"),
            "awoof_data": ("bundle_code", "plan_id"),
            "electricity": (),
            "tv": ("package_code",),
            "education": ("examination_type",),
            "giftcard_buy": ("brand", "card_type"),
            "giftcard_sell": ("brand", "card_type"),
        }
        network = ProviderAcquisitionCostRepository._first_metadata_value(metadata, network_keys.get(service_type, ()))
        product_values = [
            ProviderAcquisitionCostRepository._first_metadata_value(metadata, (key,))
            for key in product_keys.get(service_type, ())
        ]
        product_values = [value for value in product_values if value]
        product = "|".join(product_values) if product_values else None
        if service_type in {"data", "awoof_data"} and str(metadata.get("service_variant", "")).lower() == "awoof":
            service_type = "awoof_data"
        return service_type, network, product

    @staticmethod
    def _metadata(value: Any) -> dict[str, Any]:
        import json

        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @staticmethod
    def _first_metadata_value(metadata: dict[str, Any], keys: tuple[str, ...]) -> str | None:
        for key in keys:
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _specificity(configuration: ProviderAcquisitionCost) -> int:
        return int(configuration.product_identifier is not None) + int(configuration.network_identifier is not None)
