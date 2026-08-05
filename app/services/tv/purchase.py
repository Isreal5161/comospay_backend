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


class TVPurchaseService:
    """Manage TV subscription purchase workflows using injected wallet, provider, and repository services."""

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

    async def purchase_tv(
        self,
        *,
        user_id: UUID,
        provider: str,
        smart_card_number: str,
        amount: Decimal | float | int,
        transaction_pin: str,
        package_code: str | None = None,
        service_type: str | None = None,
        wallet_id: UUID | None = None,
        currency: str = "NGN",
        description: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Create a pending TV subscription purchase and dispatch it through provider orchestration."""
        amount_value = self._normalize_amount(amount)
        self._validate_purchase_request(
            user_id=user_id,
            provider=provider,
            smart_card_number=smart_card_number,
            amount=amount_value,
            transaction_pin=transaction_pin,
            package_code=package_code,
        )

        await self._ensure_user_exists(user_id)
        wallet = await self._resolve_wallet(user_id=user_id, wallet_id=wallet_id)
        await self.wallet_service.verify_transaction_pin(user_id=user_id, pin=transaction_pin)

        if not wallet.is_active or wallet.is_frozen or wallet.is_suspended:
            raise WalletException("Wallet is not active for TV purchase.")
        if wallet.available_balance < amount_value:
            raise WalletException("Insufficient wallet balance for TV purchase.")

        reference = self.generate_reference()
        self.logger.info(
            "tv_purchase_started",
            extra={
                "reference": reference,
                "user_id": str(user_id),
                "provider": provider,
                "smart_card_number": smart_card_number,
                "amount": str(amount_value),
                "package_code": package_code,
                "service_type": service_type,
            },
        )

        async with self._transaction_scope():
            transaction = Transaction(
                reference=reference,
                user_id=user_id,
                wallet_id=wallet.id,
                transaction_type="tv_purchase",
                category="tv",
                amount=amount_value,
                currency=currency,
                charges=Decimal("0"),
                total_amount=amount_value,
                status="pending",
                provider_name=provider_name,
                description=description or f"TV subscription purchase for {smart_card_number}",
                metadata_payload=self._serialize_metadata(
                    {
                        "provider": provider,
                        "smart_card_number": smart_card_number,
                        "package_code": package_code,
                        "service_type": service_type,
                        "wallet_id": str(wallet.id),
                        "requested_amount": str(amount_value),
                    }
                ),
            )
            transaction = await self.transaction_repository.create_transaction(transaction)
            await self._debit_wallet(transaction=transaction, wallet=wallet, amount=amount_value)

        return await self.process_tv_purchase(
            transaction=transaction,
            provider_operation=provider_operation,
            provider_name=provider_name,
            metadata_payload=metadata_payload,
        )

    async def process_tv_purchase(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Dispatch a pending TV purchase to the provider and update its outcome."""
        resolved = await self._resolve_transaction(transaction=transaction, reference=reference)

        self.logger.info("tv_purchase_provider_execution", extra={"reference": resolved.reference})
        try:
            provider_response = self._normalize_provider_manager_response(
                await self.provider_manager.execute(
                    "subscribe_tv",
                    smart_card_number=self._extract_smart_card(resolved),
                    provider_code=self._extract_provider_code(resolved),
                    package=self._extract_package(resolved),
                    package_code=self._extract_package_code(resolved),
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
                    reason=provider_response.get("message") or "Provider reported a failed TV purchase.",
                )
            elif status in {"succeeded", "completed", "settled"}:
                self.logger.info("tv_purchase_succeeded", extra={"reference": transaction_record.reference})
            else:
                self.logger.info("tv_purchase_pending", extra={"reference": transaction_record.reference, "status": status})

            return await self._build_response(transaction_record)

    async def retry_tv_purchase(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Retry a pending or failed TV purchase through provider orchestration."""
        resolved = await self._resolve_transaction(transaction=transaction, reference=reference)
        if resolved.status in {"succeeded", "completed", "settled"}:
            return await self._build_response(resolved)

        wallet = await self._get_wallet_for_transaction(resolved)
        if resolved.status in {"failed", "cancelled", "reversed"}:
            await self._debit_wallet(transaction=resolved, wallet=wallet, amount=resolved.amount)

        self.logger.info("tv_purchase_retry", extra={"reference": resolved.reference, "status": resolved.status})
        return await self.process_tv_purchase(
            transaction=resolved,
            provider_operation=provider_operation,
            provider_name=provider_name,
            metadata_payload=metadata_payload,
        )

    async def get_purchase_status(self, *, reference: str) -> dict[str, Any]:
        """Retrieve the current lifecycle state of a TV purchase."""
        if not reference or not isinstance(reference, str) or not reference.strip():
            raise ValidationException("TV purchase reference is required.")
        transaction = await self.transaction_repository.get_by_reference(reference)
        if transaction is None:
            raise ValidationException("TV purchase reference was not found.")
        return await self._build_response(transaction)

    async def get_purchase_details(self, *, reference: str) -> dict[str, Any]:
        """Fetch detailed TV purchase transaction data."""
        transaction = await self.transaction_repository.get_by_reference(reference)
        if transaction is None:
            raise ValidationException("TV purchase reference was not found.")
        return await self._build_response(transaction, include_metadata=True)

    def generate_reference(self) -> str:
        """Create a unique reference for a new TV purchase."""
        return f"tv-{uuid4().hex[:12]}"

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

    async def _get_wallet_for_transaction(self, transaction: Transaction) -> Wallet:
        if transaction.wallet_id is None:
            raise ValidationException("Transaction wallet was not found.")
        wallet = await self.wallet_repository.get_by_id(transaction.wallet_id)
        if wallet is None:
            raise ValidationException("Wallet was not found.")
        return wallet

    async def _debit_wallet(self, *, transaction: Transaction, wallet: Wallet, amount: Decimal) -> None:
        if wallet.available_balance < amount:
            raise WalletException("Insufficient wallet balance for TV purchase.")
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
        metadata["reversal_reason"] = reason or "tv_purchase_reversed"
        transaction.metadata_payload = self._serialize_metadata(metadata)
        await self.transaction_repository.update_transaction(transaction, metadata_payload=transaction.metadata_payload)

    async def _handle_provider_failure(self, *, transaction: Transaction, reason: str) -> None:
        self.logger.warning("tv_purchase_failed", extra={"reference": transaction.reference, "reason": reason})
        if transaction.status not in {"failed", "cancelled", "reversed"}:
            transaction.status = "failed"
            transaction.metadata_payload = self._serialize_metadata(
                {
                    **self._parse_metadata(transaction.metadata_payload),
                    "failure_reason": reason,
                }
            )
            await self.transaction_repository.update_transaction(transaction, status=transaction.status, metadata_payload=transaction.metadata_payload)

    async def _resolve_transaction(self, transaction: Transaction | None, reference: str | None) -> Transaction:
        if transaction is not None:
            return transaction
        if not reference or not isinstance(reference, str) or not reference.strip():
            raise ValidationException("TV purchase transaction reference is required.")
        resolved = await self.transaction_repository.get_by_reference(reference.strip())
        if resolved is None:
            raise ValidationException("TV purchase transaction was not found.")
        return resolved

    def _validate_purchase_request(
        self,
        *,
        user_id: UUID,
        provider: str,
        smart_card_number: str,
        amount: Decimal,
        transaction_pin: str,
        package_code: str | None = None,
    ) -> None:
        if user_id is None:
            raise ValidationException("User ID is required.")
        if not provider or not isinstance(provider, str) or not provider.strip():
            raise ValidationException("TV provider is required.")
        if not smart_card_number or not isinstance(smart_card_number, str) or not smart_card_number.strip():
            raise ValidationException("Smart card number is required.")
        if amount <= Decimal("0"):
            raise ValidationException("Amount must be greater than zero.")
        if not transaction_pin or not isinstance(transaction_pin, str) or not transaction_pin.strip():
            raise ValidationException("Transaction PIN is required.")
        if package_code is not None and not isinstance(package_code, str):
            raise ValidationException("Package code must be a string.")

    def _normalize_amount(self, amount: Decimal | float | int) -> Decimal:
        if isinstance(amount, Decimal):
            return amount
        return Decimal(str(amount))

    async def _build_response(self, transaction: Transaction, include_metadata: bool = False) -> dict[str, Any]:
        response: dict[str, Any] = {
            "reference": transaction.reference,
            "status": transaction.status,
            "amount": str(transaction.amount),
            "currency": transaction.currency,
            "provider_name": transaction.provider_name,
            "provider_reference": transaction.provider_reference,
            "provider_transaction_id": transaction.provider_transaction_id,
            "description": transaction.description,
            "created_at": transaction.created_at.isoformat(),
            "updated_at": transaction.updated_at.isoformat(),
        }
        if include_metadata:
            response["metadata_payload"] = self._parse_metadata(transaction.metadata_payload)
        return response

    def _extract_provider(self, transaction: Transaction) -> str:
        payload = self._parse_metadata(transaction.metadata_payload)
        return str(payload.get("provider") or "").strip()

    def _extract_provider_code(self, transaction: Transaction) -> str:
        payload = self._parse_metadata(transaction.metadata_payload)
        return str(payload.get("provider") or "").strip()

    def _extract_smart_card(self, transaction: Transaction) -> str:
        payload = self._parse_metadata(transaction.metadata_payload)
        return str(payload.get("smart_card_number") or "").strip()

    def _extract_package_code(self, transaction: Transaction) -> str | None:
        payload = self._parse_metadata(transaction.metadata_payload)
        return str(payload.get("package_code") or "").strip() or None

    def _extract_package(self, transaction: Transaction) -> str:
        payload = self._parse_metadata(transaction.metadata_payload)
        package = str(payload.get("service_type") or "").strip()
        if package:
            return package
        return str(payload.get("package_code") or "").strip()

    def _extract_service_type(self, transaction: Transaction) -> str | None:
        payload = self._parse_metadata(transaction.metadata_payload)
        return str(payload.get("service_type") or "").strip() or None

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

    def _normalize_status(self, status: Any) -> str:
        if not status or not isinstance(status, str):
            return "pending"
        normalized = status.strip().lower()
        if normalized in {"success", "succeeded", "completed", "settled"}:
            return "succeeded"
        if normalized in {"failed", "failure", "cancelled", "cancelled", "reversed"}:
            return "failed"
        if normalized in {"pending", "processing", "queued"}:
            return "pending"
        return "pending"

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
            provider_reference = response_data.get("transaction_hash") or response_data.get("ref") or response_data.get("provider_reference")
            provider_transaction_id = response_data.get("transaction_id") or response_data.get("transaction_hash") or response_data.get("provider_transaction_id")
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

    def _transaction_scope(self):
        return self.transaction_repository.session.begin()


__all__ = ["TVPurchaseService"]
