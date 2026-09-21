from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4

from app.models.provider import Provider
from app.models.transaction import Transaction
from app.models.wallet import Wallet
from app.integrations.airtime.manager import ProviderManager
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.provider_service import ProviderService
from app.services.vtu.status import normalize_vtu_status
from app.services.wallet_service import WalletService
from app.utils.exceptions import PaymentException, ValidationException, WalletException


class ElectricityPurchaseService:
    """Manage electricity purchase workflows using injected wallet, provider, and repository services."""

    def __init__(
        self,
        *,
        wallet_service: WalletService,
        provider_service: ProviderService,
        provider_manager: ProviderManager | None = None,
        transaction_repository: TransactionRepository,
        user_repository: UserRepository,
        wallet_repository: WalletRepository,
        logger: logging.Logger | None = None,
    ) -> None:
        self.wallet_service = wallet_service
        self.provider_service = provider_service
        self.provider_manager = provider_manager or ProviderManager()
        self.transaction_repository = transaction_repository
        self.user_repository = user_repository
        self.wallet_repository = wallet_repository
        self.logger = logger or logging.getLogger(__name__)

    async def purchase_electricity(
        self,
        *,
        user_id: UUID,
        meter_number: str,
        disco: str,
        amount: Decimal | float | int,
        transaction_pin: str,
        meter_type: str | None = None,
        customer_name: str | None = None,
        wallet_id: UUID | None = None,
        currency: str = "NGN",
        description: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Create a new electricity purchase, debit the wallet, and dispatch it through provider orchestration."""
        amount_value = self._normalize_amount(amount)
        self._validate_purchase_request(
            user_id=user_id,
            meter_number=meter_number,
            disco=disco,
            amount=amount_value,
            transaction_pin=transaction_pin,
        )

        await self._ensure_user_exists(user_id)
        wallet = await self._resolve_wallet(user_id=user_id, wallet_id=wallet_id)
        await self.wallet_service.verify_transaction_pin(user_id=user_id, pin=transaction_pin)
        balance = await self._get_wallet_balance(wallet_id=wallet.id)
        if balance < amount_value:
            raise WalletException("Insufficient wallet balance for electricity purchase.")

        reference = self.generate_reference()
        self.logger.info(
            "electricity_purchase_started",
            extra={
                "reference": reference,
                "user_id": str(user_id),
                "meter_number": meter_number,
                "disco": disco,
                "amount": str(amount_value),
            },
        )

        async with self._transaction_scope():
            transaction = Transaction(
                reference=reference,
                user_id=user_id,
                wallet_id=wallet.id,
                transaction_type="electricity_purchase",
                category="electricity",
                amount=amount_value,
                currency=currency,
                charges=Decimal("0"),
                total_amount=amount_value,
                status="pending",
                provider_name=provider_name,
                description=description or f"Electricity purchase for {disco} meter {meter_number}",
                metadata_payload=self._serialize_metadata(
                    {
                        "meter_number": meter_number,
                        "disco": disco,
                        "meter_type": meter_type,
                        "customer_name": customer_name,
                        "wallet_id": str(wallet.id),
                    }
                ),
            )
            transaction = await self.transaction_repository.create_transaction(transaction)
            await self._debit_wallet(transaction=transaction, wallet=wallet, amount=amount_value)

        return await self.process_electricity_purchase(
            transaction=transaction,
            provider_operation=provider_operation,
            provider_name=provider_name,
            metadata_payload=metadata_payload,
        )

    async def process_electricity_purchase(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Dispatch a pending electricity purchase to the provider and update its outcome."""
        resolved = await self._resolve_transaction(transaction=transaction, reference=reference)

        self.logger.info("electricity_purchase_provider_execution", extra={"reference": resolved.reference})
        try:
            provider_response = self._normalize_provider_manager_response(
                await self.provider_manager.execute(
                    operation="purchase_electricity",
                    meter_number=self._extract_meter_number(resolved),
                    provider=self._extract_disco(resolved),
                    amount=str(resolved.amount),
                    reference=resolved.reference,
                )
            )
        except Exception as exc:
            await self._handle_provider_failure(transaction=resolved, reason=str(exc))
            raise PaymentException(detail=str(exc)) from exc

        status = self._normalize_status(provider_response.get("status"))
        async with self._transaction_scope():
            transaction_record = await self.transaction_repository.get_by_reference(resolved.reference)
            if transaction_record is None:
                raise ValidationException("Purchase transaction was not found.")

            transaction_record.status = status
            transaction_record.provider_name = provider_name or provider_response.get("provider") or transaction_record.provider_name
            transaction_record.provider_reference = provider_response.get("provider_reference") or transaction_record.provider_reference
            transaction_record.provider_transaction_id = provider_response.get("provider_transaction_id") or transaction_record.provider_transaction_id
            transaction_record.external_reference = transaction_record.provider_reference
            transaction_record.metadata_payload = self._serialize_metadata(
                {
                    **self._parse_metadata(transaction_record.metadata_payload),
                    "provider_response": provider_response,
                    "meter_number": self._extract_meter_number(transaction_record),
                    "disco": self._extract_disco(transaction_record),
                    "attempted": True,
                }
            )
            transaction_record = await self.transaction_repository.update_transaction(
                transaction_record,
                status=transaction_record.status,
                provider_name=transaction_record.provider_name,
                provider_reference=transaction_record.provider_reference,
                provider_transaction_id=transaction_record.provider_transaction_id,
                external_reference=transaction_record.external_reference,
                metadata_payload=transaction_record.metadata_payload,
            )

            if status in {"failed", "cancelled", "reversed"}:
                await self._handle_provider_failure(
                    transaction=transaction_record,
                    reason=provider_response.get("message") or "Provider reported a failed electricity purchase.",
                )
            elif status in {"succeeded", "completed", "settled"}:
                self.logger.info("electricity_purchase_succeeded", extra={"reference": transaction_record.reference})
            else:
                self.logger.info("electricity_purchase_pending", extra={"reference": transaction_record.reference, "status": status})

            return await self._build_response(transaction_record)

    async def reverse_electricity_purchase(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Reverse a purchased electricity transaction and restore the debited wallet balance when applicable."""
        resolved = await self._resolve_transaction(transaction=transaction, reference=reference)
        async with self._transaction_scope():
            if resolved.status in {"reversed", "cancelled"}:
                return await self._build_response(resolved)
            await self._reverse_wallet_debit(transaction=resolved, reason=reason or "electricity_purchase_reversed")
            resolved.status = "reversed"
            resolved.metadata_payload = self._serialize_metadata(
                {
                    **self._parse_metadata(resolved.metadata_payload),
                    "reversal_reason": reason or "electricity_purchase_reversed",
                }
            )
            resolved = await self.transaction_repository.update_transaction(resolved, status=resolved.status, metadata_payload=resolved.metadata_payload)
            self.logger.info("electricity_purchase_reversed", extra={"reference": resolved.reference})
            return await self._build_response(resolved)

    async def retry_electricity_purchase(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Retry a failed or pending electricity purchase after restoring the wallet debit state if needed."""
        resolved = await self._resolve_transaction(transaction=transaction, reference=reference)
        if resolved.status in {"succeeded", "completed", "settled"}:
            return await self._build_response(resolved)
        wallet = await self._get_wallet_for_transaction(resolved)
        if resolved.status in {"failed", "cancelled", "reversed"}:
            await self._debit_wallet(transaction=resolved, wallet=wallet, amount=resolved.amount)
        self.logger.info("electricity_purchase_retry", extra={"reference": resolved.reference, "status": resolved.status})
        return await self.process_electricity_purchase(
            transaction=resolved,
            provider_operation=provider_operation,
            provider_name=provider_name,
            metadata_payload=metadata_payload,
        )

    async def get_purchase_status(self, *, reference: str) -> dict[str, Any]:
        """Retrieve the current status of an electricity purchase."""
        if not reference or not isinstance(reference, str) or not reference.strip():
            raise ValidationException("Electricity purchase reference is required.")
        transaction = await self.transaction_repository.get_by_reference(reference)
        if transaction is None:
            raise ValidationException("Electricity purchase reference was not found.")
        return await self._build_response(transaction)

    async def get_purchase_details(self, *, reference: str) -> dict[str, Any]:
        """Fetch a detailed electricity purchase record."""
        transaction = await self.transaction_repository.get_by_reference(reference)
        if transaction is None:
            raise ValidationException("Electricity purchase reference was not found.")
        return await self._build_response(transaction, include_metadata=True)

    def generate_reference(self) -> str:
        """Create a unique reference for a new electricity purchase."""
        return f"electricity-{uuid4().hex[:12]}"

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

    async def _get_wallet_balance(self, *, wallet_id: UUID) -> Decimal:
        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if wallet is None:
            raise ValidationException("Wallet was not found.")
        return wallet.available_balance

    async def _get_wallet_for_transaction(self, transaction: Transaction) -> Wallet:
        if transaction.wallet_id is None:
            raise ValidationException("Transaction wallet was not found.")
        wallet = await self.wallet_repository.get_by_id(transaction.wallet_id)
        if wallet is None:
            raise ValidationException("Wallet was not found.")
        return wallet

    async def _debit_wallet(self, *, transaction: Transaction, wallet: Wallet, amount: Decimal) -> None:
        metadata = self._parse_metadata(transaction.metadata_payload)
        if metadata.get("debit_applied") is True:
            return
        if wallet.available_balance < amount:
            raise WalletException("Insufficient wallet balance for electricity purchase.")
        wallet.available_balance = wallet.available_balance - amount
        wallet.ledger_balance = wallet.ledger_balance - amount
        await self.wallet_repository.update_balance_fields(
            wallet,
            available_balance=wallet.available_balance,
            ledger_balance=wallet.ledger_balance,
        )
        metadata["debit_applied"] = True
        transaction.metadata_payload = self._serialize_metadata(metadata)
        await self.transaction_repository.update_transaction(transaction, metadata_payload=transaction.metadata_payload)

    async def _reverse_wallet_debit(self, *, transaction: Transaction, reason: str | None = None) -> None:
        metadata = self._parse_metadata(transaction.metadata_payload)
        if metadata.get("debit_applied") is not True:
            metadata["reversal_reason"] = reason or "wallet_debit_not_required"
            transaction.metadata_payload = self._serialize_metadata(metadata)
            await self.transaction_repository.update_transaction(transaction, metadata_payload=transaction.metadata_payload)
            return
        if transaction.wallet_id is None:
            raise ValidationException("Wallet reference is missing for reversal.")

        wallet = await self.wallet_repository.get_by_id(transaction.wallet_id)
        if wallet is None:
            raise ValidationException("Wallet was not found for reversal.")
        wallet.available_balance = wallet.available_balance + transaction.amount
        wallet.ledger_balance = wallet.ledger_balance + transaction.amount
        await self.wallet_repository.update_balance_fields(
            wallet,
            available_balance=wallet.available_balance,
            ledger_balance=wallet.ledger_balance,
        )
        metadata["debit_applied"] = False
        metadata["reversal_applied"] = True
        metadata["reversal_reason"] = reason or "electricity_purchase_reversed"
        transaction.metadata_payload = self._serialize_metadata(metadata)
        await self.transaction_repository.update_transaction(transaction, metadata_payload=transaction.metadata_payload)

    async def _handle_provider_failure(self, *, transaction: Transaction, reason: str) -> None:
        async with self._transaction_scope():
            current = await self.transaction_repository.get_by_reference(transaction.reference)
            if current is None:
                raise ValidationException("Purchase transaction was not found.")
            metadata = self._parse_metadata(current.metadata_payload)
            metadata["failure_reason"] = reason
            metadata["reversed"] = False
            if current.status not in {"failed", "cancelled", "reversed"}:
                current.status = "failed"
            current.metadata_payload = self._serialize_metadata(metadata)
            await self.transaction_repository.update_transaction(current, status=current.status, metadata_payload=current.metadata_payload)
            self.logger.warning("electricity_purchase_failed", extra={"reference": current.reference, "reason": reason})

    async def _resolve_transaction(self, *, transaction: Transaction | None, reference: str | None) -> Transaction:
        if transaction is not None:
            return transaction
        if reference is None:
            raise ValidationException("Electricity purchase reference is required.")
        resolved = await self.transaction_repository.get_by_reference(reference)
        if resolved is None:
            raise ValidationException("Electricity purchase reference was not found.")
        return resolved

    def _validate_purchase_request(self, *, user_id: UUID, meter_number: str, disco: str, amount: Decimal, transaction_pin: str) -> None:
        if user_id is None:
            raise ValidationException("User is required.")
        if not meter_number or not isinstance(meter_number, str) or not meter_number.strip():
            raise ValidationException("Meter number is required.")
        if not disco or not isinstance(disco, str) or not disco.strip():
            raise ValidationException("Distribution company is required.")
        if amount <= 0:
            raise ValidationException("Amount must be greater than zero.")
        if not transaction_pin or not isinstance(transaction_pin, str) or not transaction_pin.strip():
            raise ValidationException("Transaction PIN is required.")

    def _validate_provider_payload(self, payload: dict[str, Any]) -> None:
        if not payload:
            raise ValidationException("Provider payload is required.")

    def _normalize_amount(self, amount: Decimal | float | int) -> Decimal:
        return Decimal(str(amount)).quantize(Decimal("0.01"))

    def _normalize_status(self, status: str | None) -> str:
        return normalize_vtu_status(status)

    def _normalize_provider_response(self, result: Any, provider: Any) -> dict[str, Any]:
        if isinstance(result, dict):
            payload = result
        else:
            payload = {"value": result}
        return {
            "status": self._normalize_status(payload.get("status")),
            "provider": {"name": getattr(provider, "name", None) or payload.get("provider")},
            "provider_reference": payload.get("provider_reference") or payload.get("reference"),
            "provider_transaction_id": payload.get("provider_transaction_id") or payload.get("transaction_id"),
            "message": payload.get("message"),
            "token": payload.get("token"),
            "receipt": payload.get("receipt"),
        }

    def _normalize_provider_manager_response(self, result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise ValidationException("Provider response is invalid.")

        provider_name = result.get("provider")
        payload = result.get("data") if isinstance(result.get("data"), dict) else result

        transaction_container = None
        if isinstance(payload, dict):
            if isinstance(payload.get("transaction_data"), dict):
                transaction_container = payload.get("transaction_data")
            elif isinstance(payload.get("data"), dict) and isinstance(payload["data"].get("transaction_data"), dict):
                transaction_container = payload["data"]["transaction_data"]

        response_data = transaction_container or payload

        status_value = response_data.get("status") if isinstance(response_data, dict) else None
        if status_value is None and isinstance(payload, dict):
            status_value = payload.get("status")
        if status_value is None and isinstance(result.get("success"), bool):
            status_value = "succeeded" if result.get("success") is True else "failed"

        provider_reference = None
        provider_transaction_id = None
        message = None
        token = None
        receipt = None
        metadata: dict[str, Any] | Any = response_data

        if isinstance(response_data, dict):
            provider_reference = response_data.get("transaction_hash") or response_data.get("ref")
            provider_transaction_id = response_data.get("transaction_id") or response_data.get("transaction_hash")
            message = response_data.get("message")
            token = response_data.get("token")
            receipt = response_data.get("receipt")

        if provider_reference is None and isinstance(payload, dict):
            provider_reference = payload.get("provider_reference") or payload.get("ref")
        if provider_transaction_id is None and isinstance(payload, dict):
            provider_transaction_id = payload.get("provider_transaction_id")
        if message is None and isinstance(payload, dict):
            message = payload.get("message")
        if token is None and isinstance(payload, dict):
            token = payload.get("token")
        if receipt is None and isinstance(payload, dict):
            receipt = payload.get("receipt")

        return {
            "status": self._normalize_status(status_value),
            "provider": provider_name or (response_data.get("provider") if isinstance(response_data, dict) else None),
            "provider_reference": provider_reference,
            "provider_transaction_id": provider_transaction_id,
            "message": message or result.get("message"),
            "token": token,
            "receipt": receipt,
            "metadata": metadata,
        }

    def _extract_meter_number(self, transaction: Transaction) -> str | None:
        metadata = self._parse_metadata(transaction.metadata_payload)
        return metadata.get("meter_number")

    def _extract_disco(self, transaction: Transaction) -> str | None:
        metadata = self._parse_metadata(transaction.metadata_payload)
        return metadata.get("disco")

    def _parse_metadata(self, payload: str | None) -> dict[str, Any]:
        if not payload:
            return {}
        try:
            data = json.loads(payload)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            return {"value": payload}
        return {"value": payload}

    def _serialize_metadata(self, payload: dict[str, Any] | None) -> str | None:
        if not payload:
            return None
        return json.dumps(payload, default=str)

    async def _build_response(self, transaction: Transaction, *, include_metadata: bool = False) -> dict[str, Any]:
        payload = {
            "reference": transaction.reference,
            "status": transaction.status,
            "provider": transaction.provider_name,
            "provider_reference": transaction.provider_reference,
            "provider_transaction_id": transaction.provider_transaction_id,
            "amount": str(transaction.amount),
            "currency": transaction.currency,
            "created_at": transaction.created_at.isoformat() if transaction.created_at else None,
            "updated_at": transaction.updated_at.isoformat() if transaction.updated_at else None,
        }
        if include_metadata:
            payload["metadata"] = self._parse_metadata(transaction.metadata_payload)
        return payload

    def _transaction_scope(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def scope():
            async with self.transaction_repository.session.begin():
                yield

        return scope()


__all__ = ["ElectricityPurchaseService"]
