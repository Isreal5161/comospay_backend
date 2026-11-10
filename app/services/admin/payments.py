from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.utils.exceptions import ValidationException


class PaymentAdministrationService:
    """Read-only administrative view over persisted payment-related transactions."""

    _CATEGORY_GROUPS = {
        "wallet_funding": ("funding", "deposit"),
        "payment": ("payment", "collection"),
        "transfer": ("transfer",),
        "bank_payout": ("withdrawal",),
    }
    _ALL_CATEGORIES = tuple(
        category
        for categories in _CATEGORY_GROUPS.values()
        for category in categories
    )

    def __init__(
        self,
        *,
        transaction_service: Any,
        wallet_repository: Any | None = None,
        user_repository: Any | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.transaction_service = transaction_service
        self.wallet_repository = wallet_repository
        self.user_repository = user_repository
        self.logger = logger or logging.getLogger(__name__)

    async def list_payments(
        self,
        *,
        payment_category: str | None = None,
        method: str | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        categories = self._categories_for(payment_category)
        result = await self.transaction_service.list_transactions(
            **payload,
            categories=categories,
            method=method,
        )
        data = result.get("data")
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            for item in data["items"]:
                if isinstance(item, dict):
                    item["payment_type"] = self._payment_type(item.get("category"))
                    item["method"] = item.get("payment_method") or item.get("provider_name")
            result["meta"] = {"source": "admin.payments", **result.get("meta", {})}
        return result

    async def get_payment_details(self, *, transaction_id: UUID, **payload: Any) -> dict[str, Any]:
        result = await self.transaction_service.get_transaction_details(
            transaction_id=transaction_id,
            **payload,
        )
        data = result.get("data")
        if not isinstance(data, dict) or str(data.get("category", "")).lower() not in self._ALL_CATEGORIES:
            raise ValidationException(detail="Payment record not found.", error_code="PAYMENT_NOT_FOUND")

        data["payment_type"] = self._payment_type(data.get("category"))
        metadata = data.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        data["method"] = (
            metadata.get("payment_method")
            or metadata.get("channel")
            or data.get("provider_name")
        )
        data["recipient"] = await self._resolve_transfer_recipient(metadata)
        result["meta"] = {"source": "admin.payments", **result.get("meta", {})}
        return result

    def _categories_for(self, payment_category: str | None) -> tuple[str, ...]:
        if payment_category is None or not payment_category.strip():
            return self._ALL_CATEGORIES
        categories = self._CATEGORY_GROUPS.get(payment_category.strip().lower())
        if categories is None:
            raise ValidationException(detail="Unsupported payment category.", error_code="INVALID_PAYMENT_CATEGORY")
        return categories

    @staticmethod
    def _payment_type(category: Any) -> str:
        normalized = str(category or "").strip().lower()
        if normalized in {"funding", "deposit"}:
            return "wallet_funding"
        if normalized in {"payment", "collection"}:
            return "payment"
        if normalized == "transfer":
            return "transfer"
        if normalized == "withdrawal":
            return "bank_payout"
        return normalized

    async def _resolve_transfer_recipient(self, metadata: dict[str, Any]) -> dict[str, str | None] | None:
        raw_wallet_id = metadata.get("recipient_wallet_id")
        if not isinstance(raw_wallet_id, str) or self.wallet_repository is None or self.user_repository is None:
            return None
        try:
            wallet_id = UUID(raw_wallet_id)
        except ValueError:
            return None
        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if wallet is None:
            return None
        user = await self.user_repository.get_by_id(wallet.user_id)
        if user is None:
            return None
        name = " ".join(
            part.strip()
            for part in (getattr(user, "first_name", None), getattr(user, "last_name", None))
            if isinstance(part, str) and part.strip()
        )
        return {
            "id": str(user.id),
            "name": name or None,
            "email": getattr(user, "email", None),
        }
