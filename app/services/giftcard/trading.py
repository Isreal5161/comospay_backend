from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4

from app.models.provider import Provider
from app.models.transaction import Transaction
from app.models.wallet import Wallet
from app.schemas.giftcard_schema import GiftCardSellSubmission
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.provider_service import ProviderService
from app.services.wallet_service import WalletService
from app.utils.exceptions import GiftCardException, ProviderException, ValidationException, WalletException


class GiftCardTradingService:
    """Manage gift card buy and sell transactions through provider orchestration."""

    def __init__(
        self,
        *,
        wallet_service: WalletService,
        provider_service: ProviderService,
        transaction_repository: TransactionRepository,
        user_repository: UserRepository,
        wallet_repository: WalletRepository,
        logger: logging.Logger | None = None,
    ) -> None:
        self.wallet_service = wallet_service
        self.provider_service = provider_service
        self.transaction_repository = transaction_repository
        self.user_repository = user_repository
        self.wallet_repository = wallet_repository
        self.logger = logger or logging.getLogger(__name__)

    async def buy_giftcard(
        self,
        *,
        user_id: UUID,
        brand: str,
        card_type: str,
        amount: Decimal | float | int,
        transaction_pin: str,
        country: str | None = None,
        currency: str = "NGN",
        wallet_id: UUID | None = None,
        description: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        metadata_payload: str | None = None,
        reference: str | None = None,
    ) -> dict[str, Any]:
        """Create a gift card purchase transaction and dispatch it to a gift card provider."""
        amount_value = self._normalize_amount(amount)
        self._validate_trade_request(
            user_id=user_id,
            brand=brand,
            card_type=card_type,
            amount=amount_value,
            currency=currency,
            transaction_pin=transaction_pin,
        )
        if provider_operation is None:
            raise ValidationException("A provider operation callback is required for gift card purchase transactions.")

        await self._ensure_user_exists(user_id)
        wallet = await self._resolve_wallet(user_id=user_id, wallet_id=wallet_id)
        await self.wallet_service.verify_transaction_pin(user_id=user_id, pin=transaction_pin)

        if wallet.is_frozen or wallet.is_suspended or not wallet.is_active:
            raise WalletException("Wallet is not active for gift card purchase.")
        if wallet.available_balance < amount_value:
            raise WalletException("Insufficient wallet balance for gift card purchase.")

        if reference is not None:
            existing = await self.transaction_repository.get_by_reference(reference.strip())
            if existing is not None:
                return await self._build_response(existing)

        reference_value = reference.strip() if reference is not None else self.generate_reference("buy")
        self.logger.info(
            "giftcard_purchase_started",
            extra={
                "reference": reference_value,
                "user_id": str(user_id),
                "brand": brand,
                "card_type": card_type,
                "amount": str(amount_value),
            },
        )

        async with self._transaction_scope():
            transaction = Transaction(
                reference=reference_value,
                user_id=user_id,
                wallet_id=wallet.id,
                transaction_type="giftcard_buy",
                category="giftcard",
                amount=amount_value,
                currency=currency,
                charges=Decimal("0"),
                total_amount=amount_value,
                card_amount=submission.card_amount if submission is not None else amount_value,
                card_currency=submission.card_currency if submission is not None else currency,
                status="pending",
                provider_name=provider_name,
                description=description or f"Gift card purchase for {brand} {card_type}",
                metadata_payload=self._serialize_metadata(
                    {
                        "brand": brand,
                        "card_type": card_type,
                        "country": country,
                        "request_metadata": self._parse_metadata(metadata_payload),
                    }
                ),
            )
            transaction = await self.transaction_repository.create_transaction(transaction)
            await self._debit_wallet(transaction=transaction, wallet=wallet, amount=amount_value)

        return await self.process_giftcard_trade(
            transaction=transaction,
            provider_operation=provider_operation,
            provider_name=provider_name,
            metadata_payload=metadata_payload,
        )

    async def sell_giftcard(
        self,
        *,
        user_id: UUID,
        brand: str,
        card_type: str,
        amount: Decimal | float | int,
        transaction_pin: str,
        country: str | None = None,
        currency: str = "NGN",
        wallet_id: UUID | None = None,
        description: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        metadata_payload: str | None = None,
        submission: GiftCardSellSubmission | None = None,
        reference: str | None = None,
    ) -> dict[str, Any]:
        """Create a gift card sell transaction and dispatch it to a gift card provider."""
        amount_value = self._normalize_amount(amount)
        self._validate_trade_request(
            user_id=user_id,
            brand=brand,
            card_type=card_type,
            amount=amount_value,
            currency=currency,
            transaction_pin=transaction_pin,
        )
        if submission is not None:
            if submission.brand_slug != brand:
                raise ValidationException("Submission brand slug must match the gift card brand.")
            if submission.card_type != card_type:
                raise ValidationException("Submission card type must match the gift card card type.")
            if submission.card_amount != float(amount_value):
                raise ValidationException("Submission card amount must match the gift card amount.")

        if provider_operation is None:
            raise ValidationException("A provider operation callback is required for gift card sell transactions.")

        await self._ensure_user_exists(user_id)
        wallet = await self._resolve_wallet(user_id=user_id, wallet_id=wallet_id)
        await self.wallet_service.verify_transaction_pin(user_id=user_id, pin=transaction_pin)

        if wallet.is_frozen or wallet.is_suspended or not wallet.is_active:
            raise WalletException("Wallet is not active for gift card sell transactions.")

        if reference is not None:
            existing = await self.transaction_repository.get_by_reference(reference.strip())
            if existing is not None:
                return await self._build_response(existing)

        reference_value = reference.strip() if reference is not None else self.generate_reference("sell")
        self.logger.info(
            "giftcard_sell_started",
            extra={
                "reference": reference_value,
                "user_id": str(user_id),
                "brand": brand,
                "card_type": card_type,
                "amount": str(amount_value),
            },
        )

        async with self._transaction_scope():
            transaction = Transaction(
                reference=reference_value,
                user_id=user_id,
                wallet_id=wallet.id,
                transaction_type="giftcard_sell",
                category="giftcard",
                amount=amount_value,
                currency=currency,
                charges=Decimal("0"),
                total_amount=amount_value,
                status="pending",
                provider_name=provider_name,
                description=description or f"Gift card sell for {brand} {card_type}",
                metadata_payload=self._serialize_metadata(
                    {
                        "brand": brand,
                        "card_type": card_type,
                        "country": country,
                        "request_metadata": self._parse_metadata(metadata_payload),
                    }
                ),
            )
            transaction = await self.transaction_repository.create_transaction(transaction)

        return await self.process_giftcard_trade(
            transaction=transaction,
            provider_operation=provider_operation,
            provider_name=provider_name,
            metadata_payload=metadata_payload,
            submission=submission,
        )

    async def process_giftcard_trade(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        metadata_payload: str | None = None,
        submission: GiftCardSellSubmission | None = None,
    ) -> dict[str, Any]:
        """Dispatch a pending gift card trade to a provider and update the transaction."""
        resolved = await self._resolve_transaction(transaction=transaction, reference=reference)
        if provider_operation is None:
            raise ValidationException("A provider operation callback is required for gift card processing.")

        self.logger.info("giftcard_trade_provider_execution", extra={"reference": resolved.reference})
        try:
            provider_response = await self.provider_service.execute_giftcard(
                operation=provider_operation,
                validate=self._validate_provider_payload,
                normalize=self._normalize_provider_response,
                payload={
                    "reference": resolved.reference,
                    "brand": self._extract_string(resolved.metadata_payload, "brand"),
                    "card_type": self._extract_string(resolved.metadata_payload, "card_type"),
                    "country": self._extract_string(resolved.metadata_payload, "country"),
                    "amount": str(resolved.amount),
                    "currency": resolved.currency,
                    "wallet_id": str(resolved.wallet_id) if resolved.wallet_id else None,
                    "user_id": str(resolved.user_id),
                    "transaction_type": resolved.transaction_type,
                    "description": resolved.description,
                    "metadata_payload": metadata_payload,
                    "submission": submission,
                },
            )
        except Exception as exc:
            await self._handle_provider_failure(transaction=resolved, reason=str(exc))
            raise GiftCardException(detail=str(exc)) from exc

        status = self._normalize_status(provider_response.get("status"))
        async with self._transaction_scope():
            transaction_record = await self.transaction_repository.get_by_reference_for_update(resolved.reference)
            if transaction_record is None:
                raise ValidationException("Gift card trade transaction was not found.")

            if transaction_record.transaction_type == "giftcard_sell":
                if getattr(transaction_record, "credit_applied", False):
                    return await self._build_response(transaction_record)
                if self._normalize_status(transaction_record.status) in {"failed", "cancelled", "reversed"} and status in {"succeeded", "completed", "settled"}:
                    return await self._build_response(transaction_record)

            metadata = self._parse_metadata(transaction_record.metadata_payload)
            payout_amount = self._coerce_optional_decimal(provider_response.get("payout_amount"))
            payout_currency = self._coerce_string(provider_response.get("payout_currency")).upper() or None
            if payout_amount is not None:
                transaction_record.payout_amount = payout_amount
            if payout_currency is not None:
                transaction_record.payout_currency = payout_currency
            metadata["provider_response"] = provider_response
            metadata["attempted"] = True
            metadata["last_provider_status"] = provider_response.get("status")

            transaction_record.status = status
            transaction_record.provider_name = provider_name or provider_response.get("provider") or transaction_record.provider_name
            transaction_record.provider_reference = provider_response.get("provider_reference") or transaction_record.provider_reference
            transaction_record.provider_transaction_id = provider_response.get("provider_transaction_id") or transaction_record.provider_transaction_id
            transaction_record.external_reference = transaction_record.provider_reference
            transaction_record.metadata_payload = self._serialize_metadata(metadata)

            transaction_record = await self.transaction_repository.update_transaction(
                transaction_record,
                status=transaction_record.status,
                provider_name=transaction_record.provider_name,
                provider_reference=transaction_record.provider_reference,
                provider_transaction_id=transaction_record.provider_transaction_id,
                payout_amount=transaction_record.payout_amount,
                payout_currency=transaction_record.payout_currency,
                external_reference=transaction_record.external_reference,
                metadata_payload=transaction_record.metadata_payload,
            )

            if status in {"failed", "cancelled", "reversed"}:
                await self._handle_provider_failure(transaction=transaction_record, reason=provider_response.get("message") or "Provider reported a failed gift card trade.")
            elif status in {"succeeded", "completed", "settled"}:
                await self._handle_successful_trade(transaction_record)
                self.logger.info("giftcard_trade_succeeded", extra={"reference": transaction_record.reference})
            elif status in {"timeout", "duplicate"}:
                self._mark_reconciliation_required(metadata, f"provider_reported_{status}")
                transaction_record.metadata_payload = self._serialize_metadata(metadata)
                await self.transaction_repository.update_transaction(transaction_record, metadata_payload=transaction_record.metadata_payload)
                self.logger.info("giftcard_trade_pending_reconciliation", extra={"reference": transaction_record.reference, "status": status})
            else:
                self.logger.info("giftcard_trade_pending", extra={"reference": transaction_record.reference, "status": status})

            return await self._build_response(transaction_record)

    async def retry_giftcard_trade(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Retry a previously pending or failed gift card trade."""
        resolved = await self._resolve_transaction(transaction=transaction, reference=reference)
        if resolved.status in {"succeeded", "completed", "settled"}:
            return await self._build_response(resolved)
        if provider_operation is None:
            raise ValidationException("A provider operation callback is required for gift card retry.")

        if resolved.transaction_type == "giftcard_buy" and resolved.status in {"failed", "cancelled", "reversed"}:
            wallet = await self._get_wallet_for_transaction(resolved)
            await self._debit_wallet(transaction=resolved, wallet=wallet, amount=resolved.amount)

        self.logger.info("giftcard_trade_retry", extra={"reference": resolved.reference, "status": resolved.status})
        return await self.process_giftcard_trade(
            transaction=resolved,
            provider_operation=provider_operation,
            provider_name=provider_name,
            metadata_payload=metadata_payload,
        )

    async def get_trade_status(self, *, reference: str) -> dict[str, Any]:
        """Retrieve the current status of a gift card trade."""
        if not reference or not isinstance(reference, str) or not reference.strip():
            raise ValidationException("Gift card trade reference is required.")
        transaction = await self.transaction_repository.get_by_reference(reference.strip())
        if transaction is None:
            raise ValidationException("Gift card trade reference was not found.")
        return await self._build_response(transaction)

    async def get_trade_details(self, *, reference: str) -> dict[str, Any]:
        """Retrieve gift card trade details including transaction metadata."""
        transaction = await self.transaction_repository.get_by_reference(reference.strip())
        if transaction is None:
            raise ValidationException("Gift card trade reference was not found.")
        return await self._build_response(transaction, include_metadata=True)

    def generate_reference(self, direction: str = "trade") -> str:
        """Generate a unique gift card trade reference."""
        return f"giftcard-{direction}-{uuid4().hex[:12]}"

    async def _ensure_user_exists(self, user_id: UUID) -> None:
        user = await self.user_repository.get_by_id(user_id)
        if user is None:
            raise ValidationException("User was not found.")

    async def _resolve_wallet(self, *, user_id: UUID, wallet_id: UUID | None) -> Wallet:
        if wallet_id is not None:
            wallet = await self.wallet_repository.get_by_id(wallet_id)
            if wallet is None:
                raise ValidationException("Wallet was not found.")
            if wallet.user_id != user_id:
                raise ValidationException("Wallet ownership does not match the provided user.")
            return wallet
        wallet = await self.wallet_repository.get_user_wallet(user_id=user_id)
        if wallet is None:
            raise ValidationException("Wallet was not found.")
        return wallet

    async def _get_wallet_for_transaction(self, transaction: Transaction, *, for_update: bool = False) -> Wallet:
        if transaction.wallet_id is None:
            raise ValidationException("Transaction wallet was not found.")
        getter = self.wallet_repository.get_by_id_for_update if for_update else self.wallet_repository.get_by_id
        wallet = await getter(transaction.wallet_id)
        if wallet is None:
            raise ValidationException("Wallet was not found.")
        return wallet

    async def _debit_wallet(self, *, transaction: Transaction, wallet: Wallet, amount: Decimal) -> None:
        if wallet.available_balance < amount:
            raise WalletException("Insufficient wallet balance for gift card purchase.")
        wallet.available_balance = wallet.available_balance - amount
        wallet.ledger_balance = wallet.ledger_balance - amount
        await self.wallet_repository.update_balance_fields(
            wallet,
            available_balance=wallet.available_balance,
            ledger_balance=wallet.ledger_balance,
        )
        metadata = self._parse_metadata(transaction.metadata_payload)
        metadata["debit_applied"] = True
        transaction.metadata_payload = self._serialize_metadata(metadata)
        await self.transaction_repository.update_transaction(transaction, metadata_payload=transaction.metadata_payload)

    async def _credit_wallet(self, *, transaction: Transaction, wallet: Wallet, amount: Decimal) -> None:
        wallet.available_balance = wallet.available_balance + amount
        wallet.ledger_balance = wallet.ledger_balance + amount
        await self.wallet_repository.update_balance_fields(
            wallet,
            available_balance=wallet.available_balance,
            ledger_balance=wallet.ledger_balance,
        )
        metadata = self._parse_metadata(transaction.metadata_payload)
        metadata["credit_applied"] = True
        transaction.metadata_payload = self._serialize_metadata(metadata)
        await self.transaction_repository.update_transaction(transaction, metadata_payload=transaction.metadata_payload)

    async def _reverse_wallet_debit(self, *, transaction: Transaction, reason: str | None = None) -> None:
        metadata = self._parse_metadata(transaction.metadata_payload)
        if metadata.get("debit_applied") is not True:
            metadata["reversal_reason"] = reason or "wallet_debit_not_required"
            transaction.metadata_payload = self._serialize_metadata(metadata)
            await self.transaction_repository.update_transaction(transaction, metadata_payload=transaction.metadata_payload)
            return

        wallet = await self._get_wallet_for_transaction(transaction)
        wallet.available_balance = wallet.available_balance + transaction.amount
        wallet.ledger_balance = wallet.ledger_balance + transaction.amount
        await self.wallet_repository.update_balance_fields(
            wallet,
            available_balance=wallet.available_balance,
            ledger_balance=wallet.ledger_balance,
        )
        metadata["debit_applied"] = False
        metadata["reversal_applied"] = True
        metadata["reversal_reason"] = reason or "giftcard_purchase_reversed"
        transaction.metadata_payload = self._serialize_metadata(metadata)
        await self.transaction_repository.update_transaction(transaction, metadata_payload=transaction.metadata_payload)

    async def _handle_successful_trade(self, transaction: Transaction) -> None:
        if transaction.transaction_type == "giftcard_sell":
            if getattr(transaction, "credit_applied", False):
                return
            wallet = await self._get_wallet_for_transaction(transaction, for_update=True)
            self._validate_sell_payout(transaction, wallet)
            transaction.credited_amount = transaction.payout_amount
            transaction.credited_currency = transaction.payout_currency
            transaction.credit_applied = True
            await self._credit_wallet(transaction=transaction, wallet=wallet, amount=transaction.payout_amount)
            await self.transaction_repository.update_transaction(
                transaction,
                credited_amount=transaction.credited_amount,
                credited_currency=transaction.credited_currency,
                credit_applied=transaction.credit_applied,
            )

    def _validate_sell_payout(self, transaction: Transaction, wallet: Wallet) -> None:
        if transaction.payout_amount is None or not transaction.payout_currency:
            raise ValidationException("Gift card sell payout is missing.")
        if transaction.payout_currency.upper() != wallet.currency.upper():
            raise WalletException("Gift card payout currency does not match wallet currency.")

    async def _handle_provider_failure(self, *, transaction: Transaction, reason: str) -> None:
        self.logger.warning("giftcard_trade_failed", extra={"reference": transaction.reference, "reason": reason})
        if transaction.status not in {"failed", "cancelled", "reversed"}:
            transaction.status = "failed"
            metadata = self._parse_metadata(transaction.metadata_payload)
            metadata["failure_reason"] = reason
            metadata["reconciliation_required"] = True
            transaction.metadata_payload = self._serialize_metadata(metadata)
            await self.transaction_repository.update_transaction(transaction, status=transaction.status, metadata_payload=transaction.metadata_payload)

        if transaction.transaction_type == "giftcard_buy":
            await self._reverse_wallet_debit(transaction=transaction, reason=reason)

    async def _resolve_transaction(self, transaction: Transaction | None, reference: str | None) -> Transaction:
        if transaction is not None:
            return transaction
        if not reference or not isinstance(reference, str) or not reference.strip():
            raise ValidationException("Gift card trade reference is required.")
        resolved = await self.transaction_repository.get_by_reference(reference.strip())
        if resolved is None:
            raise ValidationException("Gift card trade transaction was not found.")
        return resolved

    def _validate_trade_request(
        self,
        *,
        user_id: UUID,
        brand: str,
        card_type: str,
        amount: Decimal,
        currency: str,
        transaction_pin: str | None,
    ) -> None:
        if user_id is None:
            raise ValidationException("User ID is required.")
        if not brand or not isinstance(brand, str) or not brand.strip():
            raise ValidationException("Gift card brand is required.")
        if not card_type or not isinstance(card_type, str) or not card_type.strip():
            raise ValidationException("Gift card type is required.")
        if amount <= Decimal("0"):
            raise ValidationException("Amount must be greater than zero.")
        if not currency or not isinstance(currency, str) or not currency.strip():
            raise ValidationException("Currency code is required.")
        if not transaction_pin or not isinstance(transaction_pin, str) or not transaction_pin.strip():
            raise ValidationException("Transaction PIN is required.")

    def _normalize_amount(self, amount: Decimal | float | int) -> Decimal:
        if isinstance(amount, Decimal):
            return amount
        return Decimal(str(amount))

    def _build_response(self, transaction: Transaction, include_metadata: bool = False) -> dict[str, Any]:
        response: dict[str, Any] = {
            "reference": transaction.reference,
            "transaction_type": transaction.transaction_type,
            "category": transaction.category,
            "amount": str(transaction.amount),
            "currency": transaction.currency,
            "status": transaction.status,
            "provider_name": transaction.provider_name,
            "provider_reference": transaction.provider_reference,
            "provider_transaction_id": transaction.provider_transaction_id,
            "card_amount": str(transaction.card_amount) if transaction.card_amount is not None else None,
            "card_currency": transaction.card_currency,
            "payout_amount": str(transaction.payout_amount) if transaction.payout_amount is not None else None,
            "payout_currency": transaction.payout_currency,
            "credited_amount": str(transaction.credited_amount) if transaction.credited_amount is not None else None,
            "credited_currency": transaction.credited_currency,
            "description": transaction.description,
            "created_at": transaction.created_at.isoformat() if transaction.created_at else None,
            "updated_at": transaction.updated_at.isoformat() if transaction.updated_at else None,
        }
        if include_metadata:
            response["metadata_payload"] = self._parse_metadata(transaction.metadata_payload)
        return response

    def _extract_string(self, metadata_payload: str | None, key: str) -> str | None:
        metadata = self._parse_metadata(metadata_payload)
        value = metadata.get(key)
        return str(value).strip() if value is not None else None

    def _parse_metadata(self, metadata_payload: str | None) -> dict[str, Any]:
        if not metadata_payload or not isinstance(metadata_payload, str):
            return {}
        try:
            return json.loads(metadata_payload)
        except Exception:
            return {}

    def _serialize_metadata(self, payload: dict[str, Any]) -> str:
        try:
            return json.dumps(payload)
        except Exception:
            return "{}"

    def _validate_provider_payload(self, payload: dict[str, Any]) -> None:
        if payload is None:
            raise ValidationException("Provider payload is required.")

    def _normalize_provider_response(self, provider_response: Any, provider: Provider) -> dict[str, Any]:
        if isinstance(provider_response, dict):
            payload = provider_response
        else:
            payload = {"value": provider_response}
        return {
            "status": payload.get("status"),
            "provider": getattr(provider, "name", None) or self._coerce_string(payload.get("provider")),
            "provider_reference": self._coerce_string(payload.get("provider_reference") or payload.get("reference")),
            "provider_transaction_id": self._coerce_string(payload.get("provider_transaction_id") or payload.get("transaction_id")),
            "message": self._coerce_string(payload.get("message") or payload.get("error")),
            "payout_amount": payload.get("payout_amount"),
            "payout_currency": self._coerce_string(payload.get("payout_currency") or self._extract_payout_currency(payload.get("payout_amount"))),
            "card_amount": payload.get("card_amount"),
            "card_currency": self._coerce_string(payload.get("card_currency")),
        }

    def _coerce_string(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return str(value)

    def _coerce_optional_decimal(self, value: Any) -> Decimal | None:
        if isinstance(value, dict):
            value = value.get("raw")
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except Exception:
            return None

    def _extract_payout_currency(self, value: Any) -> str:
        if isinstance(value, dict):
            return self._coerce_string(value.get("currency"))
        return ""

    def _normalize_status(self, status: Any) -> str:
        if status is None:
            return "pending"
        normalized = str(status).strip().lower()
        if normalized in {"success", "succeeded", "completed", "settled", "paid"}:
            return "succeeded"
        if normalized in {"failed", "failure", "cancelled", "declined", "error"}:
            return "failed"
        if normalized in {"timeout", "timed_out", "time_out"}:
            return "timeout"
        if normalized in {"duplicate"}:
            return "duplicate"
        if normalized in {"pending", "processing", "queued", "in_progress"}:
            return "pending"
        return normalized

    def _mark_reconciliation_required(self, metadata: dict[str, Any], reason: str) -> None:
        events = metadata.get("reconciliation_events")
        if not isinstance(events, list):
            events = []
        events.append({"event": "reconciliation_required", "reason": reason, "timestamp": datetime.now(timezone.utc).isoformat()})
        metadata["reconciliation_events"] = events
        metadata["reconciliation_required"] = True

    @asynccontextmanager
    async def _transaction_scope(self):
        session = self.transaction_repository.session
        if session.in_transaction():
            yield
            return
        async with session.begin():
            yield


__all__ = ["GiftCardTradingService"]
