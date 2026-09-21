from __future__ import annotations

import inspect
import json
import logging
import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Callable
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider import Provider
from app.models.transaction import Transaction
from app.models.wallet import Wallet
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.user.bank_account import BankAccountService
from app.utils.exceptions import DatabaseException, ProviderException, ValidationException, WalletException


class WalletWithdrawalService:
    """Reserve wallet funds for a future provider-independent withdrawal."""

    MIN_AMOUNT = Decimal("0.01")
    MAX_AMOUNT = Decimal("999999999.99")
    DAILY_LIMIT = Decimal("1000000.00")

    def __init__(
        self,
        *,
        wallet_repository: WalletRepository,
        transaction_repository: TransactionRepository,
        bank_account_service: BankAccountService | None = None,
        provider_service: Any | None = None,
        provider_adapter: Any | None = None,
        provider_adapter_factory: Callable[[Provider], Any] | None = None,
        session: AsyncSession | None = None,
        logger: logging.Logger | None = None,
        audit_service: Any | None = None,
    ) -> None:
        self.wallet_repository = wallet_repository
        self.transaction_repository = transaction_repository
        self.provider_service = provider_service
        self.provider_adapter = provider_adapter
        self.provider_adapter_factory = provider_adapter_factory
        self.bank_account_service = bank_account_service
        self.session = session
        self.logger = logger or logging.getLogger(__name__)
        self.audit_service = audit_service

    async def create_withdrawal(
        self,
        *,
        user_id: UUID,
        amount: Decimal | float | int,
        currency: str,
        bank_account_id: UUID,
        description: str | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Reserve funds and create a withdrawal transaction without calling a provider."""
        self._require_repository(self.wallet_repository)
        self._require_repository(self.transaction_repository)

        amount_value = self._normalize_amount(amount)
        normalized_currency = currency.strip().upper()
        if not normalized_currency or len(normalized_currency) > 10:
            raise ValidationException("Withdrawal currency is invalid.")
        if self.bank_account_service is None:
            raise ValidationException("Trusted bank account service is not configured.")
        self._validate_amount(amount_value)
        await self.validate_daily_limit(user_id=user_id, amount=amount_value)

        try:
            async with self._session_scope():
                wallet = await self._load_user_wallet_for_update(user_id)
                if wallet is None:
                    raise ValidationException("Wallet not found for authenticated user.")
                if wallet.is_frozen or wallet.is_suspended or not wallet.is_active:
                    raise WalletException("Wallet is not active for withdrawal.")
                if wallet.currency.upper() != normalized_currency:
                    raise ValidationException("Withdrawal currency does not match wallet currency.")
                if wallet.available_balance < amount_value:
                    raise WalletException("Insufficient wallet balance.")
                bank_account = await self.bank_account_service.resolve_withdrawal_account(
                    user_id=user_id,
                    bank_account_id=bank_account_id,
                )

                fee = await self.calculate_withdrawal_fee(amount=amount_value)
                metadata = json.dumps(
                    {
                        "bank_account_id": str(bank_account_id),
                        "account_number_last4": bank_account["account_number"][-4:],
                    }
                )
                transaction = Transaction(
                    reference=self._make_reference("wdl"),
                    user_id=user_id,
                    wallet_id=wallet.id,
                    bank_account_id=bank_account_id,
                    transaction_type="wallet_withdrawal_bank",
                    category="withdrawal",
                    amount=amount_value,
                    currency=wallet.currency,
                    charges=fee,
                    total_amount=amount_value + fee,
                    status="funds_reserved",
                    description=description or "Bank withdrawal",
                    metadata_payload=metadata,
                )
                wallet.available_balance -= amount_value
                wallet.locked_balance += amount_value
                await self.wallet_repository.update_balance_fields(
                    wallet,
                    available_balance=wallet.available_balance,
                    locked_balance=wallet.locked_balance,
                )
                await self.transaction_repository.create_transaction(transaction)
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
            raise DatabaseException("Withdrawal reservation failed.") from exc

    async def withdraw_to_bank(self, **kwargs: Any) -> dict[str, Any]:
        """Backward-compatible alias for the provider-free withdrawal foundation."""
        return await self.create_withdrawal(**kwargs)

    async def execute_withdrawal(
        self,
        *,
        transaction_id: UUID | None = None,
        reference: str | None = None,
        account_details: Mapping[str, Any] | None = None,
        authenticated_user_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Execute one reserved withdrawal and settle its wallet exactly once."""
        self._require_repository(self.wallet_repository)
        self._require_repository(self.transaction_repository)
        if self.provider_service is None or (
            self.provider_adapter is None and self.provider_adapter_factory is None
        ):
            raise ProviderException("Withdrawal provider execution is not configured.")

        try:
            async with self._session_scope():
                transaction = await self._get_transaction_for_update(
                    transaction_id=transaction_id,
                    reference=reference,
                )
                if transaction is None:
                    raise ValidationException("Withdrawal transaction not found.")
                if transaction.category != "withdrawal":
                    raise ValidationException("Transaction is not a withdrawal.")
                if authenticated_user_id is not None and transaction.user_id != authenticated_user_id:
                    raise WalletException("Withdrawal ownership mismatch.")
                if transaction.status == "completed":
                    return self._withdrawal_result(transaction)
                if transaction.status == "failed":
                    return self._withdrawal_result(transaction)
                if transaction.status not in {"funds_reserved", "pending", "processing"}:
                    raise WalletException("Withdrawal is not eligible for execution.")

                if account_details is not None:
                    raise ValidationException("Beneficiary details must come from the trusted bank account.")
                beneficiary = await self._resolve_transaction_account(transaction)
                transaction.status = "processing"
                await self.transaction_repository.update_transaction(transaction, status="processing")

                try:
                    provider_result = await self.provider_service.execute_transfer(
                        operation=lambda provider: self._resolve_provider_adapter(provider).transfer(
                            account_details=beneficiary,
                            amount=transaction.amount,
                            reference=transaction.reference,
                        ),
                        retryable_errors=(),
                        payload={"reference": transaction.reference},
                    )
                except Exception as exc:
                    if self._is_ambiguous_provider_error(exc):
                        transaction.status = "pending"
                        transaction.metadata_payload = self._merge_metadata(
                            transaction.metadata_payload,
                            {"provider_outcome": "unknown", "provider_error_type": type(exc).__name__},
                        )
                        await self.transaction_repository.update_transaction(
                            transaction,
                            status="pending",
                            metadata_payload=transaction.metadata_payload,
                        )
                        return self._withdrawal_result(transaction)

                    return await self._release_reserved_locked(
                        transaction,
                        reason="provider_rejected",
                        provider_error_type=type(exc).__name__,
                    )

                normalized = self._unwrap_provider_result(provider_result)
                provider_status = self._normalize_status(
                    normalized.get("status"),
                    default="pending",
                )
                self._persist_provider_result(transaction, normalized)
                if provider_status == "completed":
                    wallet = await self._load_wallet_for_update(transaction.wallet_id)
                    if wallet is None:
                        raise ValidationException("Wallet not found.")
                    if wallet.locked_balance < transaction.amount:
                        raise WalletException("Withdrawal reservation is insufficient for settlement.")
                    wallet.locked_balance -= transaction.amount
                    wallet.ledger_balance -= transaction.amount
                    if wallet.available_balance < 0 or wallet.locked_balance < 0:
                        raise WalletException("Wallet balance cannot be negative.")
                    await self.wallet_repository.update_balance_fields(
                        wallet,
                        ledger_balance=wallet.ledger_balance,
                        locked_balance=wallet.locked_balance,
                    )
                    transaction.status = "completed"
                    await self.transaction_repository.update_transaction(
                        transaction,
                        status="completed",
                        provider_name=transaction.provider_name,
                        provider_reference=transaction.provider_reference,
                        provider_transaction_id=transaction.provider_transaction_id,
                        payout_amount=transaction.payout_amount,
                        payout_currency=transaction.payout_currency,
                        metadata_payload=transaction.metadata_payload,
                    )
                    return self._withdrawal_result(transaction)
                if provider_status == "failed":
                    return await self._release_reserved_locked(
                        transaction,
                        reason="provider_rejected",
                    )

                transaction.status = "pending"
                await self.transaction_repository.update_transaction(
                    transaction,
                    status="pending",
                    provider_name=transaction.provider_name,
                    provider_reference=transaction.provider_reference,
                    provider_transaction_id=transaction.provider_transaction_id,
                    metadata_payload=transaction.metadata_payload,
                )
                return self._withdrawal_result(transaction)
        except ValidationException:
            raise
        except WalletException:
            raise
        except ProviderException:
            raise
        except Exception as exc:
            raise DatabaseException("Withdrawal execution failed.") from exc

    def _validate_amount(self, amount_value: Decimal) -> None:
        if amount_value <= 0:
            raise ValidationException("Withdrawal amount must be greater than zero.")
        if amount_value < self.MIN_AMOUNT:
            raise ValidationException("Withdrawal amount is too small.")
        if amount_value > self.MAX_AMOUNT:
            raise ValidationException("Withdrawal amount exceeds the maximum allowed value.")

    def _resolve_provider_adapter(self, provider: Provider) -> Any:
        if self.provider_adapter_factory is not None:
            adapter = self.provider_adapter_factory(provider)
            if adapter is None:
                raise ProviderException("No withdrawal integration is available for the selected provider.")
            return adapter
        if self.provider_adapter is not None:
            return self.provider_adapter
        raise ProviderException("Withdrawal provider integration is not configured.")

    def _resolve_account_details(
        self,
        transaction: Transaction,
        account_details: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if account_details is not None:
            resolved = dict(account_details)
        else:
            resolved = {}
            if transaction.metadata_payload:
                try:
                    metadata = json.loads(transaction.metadata_payload)
                    if isinstance(metadata, dict):
                        resolved = dict(metadata.get("beneficiary") or {})
                except (TypeError, json.JSONDecodeError):
                    resolved = {}
        account_number = str(resolved.get("account_number") or "")
        bank_code = str(resolved.get("account_bank") or resolved.get("bank_code") or "")
        if not account_number or not account_number.isdigit():
            raise ValidationException("Trusted withdrawal account details are unavailable.")
        if not bank_code.strip():
            raise ValidationException("Trusted withdrawal bank details are unavailable.")
        if not 6 <= len(account_number) <= 20:
            raise ValidationException("Trusted withdrawal account details are invalid.")
        resolved["account_number"] = account_number
        resolved["bank_code"] = bank_code.strip()
        resolved.setdefault("currency", transaction.currency)
        return resolved

    async def _resolve_transaction_account(self, transaction: Transaction) -> dict[str, Any]:
        if self.bank_account_service is None or transaction.bank_account_id is None:
            raise ValidationException("Trusted withdrawal bank account is unavailable.")
        account = await self.bank_account_service.resolve_withdrawal_account(
            user_id=transaction.user_id,
            bank_account_id=transaction.bank_account_id,
        )
        return {
            "bank_code": account["bank_code"],
            "account_number": account["account_number"],
            "account_name": account.get("account_name"),
            "currency": transaction.currency,
        }

    def _unwrap_provider_result(self, provider_result: Any) -> dict[str, Any]:
        current: Any = provider_result
        for key in ("result", "data"):
            if isinstance(current, Mapping) and isinstance(current.get(key), Mapping):
                current = current[key]
        return dict(current) if isinstance(current, Mapping) else {}

    def _persist_provider_result(self, transaction: Transaction, result: Mapping[str, Any]) -> None:
        provider_name = result.get("provider")
        if isinstance(provider_name, str) and provider_name.strip():
            transaction.provider_name = provider_name.strip()
        provider_reference = result.get("provider_reference")
        if provider_reference is not None:
            transaction.provider_reference = str(provider_reference)
        provider_transaction_id = result.get("provider_transaction_id")
        if provider_transaction_id is not None:
            transaction.provider_transaction_id = str(provider_transaction_id)
        settled_amount = result.get("amount")
        try:
            transaction.payout_amount = Decimal(str(settled_amount if settled_amount is not None else transaction.amount))
        except (ArithmeticError, ValueError):
            transaction.payout_amount = transaction.amount
        settled_currency = result.get("currency")
        transaction.payout_currency = str(settled_currency or transaction.currency).upper()
        status = str(result.get("status") or "pending")[:50]
        transaction.metadata_payload = self._merge_metadata(
            transaction.metadata_payload,
            {"provider_status": status},
        )

    def _is_ambiguous_provider_error(self, exc: BaseException) -> bool:
        details = " ".join(
            [str(exc), type(exc).__name__, str(getattr(exc, "detail", ""))]
        ).lower()
        return any(
            marker in details
            for marker in ("timeout", "timed out", "connection", "network", "temporarily unavailable")
        )

    async def _release_reserved_locked(
        self,
        transaction: Transaction,
        *,
        reason: str,
        provider_error_type: str | None = None,
    ) -> dict[str, Any]:
        if transaction.status == "failed":
            return self._withdrawal_result(transaction)
        if transaction.wallet_id is None:
            raise ValidationException("Withdrawal transaction is missing a wallet identifier.")
        wallet = await self._load_wallet_for_update(transaction.wallet_id)
        if wallet is None:
            raise ValidationException("Wallet not found.")
        if wallet.locked_balance < transaction.amount:
            raise WalletException("Withdrawal reservation is insufficient for release.")
        wallet.available_balance += transaction.amount
        wallet.locked_balance -= transaction.amount
        if wallet.available_balance < 0 or wallet.locked_balance < 0:
            raise WalletException("Wallet balance cannot be negative.")
        await self.wallet_repository.update_balance_fields(
            wallet,
            available_balance=wallet.available_balance,
            locked_balance=wallet.locked_balance,
        )
        transaction.status = "failed"
        metadata: dict[str, Any] = {"provider_outcome": reason}
        if provider_error_type:
            metadata["provider_error_type"] = provider_error_type
        transaction.metadata_payload = self._merge_metadata(transaction.metadata_payload, metadata)
        await self.transaction_repository.update_transaction(
            transaction,
            status="failed",
            metadata_payload=transaction.metadata_payload,
        )
        return self._withdrawal_result(transaction)

    def _withdrawal_result(self, transaction: Transaction) -> dict[str, Any]:
        return {
            "transaction_id": str(transaction.id),
            "reference": transaction.reference,
            "status": transaction.status,
            "wallet_id": str(transaction.wallet_id) if transaction.wallet_id else None,
            "amount": str(transaction.amount),
            "currency": transaction.currency,
            "provider_reference": transaction.provider_reference,
            "provider_transaction_id": transaction.provider_transaction_id,
        }

    async def _load_user_wallet_for_update(self, user_id: UUID) -> Wallet | None:
        session = self._resolve_session()
        if session is None:
            return await self.wallet_repository.get_user_wallet_for_update(user_id=user_id)
        result = await session.execute(
            select(Wallet).where(Wallet.user_id == user_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def validate_withdrawal(
        self,
        *,
        wallet_id: UUID,
        amount: Decimal | float | int,
        transaction_pin: str | None = None,
    ) -> dict[str, Any]:
        """Validate wallet state, ownership, amount, and transaction PIN for withdrawal."""
        self._require_repository(self.wallet_repository)
        amount_value = self._normalize_amount(amount)
        if amount_value <= 0:
            raise ValidationException("Withdrawal amount must be greater than zero.")
        if amount_value < self.MIN_AMOUNT:
            raise ValidationException("Withdrawal amount is too small.")
        if amount_value > self.MAX_AMOUNT:
            raise ValidationException("Withdrawal amount exceeds the maximum allowed value.")

        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if wallet is None:
            raise ValidationException("Wallet not found.")
        if wallet.is_frozen or wallet.is_suspended or not wallet.is_active:
            raise WalletException("Wallet is not eligible for withdrawal.")

        await self.validate_transaction_pin(transaction_pin=transaction_pin)
        return {"wallet_id": str(wallet.id), "currency": wallet.currency, "valid": True, "amount": str(amount_value)}

    async def calculate_withdrawal_fee(self, *, amount: Decimal | float | int) -> Decimal:
        """Calculate a simple withdrawal fee model that can be adapted for providers later."""
        amount_value = self._normalize_amount(amount)
        fee = amount_value * Decimal("0.015")
        return fee.quantize(Decimal("0.01"))

    async def validate_daily_limit(self, *, user_id: UUID, amount: Decimal | float | int) -> dict[str, Any]:
        """Validate that the withdrawal amount does not exceed a daily limit."""
        amount_value = self._normalize_amount(amount)
        if amount_value > self.DAILY_LIMIT:
            raise ValidationException("Withdrawal exceeds the daily limit.")
        return {"daily_limit": str(self.DAILY_LIMIT), "remaining": str(self.DAILY_LIMIT - amount_value)}

    async def validate_transaction_pin(self, *, transaction_pin: str | None) -> bool:
        """Validate a transaction PIN for withdrawal authorization."""
        if transaction_pin is None:
            raise ValidationException("Transaction PIN is required.")
        if not re.fullmatch(r"\d{4,6}", transaction_pin):
            raise ValidationException("Transaction PIN must be numeric and between 4 and 6 digits.")
        return True

    async def debit_wallet(
        self,
        *,
        wallet_id: UUID,
        amount: Decimal | float | int,
        transaction: Transaction | None = None,
        reference: str | None = None,
    ) -> dict[str, Any]:
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
            raise WalletException("Wallet is not active for withdrawal.")

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

    async def reverse_withdrawal(self, *, transaction_id: UUID | None = None, reference: str | None = None, reason: str | None = None) -> dict[str, Any]:
        """Reverse a completed withdrawal and restore the debited wallet balance."""
        self._require_repository(self.wallet_repository)
        self._require_repository(self.transaction_repository)

        transaction = await self._get_transaction(transaction_id=transaction_id, reference=reference)
        if transaction is None:
            raise ValidationException("Withdrawal transaction not found.")
        if transaction.status.lower() != "completed":
            raise WalletException("Only completed withdrawals can be reversed.")

        wallet_id = transaction.wallet_id
        if wallet_id is None:
            raise ValidationException("Withdrawal transaction is missing a wallet identifier.")

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
                await self._log_event("withdrawal_reversed", user_id=transaction.user_id, metadata={"reference": transaction.reference})
                return {"transaction_id": str(transaction.id), "status": transaction.status, "wallet_id": str(wallet.id)}
        except Exception as exc:
            raise DatabaseException("Withdrawal reversal failed.") from exc

    async def release_withdrawal(
        self,
        *,
        transaction_id: UUID | None = None,
        reference: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Release a pending withdrawal reservation after a failed execution."""
        self._require_repository(self.wallet_repository)
        self._require_repository(self.transaction_repository)

        try:
            async with self._session_scope():
                transaction = await self._get_transaction_for_update(
                    transaction_id=transaction_id,
                    reference=reference,
                )
                if transaction is None:
                    raise ValidationException("Withdrawal transaction not found.")
                if transaction.category != "withdrawal":
                    raise ValidationException("Transaction is not a withdrawal.")
                if transaction.status in {"failed", "reversed"}:
                    return {"transaction_id": str(transaction.id), "status": transaction.status}
                if transaction.status not in {"funds_reserved", "processing", "pending"}:
                    raise WalletException("Only pending withdrawals can release funds.")
                if transaction.wallet_id is None:
                    raise ValidationException("Withdrawal transaction is missing a wallet identifier.")

                wallet = await self._load_wallet_for_update(transaction.wallet_id)
                if wallet is None:
                    raise ValidationException("Wallet not found.")
                wallet.available_balance += transaction.amount
                wallet.locked_balance = max(Decimal("0.00"), wallet.locked_balance - transaction.amount)
                await self.wallet_repository.update_balance_fields(
                    wallet,
                    available_balance=wallet.available_balance,
                    locked_balance=wallet.locked_balance,
                )
                transaction.status = "failed"
                transaction.metadata_payload = self._merge_metadata(
                    transaction.metadata_payload,
                    {"release_reason": reason} if reason else None,
                )
                await self.transaction_repository.update_transaction(
                    transaction,
                    status=transaction.status,
                    metadata_payload=transaction.metadata_payload,
                )
                return {
                    "transaction_id": str(transaction.id),
                    "status": transaction.status,
                    "wallet_id": str(wallet.id),
                }
        except ValidationException:
            raise
        except WalletException:
            raise
        except Exception as exc:
            raise DatabaseException("Withdrawal reservation release failed.") from exc

    async def _get_transaction(self, *, transaction_id: UUID | None = None, reference: str | None = None) -> Transaction | None:
        if transaction_id is not None:
            return await self.transaction_repository.get_by_id(transaction_id)
        if reference is not None:
            return await self.transaction_repository.get_by_reference(reference)
        return None

    async def _get_transaction_for_update(
        self,
        *,
        transaction_id: UUID | None = None,
        reference: str | None = None,
    ) -> Transaction | None:
        if transaction_id is not None:
            return await self.transaction_repository.get_by_id_for_update(transaction_id)
        if reference is not None:
            return await self.transaction_repository.get_by_reference_for_update(reference)
        return None

    async def _load_wallet_for_update(self, wallet_id: UUID) -> Wallet | None:
        session = self._resolve_session()
        if session is None:
            return await self.wallet_repository.get_by_id_for_update(wallet_id)
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
        raise ProviderException("No provider handler is configured for this withdrawal flow.")

    async def _resolve_provider_response(self, response: object) -> dict[str, Any]:
        resolved = response
        if inspect.isawaitable(response):
            resolved = await response
        if isinstance(resolved, Mapping):
            return dict(resolved)
        return {}

    async def _log_event(self, event_name: str, *, user_id: UUID | None = None, metadata: dict[str, Any] | None = None) -> None:
        self.logger.info(
            "wallet_withdrawal_event",
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
            raise RuntimeError("Required repository is not configured for WalletWithdrawalService.")

    def _require_dependency(self, dependency: Any | None, name: str) -> None:
        if dependency is None:
            raise RuntimeError(f"Required dependency '{name}' is not configured for WalletWithdrawalService.")

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
