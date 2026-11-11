from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4

from sqlalchemy import select

from app.models.provider import Provider
from app.models.transaction import Transaction
from app.models.wallet import Wallet
from app.integrations.airtime.manager import ProviderManager
from app.repositories.transaction_repository import TransactionRepository
from app.services.provider_acquisition_cost_service import ProviderCostSnapshotService
from app.repositories.user_repository import UserRepository
from app.services.ledger_service import LedgerService
from app.services.provider_service import ProviderService
from app.services.vtu.status import normalize_vtu_status
from app.services.wallet_service import WalletService
from app.utils.exceptions import PaymentException, ValidationException, WalletException


class DataPurchaseService:
    """Manage mobile-data purchase workflows with wallet, provider, and repository coordination."""

    def __init__(
        self,
        *,
        wallet_service: WalletService,
        provider_service: ProviderService,
        provider_manager: ProviderManager | None = None,
        transaction_repository: TransactionRepository,
        user_repository: UserRepository,
        ledger_service: LedgerService | None = None,
        cost_snapshot_service: ProviderCostSnapshotService | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.wallet_service = wallet_service
        self.provider_service = provider_service
        self.provider_manager = provider_manager or ProviderManager()
        self.transaction_repository = transaction_repository
        self.user_repository = user_repository
        self.ledger_service = ledger_service
        self.cost_snapshot_service = cost_snapshot_service
        self.logger = logger or logging.getLogger(__name__)

    async def purchase_data(
        self,
        *,
        user_id: UUID,
        phone_number: str,
        amount: Decimal | float | int,
        transaction_pin: str,
        network: str | None = None,
        plan_id: str | None = None,
        bundle_code: str | None = None,
        wallet_id: UUID | None = None,
        currency: str = "NGN",
        description: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Create a pending data purchase, debit the wallet, and dispatch it to the provider."""
        amount_value = self._normalize_amount(amount)
        self._validate_purchase_request(
            user_id=user_id,
            phone_number=phone_number,
            amount=amount_value,
            transaction_pin=transaction_pin,
            network=network,
            bundle_code=bundle_code,
        )
        request_metadata = self._parse_metadata(metadata_payload)
        service_variant = str(request_metadata.get("service_variant") or "").strip().lower()
        if service_variant in {"awoof", "awoof_data"}:
            request_metadata["service_variant"] = "awoof"

        reference = self.generate_reference()
        self.logger.info(
            "data_purchase_started",
            extra={
                "reference": reference,
                "user_id": str(user_id),
                "phone_number": phone_number,
                "amount": str(amount_value),
                "network": network,
                "bundle_code": bundle_code,
            },
        )

        async with self._transaction_scope():
            await self._ensure_user_exists(user_id)
            wallet = await self._resolve_wallet(user_id=user_id, wallet_id=wallet_id)
            wallet = await self._wallet_repository().get_by_id_for_update(wallet.id)
            if wallet is None or wallet.user_id != user_id:
                raise ValidationException("Wallet was not found.")
            await self._ensure_no_duplicate_purchase(
                user_id=user_id,
                amount=amount_value,
                phone_number=phone_number,
                network=network,
                bundle_code=bundle_code,
            )
            await self.wallet_service.verify_transaction_pin(user_id=user_id, pin=transaction_pin)
            if wallet.is_frozen or wallet.is_suspended or not wallet.is_active:
                raise WalletException("Wallet is not active for data purchase.")
            if wallet.available_balance < amount_value:
                raise WalletException("Insufficient wallet balance for data purchase.")

            transaction = Transaction(
                reference=reference,
                user_id=user_id,
                wallet_id=wallet.id,
                transaction_type="data_purchase",
                category="data",
                amount=amount_value,
                currency=currency,
                charges=Decimal("0"),
                total_amount=amount_value,
                status="pending",
                provider_name=provider_name,
                description=description or f"Data purchase for {phone_number}",
                metadata_payload=self._serialize_metadata(
                    {
                        "phone_number": phone_number,
                        "network": network,
                        "bundle_code": bundle_code,
                        "wallet_id": str(wallet.id),
                        "requested_amount": str(amount_value),
                        **(
                            {"service_variant": "awoof"}
                            if request_metadata.get("service_variant") == "awoof"
                            else {}
                        ),
                    }
                ),
            )
            transaction = await self.transaction_repository.create_transaction(transaction)
            await self._debit_wallet(transaction=transaction, wallet=wallet, amount=amount_value)

        return await self.process_data_purchase(
            transaction=transaction,
            network=network,
            bundle_code=bundle_code,
            provider_name=provider_name,
            metadata_payload=metadata_payload,
        )

    async def process_data_purchase(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        network: str | None = None,
        bundle_code: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Dispatch a pending data purchase through the provider and update the transaction outcome."""
        transaction_reference = transaction.reference if transaction is not None else reference
        if not transaction_reference:
            raise ValidationException("Data purchase reference is required.")
        async with self._transaction_scope():
            resolved = await self.transaction_repository.get_by_reference_for_update(transaction_reference)
            if resolved is None:
                raise ValidationException("Data purchase reference was not found.")
            if resolved.status in {"succeeded", "completed", "settled"}:
                return await self._build_response(resolved)
            metadata = self._parse_metadata(resolved.metadata_payload)
            if metadata.get("provider_attempted") is True:
                raise PaymentException(
                    detail="Data provider outcome is pending reconciliation. Do not retry this purchase."
                )
            metadata["provider_attempted"] = True
            metadata["provider_outcome_uncertain"] = True
            resolved.metadata_payload = self._serialize_metadata(metadata)
            await self.transaction_repository.update_transaction(
                resolved,
                metadata_payload=resolved.metadata_payload,
            )

        self.logger.info("data_purchase_provider_execution", extra={"reference": resolved.reference})
        try:
            provider_response = self._normalize_provider_manager_response(
                await self.provider_manager.execute(
                    operation="buy_data",
                    phone_number=self._extract_phone_number(resolved),
                    network=network or self._extract_network(resolved),
                    bundle_code=bundle_code or self._extract_bundle_code(resolved),
                    amount=str(resolved.amount),
                    reference=resolved.reference,
                )
            )
        except Exception as exc:
            await self._mark_provider_outcome_pending(transaction=resolved)
            self.logger.error(
                "data_purchase_provider_outcome_uncertain",
                extra={"reference": resolved.reference, "error_type": type(exc).__name__},
            )
            raise PaymentException(
                detail="Data provider outcome could not be confirmed. Check transaction status before retrying."
            ) from exc

        status = self._normalize_status(provider_response.get("status"))
        async with self._transaction_scope():
            transaction_record = await self.transaction_repository.get_by_reference_for_update(resolved.reference)
            if transaction_record is None:
                raise ValidationException("Purchase transaction was not found.")
            if transaction_record.status in {"succeeded", "completed", "settled"}:
                return await self._build_response(transaction_record)

            transaction_record.status = status
            transaction_record.provider_name = provider_response.get("provider") or provider_name or transaction_record.provider_name
            transaction_record.provider_reference = provider_response.get("provider_reference") or transaction_record.provider_reference
            transaction_record.provider_transaction_id = provider_response.get("provider_transaction_id") or transaction_record.provider_transaction_id
            transaction_record.external_reference = transaction_record.provider_reference
            transaction_record.metadata_payload = self._serialize_metadata(
                {
                    **self._parse_metadata(transaction_record.metadata_payload),
                    "provider_response": {
                        "status": status,
                        "provider": provider_response.get("provider") or provider_name,
                        "provider_reference": provider_response.get("provider_reference"),
                        "provider_transaction_id": provider_response.get("provider_transaction_id"),
                    },
                    "phone_number": self._extract_phone_number(transaction_record),
                    "network": self._extract_network(transaction_record),
                    "bundle_code": self._extract_bundle_code(transaction_record),
                    "attempted": True,
                    "provider_attempted": status not in {"failed", "cancelled", "reversed"},
                    "provider_outcome_uncertain": status not in {"succeeded", "completed", "settled", "failed", "cancelled", "reversed"},
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

            if status in {"succeeded", "completed", "settled"} and self.cost_snapshot_service is not None:
                await self.cost_snapshot_service.capture_successful_transaction(transaction_record)

            if status in {"failed", "cancelled", "reversed"}:
                reason = "Provider reported a failed data purchase."
                await self._reverse_wallet_debit(transaction=transaction_record, reason=reason)
                transaction_record.status = "reversed" if status == "reversed" else "failed"
                metadata = self._parse_metadata(transaction_record.metadata_payload)
                metadata.update(
                    {
                        "failure_reason": reason,
                        "provider_attempted": False,
                        "provider_outcome_uncertain": False,
                        "reversed": metadata.get("reversal_applied") is True,
                    }
                )
                transaction_record.metadata_payload = self._serialize_metadata(metadata)
                await self.transaction_repository.update_transaction(
                    transaction_record,
                    status=transaction_record.status,
                    metadata_payload=transaction_record.metadata_payload,
                )
            elif status in {"succeeded", "completed", "settled"}:
                self.logger.info("data_purchase_succeeded", extra={"reference": transaction_record.reference})
            else:
                self.logger.info("data_purchase_pending", extra={"reference": transaction_record.reference, "status": status})

            return await self._build_response(transaction_record)

    async def reverse_data_purchase(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Reverse a data purchase and restore the debited wallet balance when applicable."""
        transaction_reference = transaction.reference if transaction is not None else reference
        if not transaction_reference:
            raise ValidationException("Data purchase reference is required.")
        async with self._transaction_scope():
            resolved = await self.transaction_repository.get_by_reference_for_update(transaction_reference)
            if resolved is None:
                raise ValidationException("Data purchase reference was not found.")
            if resolved.status in {"reversed", "cancelled"}:
                return await self._build_response(resolved)
            await self._reverse_wallet_debit(transaction=resolved, reason=reason or "data_purchase_reversed")
            resolved.status = "reversed"
            resolved.metadata_payload = self._serialize_metadata(
                {
                    **self._parse_metadata(resolved.metadata_payload),
                    "reversal_reason": reason or "data_purchase_reversed",
                }
            )
            resolved = await self.transaction_repository.update_transaction(resolved, status=resolved.status, metadata_payload=resolved.metadata_payload)
            self.logger.info("data_purchase_reversed", extra={"reference": resolved.reference})
            return await self._build_response(resolved)

    async def retry_data_purchase(
        self,
        *,
        transaction: Transaction | None = None,
        reference: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        provider_name: str | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Retry a failed or pending data purchase after restoring the wallet debit if needed."""
        if provider_operation is None:
            raise ValidationException("A provider operation callback is required for data retries.")
        transaction_reference = transaction.reference if transaction is not None else reference
        if not transaction_reference:
            raise ValidationException("Data purchase reference is required.")
        async with self._transaction_scope():
            resolved = await self.transaction_repository.get_by_reference_for_update(transaction_reference)
            if resolved is None:
                raise ValidationException("Data purchase reference was not found.")
            if resolved.status in {"succeeded", "completed", "settled"}:
                return await self._build_response(resolved)
            metadata = self._parse_metadata(resolved.metadata_payload)
            if resolved.status == "pending" and metadata.get("provider_attempted") is True:
                raise PaymentException(
                    detail="Data provider outcome is pending reconciliation. Do not retry this purchase."
                )
            if resolved.status in {"failed", "cancelled", "reversed"}:
                wallet = await self._get_wallet_for_transaction(resolved)
                await self._debit_wallet(transaction=resolved, wallet=wallet, amount=resolved.amount)
            resolved.status = "pending"
            metadata["provider_attempted"] = False
            metadata["provider_outcome_uncertain"] = False
            resolved.metadata_payload = self._serialize_metadata(metadata)
            await self.transaction_repository.update_transaction(
                resolved,
                status=resolved.status,
                metadata_payload=resolved.metadata_payload,
            )

        self.logger.info("data_purchase_retry", extra={"reference": resolved.reference, "status": resolved.status})
        return await self.process_data_purchase(
            transaction=resolved,
            network=self._extract_network(resolved),
            bundle_code=self._extract_bundle_code(resolved),
            provider_name=provider_name,
            metadata_payload=metadata_payload,
        )

    async def get_purchase_status(self, *, reference: str) -> dict[str, Any]:
        """Return the current lifecycle state of a data purchase."""
        if not reference or not isinstance(reference, str) or not reference.strip():
            raise ValidationException("Data purchase reference is required.")
        transaction = await self.transaction_repository.get_by_reference(reference)
        if transaction is None:
            raise ValidationException("Data purchase reference was not found.")
        return await self._build_response(transaction)

    async def get_purchase_details(self, *, reference: str) -> dict[str, Any]:
        """Fetch the full data purchase record including metadata."""
        transaction = await self.transaction_repository.get_by_reference(reference)
        if transaction is None:
            raise ValidationException("Data purchase reference was not found.")
        return await self._build_response(transaction, include_metadata=True)

    def generate_reference(self) -> str:
        """Create a unique reference for a new data purchase."""
        return f"data-{uuid4().hex[:12]}"

    async def _ensure_user_exists(self, user_id: UUID) -> None:
        user = await self.user_repository.get_by_id(user_id)
        if user is None:
            raise ValidationException("User was not found.")

    async def _resolve_wallet(self, *, user_id: UUID, wallet_id: UUID | None) -> Wallet:
        if wallet_id is not None:
            wallet = await self._wallet_repository().get_by_id(wallet_id)
            if wallet is None:
                raise ValidationException("Wallet was not found.")
            if wallet.user_id != user_id:
                raise ValidationException("Wallet ownership does not match the provided user.")
            return wallet

        wallet = await self._wallet_repository().get_user_wallet(user_id=user_id)
        if wallet is None:
            raise ValidationException("Wallet was not found.")
        return wallet

    async def _get_wallet_for_transaction(self, transaction: Transaction) -> Wallet:
        if transaction.wallet_id is None:
            raise ValidationException("Transaction wallet was not found.")
        wallet = await self._wallet_repository().get_by_id(transaction.wallet_id)
        if wallet is None:
            raise ValidationException("Wallet was not found.")
        return wallet

    async def _debit_wallet(self, *, transaction: Transaction, wallet: Wallet, amount: Decimal) -> None:
        metadata = self._parse_metadata(transaction.metadata_payload)
        if metadata.get("debit_applied") is True:
            return
        if transaction.wallet_id is None:
            raise ValidationException("Wallet reference is missing for debit.")
        wallet = await self._wallet_repository().get_by_id_for_update(transaction.wallet_id)
        if wallet is None or wallet.user_id != transaction.user_id:
            raise ValidationException("Wallet was not found for debit.")
        if not wallet.is_active or wallet.is_frozen or wallet.is_suspended:
            raise WalletException("Wallet is not active for data purchase.")
        if wallet.available_balance < amount:
            raise WalletException("Insufficient wallet balance for data purchase.")
        if self.ledger_service is None:
            raise ValidationException("Data purchase ledger service is unavailable.")

        opening_balance = wallet.available_balance
        wallet.available_balance = wallet.available_balance - amount
        wallet.ledger_balance = wallet.ledger_balance - amount
        await self._wallet_repository().update_balance_fields(
            wallet,
            available_balance=wallet.available_balance,
            ledger_balance=wallet.ledger_balance,
        )
        await self.ledger_service.create_debit_entry(
            wallet_id=wallet.id,
            user_id=wallet.user_id,
            transaction_reference=transaction.reference,
            transaction_type="DATA_PURCHASE",
            opening_balance=opening_balance,
            amount=amount,
            currency=wallet.currency,
            description=transaction.description or "Data purchase",
            related_transaction_id=transaction.id,
            status="POSTED",
            metadata={"source": "data_purchase"},
        )
        metadata["debit_applied"] = True
        transaction.metadata_payload = self._serialize_metadata(metadata)
        await self.transaction_repository.update_transaction(transaction, metadata_payload=transaction.metadata_payload)
        self.logger.info("data_purchase_wallet_debited", extra={"reference": transaction.reference, "amount": str(amount)})

    async def _reverse_wallet_debit(self, *, transaction: Transaction, reason: str | None = None) -> None:
        metadata = self._parse_metadata(transaction.metadata_payload)
        if metadata.get("debit_applied") is not True:
            metadata["reversal_reason"] = reason or "wallet_debit_not_required"
            transaction.metadata_payload = self._serialize_metadata(metadata)
            await self.transaction_repository.update_transaction(transaction, metadata_payload=transaction.metadata_payload)
            return
        if transaction.wallet_id is None:
            raise ValidationException("Wallet reference is missing for reversal.")
        wallet = await self._wallet_repository().get_by_id_for_update(transaction.wallet_id)
        if wallet is None or wallet.user_id != transaction.user_id:
            raise ValidationException("Wallet was not found for reversal.")
        if self.ledger_service is None:
            raise ValidationException("Data purchase ledger service is unavailable.")

        opening_balance = wallet.available_balance
        wallet.available_balance = wallet.available_balance + transaction.amount
        wallet.ledger_balance = wallet.ledger_balance + transaction.amount
        await self._wallet_repository().update_balance_fields(
            wallet,
            available_balance=wallet.available_balance,
            ledger_balance=wallet.ledger_balance,
        )
        await self.ledger_service.create_credit_entry(
            wallet_id=wallet.id,
            user_id=wallet.user_id,
            transaction_reference=transaction.reference,
            transaction_type="DATA_PURCHASE_REVERSAL",
            opening_balance=opening_balance,
            amount=transaction.amount,
            currency=wallet.currency,
            description=reason or "Data purchase failure reversal",
            related_transaction_id=transaction.id,
            status="POSTED",
            metadata={"source": "data_purchase_reversal"},
        )
        metadata["debit_applied"] = False
        metadata["reversal_applied"] = True
        metadata["reversal_reason"] = reason or "provider_failure"
        transaction.metadata_payload = self._serialize_metadata(metadata)
        await self.transaction_repository.update_transaction(transaction, metadata_payload=transaction.metadata_payload)
        self.logger.info("data_purchase_reversal", extra={"reference": transaction.reference, "reason": reason})

    async def _handle_provider_failure(self, *, transaction: Transaction, reason: str) -> None:
        async with self._transaction_scope():
            current = await self.transaction_repository.get_by_reference_for_update(transaction.reference)
            if current is None:
                raise ValidationException("Purchase transaction was not found.")
            if current.status in {"succeeded", "completed", "settled"}:
                return
            await self._reverse_wallet_debit(transaction=current, reason=reason)
            current.status = "failed"
            current.metadata_payload = self._serialize_metadata(
                {
                    **self._parse_metadata(current.metadata_payload),
                    "failure_reason": reason,
                    "reversed": True,
                }
            )
            await self.transaction_repository.update_transaction(current, status=current.status, metadata_payload=current.metadata_payload)
            self.logger.warning("data_purchase_failed", extra={"reference": current.reference, "reason": reason})

    async def _mark_provider_outcome_pending(self, *, transaction: Transaction) -> None:
        async with self._transaction_scope():
            current = await self.transaction_repository.get_by_reference_for_update(transaction.reference)
            if current is None:
                raise ValidationException("Purchase transaction was not found.")
            if current.status in {"succeeded", "completed", "settled"}:
                return
            metadata = self._parse_metadata(current.metadata_payload)
            metadata["provider_attempted"] = True
            metadata["provider_outcome_uncertain"] = True
            metadata["provider_error"] = "provider outcome could not be confirmed"
            current.status = "pending"
            current.metadata_payload = self._serialize_metadata(metadata)
            await self.transaction_repository.update_transaction(
                current,
                status=current.status,
                metadata_payload=current.metadata_payload,
            )

    async def _ensure_no_duplicate_purchase(
        self,
        *,
        user_id: UUID,
        amount: Decimal,
        phone_number: str,
        network: str | None,
        bundle_code: str | None,
    ) -> None:
        query = (
            select(Transaction)
            .where(Transaction.user_id == user_id)
            .where(Transaction.transaction_type == "data_purchase")
            .where(Transaction.category == "data")
            .where(Transaction.status.in_({"pending", "processing"}))
            .order_by(Transaction.created_at.desc())
        )
        result = await self.transaction_repository.session.execute(query)
        for existing in result.scalars().all():
            if getattr(existing, "status", None) not in {"pending", "processing"}:
                continue
            metadata = self._parse_metadata(existing.metadata_payload)
            if (
                metadata.get("phone_number") == phone_number
                and metadata.get("network") == network
                and metadata.get("bundle_code") == bundle_code
                and metadata.get("requested_amount") == str(amount)
            ):
                raise PaymentException("Duplicate data purchase detected. Use the existing transaction reference.")

    async def _resolve_transaction(self, *, transaction: Transaction | None, reference: str | None) -> Transaction:
        if transaction is not None:
            return transaction
        if reference is None:
            raise ValidationException("Data purchase reference is required.")
        resolved = await self.transaction_repository.get_by_reference(reference)
        if resolved is None:
            raise ValidationException("Data purchase reference was not found.")
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
            "plan": {
                "network": self._extract_network(transaction),
                "bundle_code": self._extract_bundle_code(transaction),
                "phone_number": self._extract_phone_number(transaction),
            },
        }
        if include_metadata:
            response["metadata"] = self._parse_metadata(transaction.metadata_payload)
        return response

    def _validate_purchase_request(
        self,
        *,
        user_id: UUID,
        phone_number: str,
        amount: Decimal,
        transaction_pin: str,
        network: str | None,
        bundle_code: str | None,
    ) -> None:
        if user_id is None:
            raise ValidationException("User ID is required.")
        if not phone_number or not isinstance(phone_number, str) or not phone_number.strip():
            raise ValidationException("Phone number is required.")
        if amount <= 0:
            raise ValidationException("Data amount must be greater than zero.")
        if not transaction_pin or not isinstance(transaction_pin, str) or not transaction_pin.strip():
            raise ValidationException("Transaction PIN is required.")
        if not network or not isinstance(network, str) or not network.strip():
            raise ValidationException("Network is required.")
        if not bundle_code or not isinstance(bundle_code, str) or not bundle_code.strip():
            raise ValidationException("Bundle code is required.")

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
        return normalize_vtu_status(status)

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
        return self._parse_metadata(transaction.metadata_payload).get("phone_number")

    def _extract_network(self, transaction: Transaction) -> str | None:
        return self._parse_metadata(transaction.metadata_payload).get("network")

    def _extract_bundle_code(self, transaction: Transaction) -> str | None:
        return self._parse_metadata(transaction.metadata_payload).get("bundle_code")

    def _normalize_amount(self, amount: Decimal | float | int) -> Decimal:
        if isinstance(amount, Decimal):
            return amount
        return Decimal(str(amount))

    def _wallet_repository(self) -> Any:
        return self.wallet_service.wallet_manager.wallet_repository

    def _transaction_scope(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def scope():
            async with self.transaction_repository.session.begin():
                yield

        return scope()


__all__ = ["DataPurchaseService"]
