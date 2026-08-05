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
from app.services.wallet_service import WalletService
from app.utils.exceptions import PaymentException, ValidationException, WalletException


class AirtimePurchaseService:
    """Manage airtime purchase workflows using injected wallet, provider, and repository services."""

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

    async def purchase_airtime(
        self,
        *,
        user_id: UUID,
        phone_number: str,
        amount: Decimal | float | int,
        transaction_pin: str,
        network: str | None = None,
        wallet_id: UUID | None = None,
        currency: str = "NGN",
        description: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Create a new airtime purchase, debit the wallet, and dispatch it through provider orchestration."""
        amount_value = self._normalize_amount(amount)
        self._validate_purchase_request(user_id=user_id, phone_number=phone_number, amount=amount_value, transaction_pin=transaction_pin)

        await self._ensure_user_exists(user_id)
        wallet = await self._resolve_wallet(user_id=user_id, wallet_id=wallet_id)
        await self.wallet_service.verify_transaction_pin(user_id=user_id, pin=transaction_pin)
        balance = await self._get_wallet_balance(wallet_id=wallet.id)
        if balance < amount_value:
            raise WalletException("Insufficient wallet balance for airtime purchase.")

        reference = self.generate_reference()
        self.logger.info(
            "airtime_purchase_started",
            extra={"reference": reference, "user_id": str(user_id), "phone_number": phone_number, "amount": str(amount_value)},
        )

        async with self._transaction_scope():
            transaction = Transaction(
                reference=reference,
                user_id=user_id,
                wallet_id=wallet.id,
                transaction_type="airtime_purchase",
                category="airtime",
                amount=amount_value,
                currency=currency,
                charges=Decimal("0"),
                total_amount=amount_value,
                status="pending",
                provider_name=provider_name,
                description=description or f"Airtime purchase for {phone_number}",
                metadata_payload=self._serialize_metadata({"phone_number": phone_number, "network": network, "wallet_id": str(wallet.id)}),
            )
            transaction = await self.transaction_repository.create_transaction(transaction)
            await self._debit_wallet(transaction=transaction, wallet=wallet, amount=amount_value)

        return await self.process_airtime_purchase(
            transaction=transaction,
            network=network,
            provider_name=provider_name,
            metadata_payload=metadata_payload,
        )

    async def process_airtime_purchase(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        network: str | None = None,
        provider_name: str | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Dispatch a pending airtime purchase to the provider and update its outcome."""
        resolved = await self._resolve_transaction(transaction=transaction, reference=reference)

        self.logger.info("airtime_purchase_provider_execution", extra={"reference": resolved.reference})
        try:
            provider_response = self._normalize_provider_manager_response(
                await self.provider_manager.execute(
                    "buy_airtime",
                    phone_number=self._extract_phone_number(resolved),
                    network=network or self._extract_network(resolved),
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
                    "provider_response": provider_response,
                    "phone_number": self._extract_phone_number(transaction_record),
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
                await self._handle_provider_failure(transaction=transaction_record, reason=provider_response.get("message") or "Provider reported a failed airtime purchase.")
            elif status in {"succeeded", "completed", "settled"}:
                self.logger.info("airtime_purchase_succeeded", extra={"reference": transaction_record.reference})
            else:
                self.logger.info("airtime_purchase_pending", extra={"reference": transaction_record.reference, "status": status})

            return await self._build_response(transaction_record)

    async def reverse_airtime_purchase(self, *, transaction: Transaction | None = None, reference: str | None = None, reason: str | None = None) -> dict[str, Any]:
        """Reverse a purchased airtime transaction and restore the debited wallet balance when applicable."""
        resolved = await self._resolve_transaction(transaction=transaction, reference=reference)
        async with self._transaction_scope():
            if resolved.status in {"reversed", "cancelled"}:
                return await self._build_response(resolved)
            await self._reverse_wallet_debit(transaction=resolved, reason=reason or "airtime_purchase_reversed")
            resolved.status = "reversed"
            resolved.metadata_payload = self._serialize_metadata({"reversal_reason": reason or "airtime_purchase_reversed"})
            resolved = await self.transaction_repository.update_transaction(resolved, status=resolved.status, metadata_payload=resolved.metadata_payload)
            self.logger.info("airtime_purchase_reversed", extra={"reference": resolved.reference})
            return await self._build_response(resolved)

    async def retry_airtime_purchase(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        network: str | None = None,
        provider_name: str | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Retry a failed or pending airtime purchase after restoring the wallet debit state if needed."""
        resolved = await self._resolve_transaction(transaction=transaction, reference=reference)
        if resolved.status in {"succeeded", "completed", "settled"}:
            return await self._build_response(resolved)

        wallet = await self._get_wallet_for_transaction(resolved)
        if resolved.status in {"failed", "cancelled", "reversed"}:
            await self._debit_wallet(transaction=resolved, wallet=wallet, amount=resolved.amount)
        self.logger.info("airtime_purchase_retry", extra={"reference": resolved.reference, "status": resolved.status})
        return await self.process_airtime_purchase(
            transaction=resolved,
            network=network or self._extract_network(resolved),
            provider_name=provider_name,
            metadata_payload=metadata_payload,
        )

    async def get_purchase_status(self, *, reference: str) -> dict[str, Any]:
        """Retrieve the current status of an airtime purchase."""
        if not reference or not isinstance(reference, str) or not reference.strip():
            raise ValidationException("Airtime purchase reference is required.")
        transaction = await self.transaction_repository.get_by_reference(reference)
        if transaction is None:
            raise ValidationException("Airtime purchase reference was not found.")
        return await self._build_response(transaction)

    async def get_purchase_details(self, *, reference: str) -> dict[str, Any]:
        """Fetch a detailed airtime purchase record."""
        transaction = await self.transaction_repository.get_by_reference(reference)
        if transaction is None:
            raise ValidationException("Airtime purchase reference was not found.")
        return await self._build_response(transaction, include_metadata=True)

    def generate_reference(self) -> str:
        """Create a unique reference for a new airtime purchase."""
        return f"airtime-{uuid4().hex[:12]}"

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
        self._require_wallet_repository()
        metadata = self._parse_metadata(transaction.metadata_payload)
        if metadata.get("debit_applied") is True:
            return
        if wallet.available_balance < amount:
            raise WalletException("Insufficient wallet balance for airtime purchase.")
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
        self.logger.info("airtime_purchase_wallet_debited", extra={"reference": transaction.reference, "amount": str(amount)})

    async def _reverse_wallet_debit(self, *, transaction: Transaction, reason: str | None = None) -> None:
        self._require_wallet_repository()
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
        metadata["reversal_reason"] = reason or "provider_failure"
        transaction.metadata_payload = self._serialize_metadata(metadata)
        await self.transaction_repository.update_transaction(transaction, metadata_payload=transaction.metadata_payload)
        self.logger.info("airtime_purchase_reversed", extra={"reference": transaction.reference, "reason": reason})

    async def _handle_provider_failure(self, *, transaction: Transaction, reason: str) -> None:
        async with self._transaction_scope():
            current = await self.transaction_repository.get_by_reference(transaction.reference)
            if current is None:
                raise ValidationException("Purchase transaction was not found.")
            await self._reverse_wallet_debit(transaction=current, reason=reason)
            current.status = "failed"
            current.metadata_payload = self._serialize_metadata({"failure_reason": reason, "reversed": True})
            await self.transaction_repository.update_transaction(current, status=current.status, metadata_payload=current.metadata_payload)
            self.logger.warning("airtime_purchase_failed", extra={"reference": current.reference, "reason": reason})

    async def _resolve_transaction(self, *, transaction: Transaction | None, reference: str | None) -> Transaction:
        if transaction is not None:
            return transaction
        if reference is None:
            raise ValidationException("Airtime purchase reference is required.")
        resolved = await self.transaction_repository.get_by_reference(reference)
        if resolved is None:
            raise ValidationException("Airtime purchase reference was not found.")
        return resolved

    async def _build_response(self, transaction: Transaction, *, include_metadata: bool = False) -> dict[str, Any]:
        response: dict[str, Any] = {
            "reference": transaction.reference,
            "status": transaction.status,
            "amount": str(transaction.amount),
            "currency": transaction.currency,
            "provider": transaction.provider_name,
            "provider_reference": transaction.provider_reference,
            "provider_transaction_id": transaction.provider_transaction_id,
            "wallet_id": str(transaction.wallet_id) if transaction.wallet_id else None,
        }
        if include_metadata:
            response["metadata"] = self._parse_metadata(transaction.metadata_payload)
        return response

    def _validate_purchase_request(self, *, user_id: UUID, phone_number: str, amount: Decimal, transaction_pin: str) -> None:
        if user_id is None:
            raise ValidationException("User ID is required.")
        if not phone_number or not isinstance(phone_number, str) or not phone_number.strip():
            raise ValidationException("Phone number is required.")
        if amount <= 0:
            raise ValidationException("Airtime amount must be greater than zero.")
        if not transaction_pin or not isinstance(transaction_pin, str) or not transaction_pin.strip():
            raise ValidationException("Transaction PIN is required.")

    def _validate_provider_payload(self, payload: dict[str, Any]) -> None:
        if not payload:
            raise ValidationException("Provider payload is required.")

    def _normalize_provider_response(self, result: Any, provider: Provider) -> dict[str, Any]:
        if isinstance(result, dict):
            payload = result
        else:
            payload = {"value": result}
        return {
            "status": self._normalize_status(payload.get("status")),
            "provider": provider.name,
            "provider_reference": payload.get("provider_reference") or payload.get("reference"),
            "provider_transaction_id": payload.get("provider_transaction_id") or payload.get("transaction_id"),
            "message": payload.get("message"),
            "metadata": payload.get("metadata"),
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
        metadata: dict[str, Any] | Any = response_data
        if isinstance(response_data, dict):
            provider_reference = response_data.get("transaction_hash") or response_data.get("ref")
            provider_transaction_id = response_data.get("transaction_id") or response_data.get("transaction_hash")
            message = response_data.get("message")

        if provider_reference is None and isinstance(payload, dict):
            provider_reference = payload.get("provider_reference") or payload.get("ref")
        if provider_transaction_id is None and isinstance(payload, dict):
            provider_transaction_id = payload.get("provider_transaction_id")
        if message is None and isinstance(payload, dict):
            message = payload.get("message")

        return {
            "status": self._normalize_status(status_value),
            "provider": provider_name or (response_data.get("provider") if isinstance(response_data, dict) else None),
            "provider_reference": provider_reference,
            "provider_transaction_id": provider_transaction_id,
            "message": message or result.get("message"),
            "metadata": metadata,
        }

    def _normalize_status(self, status: str | None) -> str:
        if not status:
            return "pending"
        lowered = str(status).strip().lower()
        mapping = {
            "success": "succeeded",
            "successful": "succeeded",
            "succeeded": "succeeded",
            "completed": "completed",
            "settled": "settled",
            "failed": "failed",
            "failure": "failed",
            "error": "failed",
            "cancelled": "cancelled",
            "reversed": "reversed",
            "pending": "pending",
            "processing": "pending",
            "in-progress": "pending",
        }
        return mapping.get(lowered, lowered)

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

    def _extract_phone_number(self, transaction: Transaction) -> str | None:
        metadata = self._parse_metadata(transaction.metadata_payload)
        return metadata.get("phone_number")

    def _extract_network(self, transaction: Transaction) -> str | None:
        metadata = self._parse_metadata(transaction.metadata_payload)
        network = metadata.get("network")
        if isinstance(network, str) and network.strip():
            return network.strip()
        return None

    def _normalize_amount(self, amount: Decimal | float | int) -> Decimal:
        if isinstance(amount, Decimal):
            return amount
        return Decimal(str(amount))

    def _require_wallet_repository(self) -> None:
        if self.wallet_repository is None:
            raise RuntimeError("Wallet repository is not configured for airtime purchases.")

    def _transaction_scope(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def scope():
            try:
                async with self.transaction_repository.session.begin():
                    yield
            except Exception:
                raise

        return scope()


__all__ = ["AirtimePurchaseService"]
