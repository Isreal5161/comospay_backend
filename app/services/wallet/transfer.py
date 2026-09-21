from __future__ import annotations

import inspect
import logging
import re
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction
from app.models.wallet import Wallet
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.wallet_repository import WalletRepository
from app.utils.exceptions import DatabaseException, ProviderException, ValidationException, WalletException


class WalletTransferService:
    """Implement wallet transfer workflows with validation, fees, limits, and atomic updates."""

    def __init__(
        self,
        *,
        wallet_repository: WalletRepository,
        transaction_repository: TransactionRepository,
        provider_service: Any | None = None,
        session: AsyncSession | None = None,
        logger: logging.Logger | None = None,
        audit_service: Any | None = None,
    ) -> None:
        self.wallet_repository = wallet_repository
        self.transaction_repository = transaction_repository
        self.provider_service = provider_service
        self.session = session
        self.logger = logger or logging.getLogger(__name__)
        self.audit_service = audit_service

    async def transfer_between_users(
        self,
        *,
        sender_user_id: UUID,
        sender_wallet_id: UUID,
        recipient_user_id: UUID,
        recipient_wallet_id: UUID | None = None,
        amount: Decimal | float | int,
        transaction_pin: str | None = None,
        description: str | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Transfer funds between wallets owned by different users."""
        self._require_repository(self.wallet_repository)
        self._require_repository(self.transaction_repository)

        amount_value = self._normalize_amount(amount)
        await self.validate_transfer(
            wallet_id=sender_wallet_id,
            amount=amount_value,
            transaction_pin=transaction_pin,
            transfer_type="internal",
        )
        await self.validate_daily_limit(user_id=sender_user_id, amount=amount_value)

        try:
            async with self._session_scope():
                source_wallet = await self._load_wallet_for_update(sender_wallet_id)
                if source_wallet is None:
                    raise ValidationException("Wallet not found.")
                if source_wallet.user_id != sender_user_id:
                    raise WalletException("Wallet ownership mismatch.")
                if source_wallet.is_frozen or source_wallet.is_suspended or not source_wallet.is_active:
                    raise WalletException("Source wallet is not active for transfer.")

                recipient_wallet = await self._get_recipient_wallet(recipient_user_id=recipient_user_id, recipient_wallet_id=recipient_wallet_id)
                if recipient_wallet is None:
                    raise ValidationException("Recipient wallet not found.")
                if recipient_wallet.id == source_wallet.id:
                    raise ValidationException("Sender and recipient wallets must be different.")

                if recipient_wallet.is_frozen or recipient_wallet.is_suspended or not recipient_wallet.is_active:
                    raise WalletException("Recipient wallet is not active for transfer.")

                transaction = Transaction(
                    reference=self._make_reference("trf"),
                    user_id=sender_user_id,
                    wallet_id=source_wallet.id,
                    transaction_type="wallet_transfer_internal",
                    category="transfer",
                    amount=amount_value,
                    currency=source_wallet.currency,
                    charges=Decimal("0.00"),
                    total_amount=amount_value,
                    status="pending",
                    description=description or "Internal wallet transfer",
                    metadata_payload=self._sanitize_metadata(metadata_payload),
                )
                await self.transaction_repository.create_transaction(transaction)

                debit_result = await self.debit_wallet(
                    wallet_id=source_wallet.id,
                    amount=amount_value,
                    transaction=transaction,
                    reference=transaction.reference,
                )
                recipient_wallet_after_debit = await self._load_wallet_for_update(recipient_wallet.id)
                if recipient_wallet_after_debit is None:
                    raise ValidationException("Recipient wallet not found.")

                recipient_wallet_after_debit.available_balance = recipient_wallet_after_debit.available_balance + amount_value
                recipient_wallet_after_debit.ledger_balance = recipient_wallet_after_debit.ledger_balance + amount_value
                await self.wallet_repository.update_balance_fields(
                    recipient_wallet_after_debit,
                    available_balance=recipient_wallet_after_debit.available_balance,
                    ledger_balance=recipient_wallet_after_debit.ledger_balance,
                )

                transaction.status = "completed"
                transaction.metadata_payload = self._merge_metadata(transaction.metadata_payload, {"recipient_wallet_id": str(recipient_wallet_after_debit.id)})
                await self.transaction_repository.update_transaction(transaction, status=transaction.status, metadata_payload=transaction.metadata_payload)
                await self._log_event("transfer_between_users_completed", user_id=sender_user_id, metadata={"reference": transaction.reference})
                return {
                    "transaction_id": str(transaction.id),
                    "reference": transaction.reference,
                    "status": transaction.status,
                    "source_wallet_id": str(source_wallet.id),
                    "recipient_wallet_id": str(recipient_wallet.id),
                    "amount": str(transaction.amount),
                    "currency": source_wallet.currency,
                    "fee": str(await self.calculate_transfer_fee(amount=amount_value, transfer_type="internal")),
                }
        except WalletException:
            raise
        except ValidationException:
            raise
        except Exception as exc:
            raise DatabaseException("Internal wallet transfer failed.") from exc

    async def transfer_to_bank(
        self,
        *,
        user_id: UUID,
        wallet_id: UUID,
        amount: Decimal | float | int,
        bank_code: str,
        account_number: str,
        account_name: str | None = None,
        transaction_pin: str | None = None,
        description: str | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Transfer funds from a wallet to a bank account through a provider abstraction."""
        self._require_repository(self.wallet_repository)
        self._require_repository(self.transaction_repository)
        self._require_dependency(self.provider_service, "provider_service")

        amount_value = self._normalize_amount(amount)
        await self.validate_transfer(wallet_id=wallet_id, amount=amount_value, transaction_pin=transaction_pin, transfer_type="bank")
        await self.validate_daily_limit(user_id=user_id, amount=amount_value)

        try:
            async with self._session_scope():
                wallet = await self._load_wallet_for_update(wallet_id)
                if wallet is None:
                    raise ValidationException("Wallet not found.")
                if wallet.user_id != user_id:
                    raise WalletException("Wallet ownership mismatch.")
                if wallet.is_frozen or wallet.is_suspended or not wallet.is_active:
                    raise WalletException("Wallet is not active for transfer.")

                transaction = Transaction(
                    reference=self._make_reference("bank"),
                    user_id=user_id,
                    wallet_id=wallet.id,
                    transaction_type="wallet_transfer_bank",
                    category="transfer",
                    amount=amount_value,
                    currency=wallet.currency,
                    charges=await self.calculate_transfer_fee(amount=amount_value, transfer_type="bank"),
                    total_amount=amount_value + (await self.calculate_transfer_fee(amount=amount_value, transfer_type="bank")),
                    status="pending",
                    description=description or "Bank transfer",
                    metadata_payload=self._sanitize_metadata(metadata_payload),
                )
                await self.transaction_repository.create_transaction(transaction)

                provider_payload = await self._dispatch_provider_call(
                    "transfer_to_bank",
                    user_id=user_id,
                    wallet_id=wallet.id,
                    amount=amount_value,
                    bank_code=bank_code,
                    account_number=account_number,
                    account_name=account_name,
                    reference=transaction.reference,
                )
                transaction.provider_reference = provider_payload.get("provider_reference") or transaction.provider_reference
                transaction.external_reference = provider_payload.get("external_reference") or transaction.external_reference
                transaction.metadata_payload = self._merge_metadata(transaction.metadata_payload, provider_payload)

                if provider_payload.get("status") in {"completed", "success", "successful", "succeeded"}:
                    await self.debit_wallet(wallet_id=wallet.id, amount=amount_value, transaction=transaction, reference=transaction.reference)
                    transaction.status = "completed"
                    await self.transaction_repository.update_transaction(transaction, status=transaction.status, metadata_payload=transaction.metadata_payload)
                    await self._log_event("transfer_to_bank_completed", user_id=user_id, metadata={"reference": transaction.reference})
                    return {
                        "transaction_id": str(transaction.id),
                        "reference": transaction.reference,
                        "status": transaction.status,
                        "wallet_id": str(wallet.id),
                        "amount": str(transaction.amount),
                        "currency": wallet.currency,
                        "fee": str(transaction.charges),
                    }

                transaction.status = self._normalize_status(provider_payload.get("status"), default="pending")
                await self.transaction_repository.update_transaction(transaction, status=transaction.status, metadata_payload=transaction.metadata_payload)
                await self._log_event("transfer_to_bank_pending", user_id=user_id, metadata={"reference": transaction.reference})
                return {
                    "transaction_id": str(transaction.id),
                    "reference": transaction.reference,
                    "status": transaction.status,
                    "wallet_id": str(wallet.id),
                    "amount": str(transaction.amount),
                    "currency": wallet.currency,
                    "fee": str(transaction.charges),
                }
        except ProviderException:
            raise
        except ValidationException:
            raise
        except WalletException:
            raise
        except Exception as exc:
            raise DatabaseException("Bank transfer failed.") from exc

    async def validate_transfer(
        self,
        *,
        wallet_id: UUID,
        amount: Decimal | float | int,
        transaction_pin: str | None = None,
        transfer_type: str = "internal",
    ) -> dict[str, Any]:
        """Validate the core transfer parameters and wallet state."""
        self._require_repository(self.wallet_repository)
        amount_value = self._normalize_amount(amount)
        if amount_value <= 0:
            raise ValidationException("Transfer amount must be greater than zero.")

        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if wallet is None:
            raise ValidationException("Wallet not found.")
        if wallet.is_frozen or wallet.is_suspended or not wallet.is_active:
            raise WalletException("Wallet is not eligible for transfer.")

        if transfer_type == "bank":
            await self.validate_transaction_pin(transaction_pin=transaction_pin)

        return {
            "wallet_id": str(wallet.id),
            "currency": wallet.currency,
            "valid": True,
            "amount": str(amount_value),
        }

    async def calculate_transfer_fee(self, *, amount: Decimal | float | int, transfer_type: str = "internal") -> Decimal:
        """Calculate a simple transfer fee model that can be adapted for providers later."""
        amount_value = self._normalize_amount(amount)
        if transfer_type == "bank":
            fee = amount_value * Decimal("0.015")
            return fee.quantize(Decimal("0.01"))
        return Decimal("0.00")

    async def validate_daily_limit(self, *, user_id: UUID, amount: Decimal | float | int) -> dict[str, Any]:
        """Validate that the transfer amount does not exceed a daily limit."""
        amount_value = self._normalize_amount(amount)
        daily_limit = Decimal("1000000.00")
        if amount_value > daily_limit:
            raise ValidationException("Transfer exceeds the daily limit.")
        return {"daily_limit": str(daily_limit), "remaining": str(daily_limit - amount_value)}

    async def validate_transaction_pin(self, *, transaction_pin: str | None) -> bool:
        """Validate a transaction PIN for transfer authorization."""
        if transaction_pin is None:
            raise ValidationException("Transaction PIN is required.")
        if not re.fullmatch(r"\d{4,6}", transaction_pin):
            raise ValidationException("Transaction PIN must be numeric and between 4 and 6 digits.")
        return True

    async def debit_wallet(self, *, wallet_id: UUID, amount: Decimal | float | int, transaction: Transaction | None = None, reference: str | None = None) -> dict[str, Any]:
        """Debit a wallet atomically and update the transaction state."""
        self._require_repository(self.wallet_repository)
        self._require_repository(self.transaction_repository)

        amount_value = self._normalize_amount(amount)
        wallet = await self._load_wallet_for_update(wallet_id)
        if wallet is None:
            raise ValidationException("Wallet not found.")
        if wallet.available_balance < amount_value:
            raise WalletException("Insufficient wallet balance.")
        if wallet.is_frozen or wallet.is_suspended or not wallet.is_active:
            raise WalletException("Wallet is not active for transfer.")

        updated_available = wallet.available_balance - amount_value
        updated_ledger = wallet.ledger_balance - amount_value
        wallet.available_balance = updated_available
        wallet.ledger_balance = updated_ledger
        await self.wallet_repository.update_balance_fields(
            wallet,
            available_balance=updated_available,
            ledger_balance=updated_ledger,
        )

        if transaction is not None:
            transaction.status = "completed"
            if reference:
                transaction.reference = reference
            await self.transaction_repository.update_transaction(transaction, status=transaction.status)

        await self._log_event("wallet_debited", user_id=wallet.user_id, metadata={"wallet_id": str(wallet.id), "amount": str(amount_value)})
        return {"wallet_id": str(wallet.id), "remaining_balance": str(updated_available)}

    async def reverse_transfer(self, *, transaction_id: UUID | None = None, reference: str | None = None, reason: str | None = None) -> dict[str, Any]:
        """Reverse a completed transfer and restore the debited wallet balance."""
        self._require_repository(self.wallet_repository)
        self._require_repository(self.transaction_repository)

        transaction = await self._get_transaction(transaction_id=transaction_id, reference=reference)
        if transaction is None:
            raise ValidationException("Transfer transaction not found.")
        if transaction.status.lower() != "completed":
            raise WalletException("Only completed transfers can be reversed.")

        wallet_id = transaction.wallet_id
        if wallet_id is None:
            raise ValidationException("Transfer transaction is missing a wallet identifier.")

        wallet = await self._load_wallet_for_update(wallet_id)
        if wallet is None:
            raise ValidationException("Wallet not found.")

        try:
            async with self._session_scope():
                wallet.available_balance = wallet.available_balance + transaction.amount
                wallet.ledger_balance = wallet.ledger_balance + transaction.amount
                await self.wallet_repository.update_balance_fields(
                    wallet,
                    available_balance=wallet.available_balance,
                    ledger_balance=wallet.ledger_balance,
                )
                transaction.status = "reversed"
                transaction.metadata_payload = self._merge_metadata(transaction.metadata_payload, {"reversal_reason": reason})
                await self.transaction_repository.update_transaction(transaction, status=transaction.status, metadata_payload=transaction.metadata_payload)
                await self._log_event("transfer_reversed", user_id=transaction.user_id, metadata={"reference": transaction.reference})
                return {"transaction_id": str(transaction.id), "status": transaction.status, "wallet_id": str(wallet.id)}
        except Exception as exc:
            raise DatabaseException("Transfer reversal failed.") from exc

    async def _get_recipient_wallet(self, *, recipient_user_id: UUID, recipient_wallet_id: UUID | None) -> Wallet | None:
        if recipient_wallet_id is not None:
            return await self.wallet_repository.get_by_id(recipient_wallet_id)
        return await self.wallet_repository.get_user_wallet(user_id=recipient_user_id)

    async def _get_transaction(self, *, transaction_id: UUID | None = None, reference: str | None = None) -> Transaction | None:
        if transaction_id is not None:
            return await self.transaction_repository.get_by_id(transaction_id)
        if reference is not None:
            return await self.transaction_repository.get_by_reference(reference)
        return None

    async def _load_wallet_for_update(self, wallet_id: UUID) -> Wallet | None:
        session = self._resolve_session()
        if session is None:
            return await self.wallet_repository.get_by_id(wallet_id)
        stmt = select(Wallet).where(Wallet.id == wallet_id).with_for_update()
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def _dispatch_provider_call(self, method_name: str, **kwargs: Any) -> dict[str, Any]:
        if self.provider_service is None:
            raise ProviderException("Provider service is not configured.")
        for candidate in (method_name, f"{method_name}_provider", f"process_{method_name}"):
            handler = getattr(self.provider_service, candidate, None)
            if callable(handler):
                return await self._resolve_provider_response(handler(**kwargs))
        raise ProviderException("No provider handler is configured for this transfer flow.")

    async def _resolve_provider_response(self, response: object) -> dict[str, Any]:
        resolved = response
        if inspect.isawaitable(response):
            resolved = await response
        if isinstance(resolved, dict):
            return resolved
        return {}

    async def _log_event(self, event_name: str, *, user_id: UUID | None = None, metadata: dict[str, Any] | None = None) -> None:
        self.logger.info(
            "wallet_transfer_event",
            extra={"event": event_name, "user_id": str(user_id) if user_id else None, "metadata": metadata or {}},
        )
        if self.audit_service is not None:
            try:
                await self.audit_service(event_name, user_id=user_id, metadata=metadata)
            except TypeError:
                self.audit_service(event_name, user_id=user_id, metadata=metadata)

    def _normalize_amount(self, amount: Decimal | float | int) -> Decimal:
        if isinstance(amount, Decimal):
            return amount
        return Decimal(str(amount))

    def _normalize_status(self, status: str | None, *, default: str) -> str:
        if not status:
            return default
        lowered = status.strip().lower()
        if lowered in {"success", "successful", "succeeded", "completed", "complete", "approved"}:
            return "completed"
        if lowered in {"pending", "processing", "initialized", "in-progress"}:
            return "pending"
        if lowered in {"failed", "failure", "declined", "rejected", "error"}:
            return "failed"
        return lowered

    def _sanitize_metadata(self, value: str | None) -> str | None:
        if value is None:
            return None
        sanitized = value.strip()
        if len(sanitized) > 1000:
            raise ValidationException("metadata_payload is too long.")
        return sanitized or None

    def _merge_metadata(self, existing: str | None, payload: dict[str, Any] | None) -> str | None:
        if not payload:
            return existing
        import json

        existing_data: dict[str, Any] = {}
        if existing:
            try:
                existing_data = json.loads(existing)
            except json.JSONDecodeError:
                existing_data = {"value": existing}
        existing_data.update(payload)
        return json.dumps(existing_data, default=str)

    def _make_reference(self, prefix: str) -> str:
        return f"{prefix}-{uuid4().hex[:12]}"

    def _require_repository(self, repository: Any | None) -> None:
        if repository is None:
            raise RuntimeError("Required repository is not configured for WalletTransferService.")

    def _require_dependency(self, dependency: Any | None, name: str) -> None:
        if dependency is None:
            raise RuntimeError(f"Required dependency '{name}' is not configured for WalletTransferService.")

    def _resolve_session(self) -> AsyncSession | None:
        if self.session is not None:
            return self.session
        repository_session = getattr(self.transaction_repository, "session", None)
        if isinstance(repository_session, AsyncSession):
            return repository_session
        repository_session = getattr(self.wallet_repository, "session", None)
        if isinstance(repository_session, AsyncSession):
            return repository_session
        return None

    def _session_scope(self) -> Any:
        session = self._resolve_session()
        if session is None:
            return _NullSessionContext()
        if session.in_transaction():
            return _ActiveSessionContext(session)
        return session.begin()


class _ActiveSessionContext:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def __aenter__(self) -> None:
        if not self.session.in_transaction():
            raise RuntimeError("Expected an active database transaction.")
        return None

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class _NullSessionContext:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False
