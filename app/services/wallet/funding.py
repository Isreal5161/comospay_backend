from __future__ import annotations

import inspect
import logging
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction
from app.models.wallet import Wallet
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.wallet_repository import WalletRepository
from app.utils.exceptions import DatabaseException, ProviderException, ValidationException, WalletException


class WalletFundingService:
    """Coordinate wallet funding workflows through provider and payment services."""

    MIN_AMOUNT = Decimal("0.01")
    MAX_AMOUNT = Decimal("999999999.99")

    def __init__(
        self,
        *,
        wallet_repository: WalletRepository,
        transaction_repository: TransactionRepository,
        provider_service: Any | None = None,
        payment_service: Any | None = None,
        user_repository: Any | None = None,
        session: AsyncSession | None = None,
        logger: logging.Logger | None = None,
        audit_service: Any | None = None,
    ) -> None:
        self.wallet_repository = wallet_repository
        self.transaction_repository = transaction_repository
        self.provider_service = provider_service
        self.payment_service = payment_service
        self.user_repository = user_repository
        self.session = session
        self.logger = logger or logging.getLogger(__name__)
        self.audit_service = audit_service

    async def initialize_wallet_funding(
        self,
        *,
        user_id: UUID,
        wallet_id: UUID,
        amount: Decimal | float | int,
        provider_name: str = "flutterwave",
        provider_reference: str | None = None,
        currency: str = "NGN",
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Initialize a wallet funding request with a provider and create a pending transaction."""
        self._require_repository(self.wallet_repository)
        self._require_repository(self.transaction_repository)
        self._require_dependency(self.provider_service, "provider_service")

        wallet = await self._get_wallet_and_validate_ownership(wallet_id=wallet_id, user_id=user_id)
        amount_value = self._normalize_amount(amount)
        self._validate_funding_amount(amount_value)
        self._validate_wallet_state(wallet)

        await self._ensure_provider_reference_is_unique(provider_name=provider_name, provider_reference=provider_reference)

        reference = self._make_reference("fund")
        transaction = Transaction(
            reference=reference,
            user_id=user_id,
            wallet_id=wallet.id,
            transaction_type="wallet_funding",
            category="funding",
            amount=amount_value,
            currency=self._normalize_currency(currency),
            charges=Decimal("0.00"),
            total_amount=amount_value,
            status="pending",
            provider_name=provider_name,
            provider_reference=provider_reference,
            description="Wallet funding initialization",
            metadata_payload=self._sanitize_metadata(metadata_payload),
        )

        try:
            async with self._session_scope():
                await self.transaction_repository.create_transaction(transaction)
                payload = await self._dispatch_provider_call(
                    "initialize_funding",
                    provider_name=provider_name,
                    user_id=user_id,
                    wallet_id=wallet.id,
                    amount=amount_value,
                    currency=currency.upper(),
                    provider_reference=provider_reference,
                    transaction_reference=reference,
                    metadata_payload=metadata_payload,
                )
                transaction.provider_transaction_id = payload.get("provider_transaction_id") or transaction.provider_transaction_id
                transaction.external_reference = payload.get("external_reference") or transaction.external_reference
                transaction.status = self._normalize_status(payload.get("status"), default="pending")
                transaction.metadata_payload = self._merge_metadata(transaction.metadata_payload, payload)
                await self.transaction_repository.update_transaction(
                    transaction,
                    provider_transaction_id=transaction.provider_transaction_id,
                    external_reference=transaction.external_reference,
                    status=transaction.status,
                    metadata_payload=transaction.metadata_payload,
                )
                await self._log_event("wallet_funding_initialized", user_id=user_id, metadata={"reference": reference, "provider_name": provider_name})
                return self._serialize_transaction(transaction, wallet=wallet)
        except ProviderException:
            raise
        except Exception as exc:
            raise DatabaseException("Wallet funding initialization failed.") from exc

    async def verify_wallet_funding(
        self,
        *,
        transaction_id: UUID | None = None,
        reference: str | None = None,
        provider_reference: str | None = None,
        provider_name: str = "flutterwave",
    ) -> dict[str, Any]:
        """Verify a pending funding transaction and credit the wallet when the provider confirms success."""
        self._require_repository(self.transaction_repository)
        self._require_dependency(self.provider_service, "provider_service")

        transaction = await self._get_transaction(transaction_id=transaction_id, reference=reference, provider_reference=provider_reference)
        if not transaction:
            raise ValidationException("Funding transaction not found.")

        if transaction.status.lower() in {"completed", "succeeded", "credited"}:
            return self._serialize_transaction(transaction)

        try:
            async with self._session_scope():
                payload = await self._dispatch_provider_call(
                    "verify_funding",
                    provider_name=provider_name,
                    transaction=transaction,
                    provider_reference=provider_reference or transaction.provider_reference,
                    transaction_reference=transaction.reference,
                )
                normalized_status = self._normalize_status(payload.get("status"), default=transaction.status)
                if normalized_status in {"succeeded", "completed", "successful", "success"}:
                    result = await self.credit_wallet(transaction_id=transaction.id)
                    return result

                transaction.status = normalized_status
                transaction.metadata_payload = self._merge_metadata(transaction.metadata_payload, payload)
                await self.transaction_repository.update_transaction(transaction, status=transaction.status, metadata_payload=transaction.metadata_payload)
                await self._log_event("wallet_funding_verified", user_id=transaction.user_id, metadata={"reference": transaction.reference})
                return self._serialize_transaction(transaction)
        except WalletException:
            raise
        except ProviderException:
            raise
        except Exception as exc:
            raise DatabaseException("Wallet funding verification failed.") from exc

    async def credit_wallet(self, *, transaction_id: UUID | None = None, reference: str | None = None) -> dict[str, Any]:
        """Credit a wallet for a successful funding transaction and persist the audit trail atomically."""
        self._require_repository(self.wallet_repository)
        self._require_repository(self.transaction_repository)

        transaction = await self._get_transaction(transaction_id=transaction_id, reference=reference)
        if not transaction:
            raise ValidationException("Funding transaction not found.")

        if transaction.status.lower() in {"completed", "succeeded", "credited"}:
            return self._serialize_transaction(transaction)

        if transaction.status.lower() in {"reversed", "failed", "cancelled", "voided"}:
            raise WalletException("Funding transaction is not eligible for credit.")

        wallet_id = transaction.wallet_id
        user_id = transaction.user_id
        if wallet_id is None:
            raise ValidationException("Funding transaction is missing a wallet identifier.")
        if user_id is None:
            raise ValidationException("Funding transaction is missing a user identifier.")

        wallet = await self._get_wallet_and_validate_ownership(wallet_id=wallet_id, user_id=user_id)
        self._validate_wallet_state(wallet)

        try:
            async with self._session_scope():
                updated_available = wallet.available_balance + transaction.amount
                updated_ledger = wallet.ledger_balance + transaction.amount
                wallet.available_balance = updated_available
                wallet.ledger_balance = updated_ledger
                await self.wallet_repository.update_balance_fields(
                    wallet,
                    available_balance=updated_available,
                    ledger_balance=updated_ledger,
                )
                transaction.status = "completed"
                transaction.metadata_payload = self._merge_metadata(transaction.metadata_payload, {"credited": True})
                await self.transaction_repository.update_transaction(transaction, status=transaction.status, metadata_payload=transaction.metadata_payload)
                await self._log_event("wallet_funding_credited", user_id=transaction.user_id, metadata={"reference": transaction.reference})
                return self._serialize_transaction(transaction, wallet=wallet)
        except Exception as exc:
            raise DatabaseException("Wallet credit failed.") from exc

    async def reverse_wallet_credit(self, *, transaction_id: UUID | None = None, reference: str | None = None, reason: str | None = None) -> dict[str, Any]:
        """Reverse a completed wallet funding credit and update the transaction status."""
        self._require_repository(self.wallet_repository)
        self._require_repository(self.transaction_repository)

        transaction = await self._get_transaction(transaction_id=transaction_id, reference=reference)
        if not transaction:
            raise ValidationException("Funding transaction not found.")

        if transaction.status.lower() not in {"completed", "succeeded", "credited"}:
            raise WalletException("Only completed funding transactions can be reversed.")

        wallet_id = transaction.wallet_id
        user_id = transaction.user_id
        if wallet_id is None:
            raise ValidationException("Funding transaction is missing a wallet identifier.")
        if user_id is None:
            raise ValidationException("Funding transaction is missing a user identifier.")

        wallet = await self._get_wallet_and_validate_ownership(wallet_id=wallet_id, user_id=user_id)
        self._validate_wallet_state(wallet)

        if wallet.available_balance < transaction.amount:
            raise WalletException("Wallet balance is insufficient to reverse the funding credit.")

        try:
            async with self._session_scope():
                updated_available = wallet.available_balance - transaction.amount
                updated_ledger = wallet.ledger_balance - transaction.amount
                wallet.available_balance = updated_available
                wallet.ledger_balance = updated_ledger
                await self.wallet_repository.update_balance_fields(
                    wallet,
                    available_balance=updated_available,
                    ledger_balance=updated_ledger,
                )
                transaction.status = "reversed"
                transaction.metadata_payload = self._merge_metadata(transaction.metadata_payload, {"reversed": True, "reason": reason})
                await self.transaction_repository.update_transaction(transaction, status=transaction.status, metadata_payload=transaction.metadata_payload)
                await self._log_event("wallet_funding_reversed", user_id=transaction.user_id, metadata={"reference": transaction.reference, "reason": reason})
                return self._serialize_transaction(transaction, wallet=wallet)
        except Exception as exc:
            raise DatabaseException("Wallet credit reversal failed.") from exc

    async def reconcile_wallet_funding(
        self,
        *,
        provider_name: str = "flutterwave",
        provider_reference: str | None = None,
        transaction_id: UUID | None = None,
        reference: str | None = None,
    ) -> dict[str, Any]:
        """Reconcile a funding transaction by asking the provider for the current state."""
        self._require_repository(self.transaction_repository)
        self._require_dependency(self.provider_service, "provider_service")

        transaction = await self._get_transaction(transaction_id=transaction_id, reference=reference, provider_reference=provider_reference)
        if not transaction:
            raise ValidationException("Funding transaction not found.")

        if transaction.status.lower() in {"completed", "succeeded", "credited"}:
            return self._serialize_transaction(transaction)

        try:
            payload = await self._dispatch_provider_call(
                "reconcile_funding",
                provider_name=provider_name,
                transaction=transaction,
                provider_reference=provider_reference or transaction.provider_reference,
                transaction_reference=transaction.reference,
            )
            normalized_status = self._normalize_status(payload.get("status"), default=transaction.status)
            if normalized_status in {"succeeded", "completed", "successful", "success"}:
                return await self.credit_wallet(transaction_id=transaction.id)
            transaction.status = normalized_status
            transaction.metadata_payload = self._merge_metadata(transaction.metadata_payload, payload)
            await self.transaction_repository.update_transaction(transaction, status=transaction.status, metadata_payload=transaction.metadata_payload)
            return self._serialize_transaction(transaction)
        except ProviderException:
            raise
        except Exception as exc:
            raise DatabaseException("Wallet funding reconciliation failed.") from exc

    async def process_virtual_account_funding(
        self,
        *,
        user_id: UUID,
        wallet_id: UUID,
        amount: Decimal | float | int,
        provider_name: str = "flutterwave",
        currency: str = "NGN",
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Create a virtual account funding request through the provider service."""
        self._require_repository(self.wallet_repository)
        self._require_repository(self.transaction_repository)
        self._require_dependency(self.provider_service, "provider_service")

        wallet = await self._get_wallet_and_validate_ownership(wallet_id=wallet_id, user_id=user_id)
        amount_value = self._normalize_amount(amount)
        self._validate_funding_amount(amount_value)
        self._validate_wallet_state(wallet)

        try:
            async with self._session_scope():
                payload = await self._dispatch_provider_call(
                    "create_virtual_account",
                    provider_name=provider_name,
                    user_id=user_id,
                    wallet_id=wallet.id,
                    amount=amount_value,
                    currency=currency.upper(),
                    metadata_payload=metadata_payload,
                )
                reference = self._make_reference("va")
                transaction = Transaction(
                    reference=reference,
                    user_id=user_id,
                    wallet_id=wallet.id,
                    transaction_type="wallet_funding",
                    category="funding",
                    amount=amount_value,
                    currency=currency.upper(),
                    charges=Decimal("0.00"),
                    total_amount=amount_value,
                    status="pending",
                    provider_name=provider_name,
                    provider_reference=payload.get("provider_reference"),
                    provider_transaction_id=payload.get("provider_transaction_id"),
                    external_reference=payload.get("external_reference"),
                    description="Virtual account funding",
                    metadata_payload=self._merge_metadata(self._sanitize_metadata(metadata_payload), payload),
                )
                await self.transaction_repository.create_transaction(transaction)
                await self._log_event("virtual_account_funding_requested", user_id=user_id, metadata={"reference": reference, "provider_name": provider_name})
                return {
                    "transaction_id": str(transaction.id),
                    "reference": transaction.reference,
                    "wallet_id": str(wallet.id),
                    "provider_name": provider_name,
                    "status": transaction.status,
                    "amount": str(transaction.amount),
                    "currency": transaction.currency,
                    "virtual_account": payload.get("virtual_account") or payload.get("account_details") or {},
                }
        except ProviderException:
            raise
        except Exception as exc:
            raise DatabaseException("Virtual account funding processing failed.") from exc

    async def _get_wallet_and_validate_ownership(self, *, wallet_id: UUID, user_id: UUID) -> Wallet:
        repository_getter = getattr(self.wallet_repository, "get_by_id_for_update", None)
        if callable(repository_getter):
            wallet_result = repository_getter(wallet_id)
            wallet = await self._await_if_needed(wallet_result)
        else:
            wallet = await self.wallet_repository.get_by_id(wallet_id)
        if not wallet:
            raise ValidationException("Wallet not found.")
        if wallet.user_id != user_id:
            raise WalletException("Wallet ownership mismatch.")
        return wallet

    async def _ensure_provider_reference_is_unique(self, *, provider_name: str, provider_reference: str | None) -> None:
        if not provider_reference:
            return
        session = getattr(self.transaction_repository, "session", None)
        if session is None:
            return
        result = await session.execute(
            session.query(Transaction).filter(Transaction.provider_reference == provider_reference)  # type: ignore[attr-defined]
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            raise WalletException("Duplicate provider reference detected.")

    async def _get_transaction(
        self,
        *,
        transaction_id: UUID | None = None,
        reference: str | None = None,
        provider_reference: str | None = None,
    ) -> Transaction | None:
        if transaction_id is not None:
            return await self.transaction_repository.get_by_id(transaction_id)
        if reference is not None:
            return await self.transaction_repository.get_by_reference(reference)
        if provider_reference is not None:
            session = getattr(self.transaction_repository, "session", None)
            if session is None:
                return None
            result = await session.execute(
                session.query(Transaction).filter(Transaction.provider_reference == provider_reference)  # type: ignore[attr-defined]
            )
            return result.scalar_one_or_none()
        return None

    async def _dispatch_provider_call(self, method_name: str, **kwargs: Any) -> dict[str, Any]:
        for candidate in (method_name, f"{method_name}_provider", f"{method_name}_payment", f"process_{method_name}"):
            handler = getattr(self.provider_service, candidate, None) if self.provider_service is not None else None
            if callable(handler):
                return await self._resolve_provider_response(handler(**kwargs))

        if self.payment_service is not None:
            for candidate in (method_name, f"{method_name}_payment", f"process_{method_name}"):
                handler = getattr(self.payment_service, candidate, None)
                if callable(handler):
                    return await self._resolve_provider_response(handler(**kwargs))

        raise ProviderException("No provider or payment handler is configured for this funding flow.")

    async def _await_if_needed(self, value: object) -> Any:
        if inspect.isawaitable(value):
            return await value
        return value

    async def _resolve_provider_response(self, response: object) -> dict[str, Any]:
        resolved = await self._await_if_needed(response)
        if isinstance(resolved, dict):
            return resolved
        if isinstance(resolved, Mapping):
            return dict(resolved)
        raise ProviderException("Provider returned an unexpected response type.")

    async def _log_event(self, event_name: str, *, user_id: UUID | None = None, metadata: dict[str, Any] | None = None) -> None:
        self.logger.info(
            "wallet_funding_event",
            extra={"event": event_name, "user_id": str(user_id) if user_id else None, "metadata": metadata or {}},
        )
        if self.audit_service is not None:
            try:
                await self.audit_service(event_name, user_id=user_id, metadata=metadata)
            except TypeError:
                self.audit_service(event_name, user_id=user_id, metadata=metadata)

    def _validate_funding_amount(self, amount: Decimal) -> None:
        if amount <= self.MIN_AMOUNT - Decimal("0.0000001"):
            raise ValidationException("Funding amount must be greater than zero.", error_code="INVALID_FUNDING_AMOUNT")
        if amount > self.MAX_AMOUNT:
            raise ValidationException("Funding amount exceeds the maximum allowed amount.", error_code="FUNDING_AMOUNT_EXCEEDS_LIMIT")

    def _validate_wallet_state(self, wallet: Wallet) -> None:
        if wallet is None:
            raise ValidationException("Wallet not found.")
        if getattr(wallet, "is_frozen", False) or getattr(wallet, "is_suspended", False) or not getattr(wallet, "is_active", True):
            raise WalletException("Wallet is not active for funding.")
        if str(getattr(wallet, "status", "")).lower() not in {"active", "pending"}:
            raise WalletException("Wallet is not active for funding.")

    def _normalize_amount(self, amount: Decimal | float | int) -> Decimal:
        if isinstance(amount, Decimal):
            return amount
        return Decimal(str(amount))

    def _normalize_currency(self, currency: str | None) -> str:
        normalized = (currency or "NGN").strip().upper()
        if len(normalized) != 3:
            raise ValidationException("Currency code must be a 3-letter ISO code.", error_code="INVALID_CURRENCY")
        return normalized

    def _normalize_status(self, status: str | None, *, default: str) -> str:
        if not status:
            return default
        lowered = status.strip().lower()
        if lowered in {"success", "successful", "succeeded", "completed", "complete", "approved"}:
            return "completed"
        if lowered in {"pending", "processing", "initialized", "in_progress", "in-progress"}:
            return "pending"
        if lowered in {"failed", "failure", "declined", "rejected", "error"}:
            return "failed"
        if lowered in {"reversed", "refund", "refunded"}:
            return "reversed"
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
        safe_data = {key: value for key, value in payload.items() if key not in {"amount", "currency", "wallet_id", "user_id"}}
        if not safe_data:
            return existing
        import json

        existing_data: dict[str, Any] = {}
        if existing:
            try:
                existing_data = json.loads(existing)
            except json.JSONDecodeError:
                existing_data = {"value": existing}
        existing_data.update(safe_data)
        return json.dumps(existing_data, default=str)

    def _serialize_transaction(self, transaction: Transaction, *, wallet: Wallet | None = None) -> dict[str, Any]:
        return {
            "id": str(transaction.id),
            "reference": transaction.reference,
            "wallet_id": str(transaction.wallet_id),
            "wallet_status": wallet.status if wallet is not None else None,
            "transaction_type": transaction.transaction_type,
            "category": transaction.category,
            "amount": str(transaction.amount),
            "currency": transaction.currency,
            "status": transaction.status,
            "provider_name": transaction.provider_name,
            "provider_reference": transaction.provider_reference,
            "provider_transaction_id": transaction.provider_transaction_id,
            "external_reference": transaction.external_reference,
        }

    def _require_dependency(self, dependency: Any | None, name: str) -> None:
        if dependency is None:
            raise RuntimeError(f"Required dependency '{name}' is not configured for WalletFundingService.")

    def _require_repository(self, repository: Any | None) -> None:
        if repository is None:
            raise RuntimeError("Required repository is not configured for WalletFundingService.")

    def _session_scope(self) -> Any:
        if self.session is None:
            return _NullSessionContext()
        return self.session.begin()

    def _make_reference(self, prefix: str) -> str:
        return f"{prefix}-{uuid4().hex[:12]}"


class _NullSessionContext:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False
