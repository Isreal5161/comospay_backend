from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.ledger import Ledger
from app.models.wallet import Wallet
from app.repositories.admin_repository import AdminRepository
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.ledger_repository import LedgerRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.admin.authorization import require_active_admin
from app.services.notification_service import NotificationService
from app.services.wallet.statement import WalletStatementService
from app.services.wallet.wallet_balance import WalletBalanceService
from app.services.wallet.wallet_reconciliation import WalletReconciliationService
from app.services.wallet_service import WalletService
from app.utils.exceptions import AuthorizationException, ValidationException


class WalletAdministrationService:
    """Admin wallet orchestrator that delegates to existing wallet-domain services."""

    def __init__(
        self,
        logger: logging.Logger | None = None,
        wallet_repository: WalletRepository | None = None,
        ledger_repository: LedgerRepository | None = None,
        admin_repository: AdminRepository | None = None,
        audit_repository: AuditLogRepository | None = None,
        notification_service: NotificationService | None = None,
        wallet_service: WalletService | None = None,
        wallet_balance_service: WalletBalanceService | None = None,
        wallet_statement_service: WalletStatementService | None = None,
        wallet_reconciliation_service: WalletReconciliationService | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.wallet_repository = wallet_repository
        self.ledger_repository = ledger_repository
        self.admin_repository = admin_repository
        self.audit_repository = audit_repository
        self.notification_service = notification_service
        self.wallet_service = wallet_service
        self.wallet_balance_service = wallet_balance_service
        self.wallet_statement_service = wallet_statement_service
        self.wallet_reconciliation_service = wallet_reconciliation_service
        self.session = session

    async def adjust_wallet(
        self,
        *,
        user_id: UUID,
        wallet_id: UUID,
        amount: Decimal,
        currency: str,
        reason: str,
        **payload: Any,
    ) -> dict[str, Any]:
        self._require_wallet_repository()
        admin_id = await self._require_authorized_admin(payload)

        if self.ledger_repository is None or self.audit_repository is None or self.session is None:
            raise ValidationException(
                detail="Audited wallet adjustments are unavailable.",
                error_code="ADMIN_WALLET_ADJUSTMENT_UNAVAILABLE",
            )
        if self.wallet_balance_service is None:
            self._missing_operation_exception(
                operation="adjust_wallet",
                missing_service="WalletBalanceService",
                missing_method="credit_wallet/debit_wallet",
                reason="Wallet balance service dependency is unavailable for admin orchestration.",
            )

        amount_value = Decimal(str(amount))
        if not amount_value.is_finite():
            raise ValidationException(detail="Adjustment amount must be finite.", error_code="INVALID_ADJUSTMENT_AMOUNT")
        if amount_value == Decimal("0"):
            raise ValidationException(detail="Adjustment amount must be non-zero.", error_code="INVALID_ADJUSTMENT_AMOUNT")

        currency_code = currency.strip().upper()
        if not currency_code or len(currency_code) < 3 or len(currency_code) > 10:
            raise ValidationException(detail="Currency code is invalid.", error_code="INVALID_CURRENCY")
        normalized_reason = self._optional_text(reason)
        if normalized_reason is None or len(normalized_reason) > 500:
            raise ValidationException(detail="A valid adjustment reason is required.", error_code="INVALID_ADJUSTMENT_REASON")
        reference = self._optional_text(payload.get("reference"))
        if reference is None or len(reference) > 100:
            raise ValidationException(
                detail="A valid idempotency reference is required.",
                error_code="INVALID_IDEMPOTENCY_REFERENCE",
            )

        wallet = await self.wallet_repository.get_by_id_for_update(wallet_id)
        if wallet is None or wallet.user_id != user_id:
            raise ValidationException(detail="Wallet not found for user.", error_code="WALLET_NOT_FOUND")
        wallet_currency = str(wallet.currency or "").strip().upper()
        if wallet_currency != currency_code:
            raise ValidationException(
                detail="Requested currency does not match the wallet currency.",
                error_code="WALLET_CURRENCY_MISMATCH",
            )

        await self.wallet_balance_service.validate_balance_operation(
            wallet_id=wallet_id,
            amount=abs(amount_value),
        )

        operation = "manual_credit" if amount_value > 0 else "manual_debit"
        transaction_type = "ADMIN_CREDIT" if amount_value > 0 else "ADMIN_DEBIT"
        entry_type = "CREDIT" if amount_value > 0 else "DEBIT"
        metadata = {
            "source": "admin.wallet",
            "admin_id": str(admin_id),
            "reason": normalized_reason,
            "idempotency_reference": reference,
            "opening_ledger_balance": str(wallet.ledger_balance),
            "closing_ledger_balance": str(wallet.ledger_balance + amount_value),
        }
        existing_entries = await self.ledger_repository.get_by_wallet_reference(
            wallet_id=wallet_id,
            transaction_reference=reference,
        )
        if existing_entries:
            existing = existing_entries[0]
            existing_metadata = existing.metadata_payload or {}
            existing_amount = existing.credit_amount if entry_type == "CREDIT" else existing.debit_amount
            matches_request = (
                len(existing_entries) == 1
                and existing.user_id == user_id
                and existing.entry_type.upper() == entry_type
                and existing.transaction_type.upper() == transaction_type
                and Decimal(str(existing_amount)) == abs(amount_value)
                and str(existing.currency).strip().upper() == currency_code
                and existing.created_by == f"admin:{admin_id}"
                and existing_metadata.get("reason") == normalized_reason
                and existing_metadata.get("admin_id") == str(admin_id)
                and existing_metadata.get("idempotency_reference") == reference
            )
            if not matches_request:
                await self.session.rollback()
                raise ValidationException(
                    detail="The idempotency reference was already used for a different wallet adjustment.",
                    error_code="IDEMPOTENCY_REFERENCE_CONFLICT",
                )

            await self.session.commit()
            return {
                "success": True,
                "data": {
                    "wallet_id": str(wallet.id),
                    "user_id": str(wallet.user_id),
                    "operation": operation,
                    "amount": str(amount_value),
                    "currency": currency_code,
                    "reference": reference,
                    "idempotent": True,
                    "result": {
                        "wallet_id": str(wallet.id),
                        "transaction_reference": reference,
                        "previous_balance": float(existing.opening_balance),
                        "amount": float(abs(amount_value)),
                        "new_available_balance": float(existing.closing_balance),
                        "new_ledger_balance": float(
                            Decimal(str(existing_metadata.get("closing_ledger_balance", existing.closing_balance)))
                        ),
                        "ledger_entry_id": str(existing.ledger_id),
                        "status": "success",
                        "timestamp": existing.created_at.isoformat(),
                    },
                },
                "meta": {"source": "admin.wallet"},
            }

        requested_at = datetime.now(timezone.utc)
        audit_payload = {
            "user_id": str(user_id),
            "wallet_id": str(wallet.id),
            "amount": str(amount_value),
            "currency": currency_code,
            "reason": normalized_reason,
            "reference": reference,
            "requested_at": requested_at.isoformat(),
        }
        try:
            await self.audit_repository.create_audit_log(
                AuditLog(
                    actor_type="admin",
                    actor_id=str(admin_id),
                    action=f"admin.wallet.{operation}",
                    category="admin_wallet",
                    description=f"Admin performed '{operation}' on wallet.",
                    resource_type="wallet",
                    resource_id=str(wallet.id),
                    metadata_payload=json.dumps(audit_payload),
                    new_value=json.dumps(audit_payload),
                    created_at=requested_at,
                )
            )
            if self.notification_service is not None:
                await self.notification_service.create_notification(
                    user_id=wallet.user_id,
                    title="Wallet Update",
                    message=self._build_notification_message(action=operation, reason=normalized_reason),
                    notification_type="admin_wallet",
                    category="wallet",
                    reference=wallet.wallet_reference,
                    metadata={"action": operation, "wallet_id": str(wallet.id), "reason": normalized_reason},
                    channel="in_app",
                )

            if amount_value > 0:
                result = await self.wallet_balance_service.credit_wallet(
                    wallet_id=wallet.id,
                    amount=amount_value,
                    transaction_reference=reference,
                    transaction_type=transaction_type,
                    description=normalized_reason,
                    metadata=metadata,
                    created_by=f"admin:{admin_id}",
                )
            else:
                result = await self.wallet_balance_service.debit_wallet(
                    wallet_id=wallet.id,
                    amount=abs(amount_value),
                    transaction_reference=reference,
                    transaction_type=transaction_type,
                    description=normalized_reason,
                    metadata=metadata,
                    created_by=f"admin:{admin_id}",
                )
        except Exception:
            if self.session.in_transaction():
                await self.session.rollback()
            raise

        return {
            "success": True,
            "data": {
                "wallet_id": str(wallet.id),
                "user_id": str(wallet.user_id),
                "operation": operation,
                "amount": str(amount_value),
                "currency": currency_code,
                "reference": reference,
                "idempotent": False,
                "result": result,
            },
            "meta": {"source": "admin.wallet"},
        }

    async def list_wallets(self, **payload: Any) -> dict[str, Any]:
        self._require_wallet_repository()
        await self._require_authorized_admin(payload)

        page = payload.get("page", 1)
        page_size = payload.get("page_size", 20)
        status = payload.get("status")
        wallet_type = payload.get("wallet_type")
        currency = payload.get("currency")
        query_text = payload.get("query")
        order_by = payload.get("order_by", "created_at")
        descending = payload.get("descending", True)

        wallets, total = await self.wallet_repository.list_wallets(
            page=page,
            page_size=page_size,
            status=status,
            wallet_type=wallet_type,
            currency=currency,
            query_text=query_text,
            order_by=order_by,
            descending=descending,
        )
        total_wallets, currencies = await self.wallet_repository.get_wallet_directory_metadata()
        items = [self._serialize_wallet(item) for item in wallets]

        return {
            "success": True,
            "data": {
                "items": items,
                "total": total,
                "total_wallets": total_wallets,
                "currencies": currencies,
            },
            "meta": {"source": "admin.wallet", "page": page, "page_size": page_size},
        }

    async def get_wallet_details(self, *, wallet_id: UUID, **payload: Any) -> dict[str, Any]:
        self._require_wallet_repository()
        await self._require_authorized_admin(payload)

        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if wallet is None:
            raise ValidationException(detail="Wallet not found.", error_code="WALLET_NOT_FOUND")
        return {
            "success": True,
            "data": self._serialize_wallet(wallet),
            "meta": {"source": "admin.wallet"},
        }

    async def get_user_wallet(self, *, user_id: UUID, **payload: Any) -> dict[str, Any]:
        self._require_wallet_repository()
        await self._require_authorized_admin(payload)

        wallet = await self.wallet_repository.get_user_wallet(user_id=user_id)
        if wallet is None:
            raise ValidationException(detail="Wallet not found for user.", error_code="WALLET_NOT_FOUND")
        return {
            "success": True,
            "data": self._serialize_wallet(wallet),
            "meta": {"source": "admin.wallet"},
        }

    async def get_user_wallet_profile(self, *, user_id: UUID, **payload: Any) -> dict[str, Any]:
        self._require_wallet_repository()
        await self._require_authorized_admin(payload)
        wallet = await self.wallet_repository.get_user_wallet(user_id=user_id)
        return {"wallet": self._serialize_wallet(wallet) if wallet is not None else None}

    async def search_wallets(self, *, query: str, **payload: Any) -> dict[str, Any]:
        merged_payload = {**payload, "query": query}
        return await self.list_wallets(**merged_payload)

    async def wallet_history(self, *, wallet_id: UUID, **payload: Any) -> dict[str, Any]:
        self._require_wallet_repository()
        await self._require_authorized_admin(payload)

        if self.wallet_statement_service is None:
            self._missing_operation_exception(
                operation="wallet_history",
                missing_service="WalletStatementService",
                missing_method="get_transaction_history",
                reason="Wallet statement service dependency is unavailable for admin orchestration.",
            )

        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if wallet is None:
            raise ValidationException(detail="Wallet not found.", error_code="WALLET_NOT_FOUND")

        history = await self.wallet_statement_service.get_transaction_history(
            user_id=wallet.user_id,
            wallet_id=wallet.id,
            page=payload.get("page", 1),
            page_size=payload.get("page_size", 20),
            sort_by=payload.get("sort_by", "created_at"),
            sort_desc=payload.get("sort_desc", True),
        )
        return {
            "success": True,
            "data": history,
            "meta": {"source": "admin.wallet"},
        }

    async def get_user_ledger(
        self,
        *,
        user_id: UUID,
        wallet_id: UUID,
        page: int = 1,
        page_size: int = 20,
        currency: str | None = None,
        status: str | None = None,
        entry_type: str | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        self._require_wallet_repository()
        await self._require_authorized_admin(payload)
        if self.ledger_repository is None:
            raise ValidationException(
                detail="Wallet ledger retrieval is unavailable.",
                error_code="ADMIN_WALLET_LEDGER_UNAVAILABLE",
            )
        if page < 1 or page_size < 1 or page_size > 100:
            raise ValidationException(detail="Invalid ledger pagination.", error_code="INVALID_PAGINATION")
        if entry_type and entry_type.upper() not in {"CREDIT", "DEBIT", "REFUND", "FEE", "COMMISSION", "ADJUSTMENT", "REVERSAL"}:
            raise ValidationException(detail="Unsupported ledger entry type.", error_code="INVALID_ENTRY_TYPE")

        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if wallet is None or wallet.user_id != user_id:
            raise ValidationException(detail="Wallet not found for user.", error_code="WALLET_NOT_FOUND")
        entries, total = await self.ledger_repository.list_wallet_entries(
            wallet_id=wallet_id,
            user_id=user_id,
            page=page,
            page_size=page_size,
            currency=currency,
            status=status,
            entry_type=entry_type,
        )
        return {
            "success": True,
            "data": {
                "items": [self._serialize_ledger_entry(entry) for entry in entries],
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": (total + page_size - 1) // page_size,
            },
            "meta": {"source": "admin.wallet.ledger"},
        }

    async def list_admin_adjustments(self, **payload: Any) -> dict[str, Any]:
        self._require_wallet_repository()
        await self._require_authorized_admin(payload)
        if self.ledger_repository is None:
            raise ValidationException(
                detail="Wallet ledger retrieval is unavailable.",
                error_code="ADMIN_WALLET_LEDGER_UNAVAILABLE",
            )

        page = int(payload.get("page") or 1)
        page_size = int(payload.get("page_size") or 20)
        start_date = payload.get("start_date")
        end_date = payload.get("end_date")
        if page < 1 or page_size < 1 or page_size > 100:
            raise ValidationException(detail="Invalid ledger pagination.", error_code="INVALID_PAGINATION")
        if start_date is not None and not isinstance(start_date, datetime):
            raise ValidationException(detail="Start date is invalid.", error_code="INVALID_DATE_RANGE")
        if end_date is not None and not isinstance(end_date, datetime):
            raise ValidationException(detail="End date is invalid.", error_code="INVALID_DATE_RANGE")
        if start_date is not None and end_date is not None and start_date > end_date:
            raise ValidationException(detail="Start date must not be after end date.", error_code="INVALID_DATE_RANGE")

        entries, total = await self.ledger_repository.list_admin_adjustments(
            page=page,
            page_size=page_size,
            search=self._optional_text(payload.get("search")),
            start_date=start_date,
            end_date=end_date,
        )
        items = []
        for entry, user in entries:
            serialized = self._serialize_ledger_entry(entry)
            serialized["source"] = "ledger_adjustment"
            serialized["user"] = {
                "id": str(user.id),
                "name": " ".join(
                    part.strip()
                    for part in (user.first_name, user.last_name)
                    if isinstance(part, str) and part.strip()
                ) or None,
                "email": user.email,
            }
            items.append(serialized)
        return {
            "success": True,
            "data": {
                "items": items,
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": (total + page_size - 1) // page_size,
            },
            "meta": {"source": "admin.wallet.adjustments"},
        }

    async def get_user_ledger_by_reference(
        self,
        *,
        user_id: UUID,
        wallet_id: UUID,
        transaction_reference: str,
        **payload: Any,
    ) -> dict[str, Any]:
        self._require_wallet_repository()
        await self._require_authorized_admin(payload)
        if self.ledger_repository is None:
            raise ValidationException(
                detail="Wallet ledger retrieval is unavailable.",
                error_code="ADMIN_WALLET_LEDGER_UNAVAILABLE",
            )

        reference = transaction_reference.strip()
        if not reference or len(reference) > 100:
            raise ValidationException(
                detail="A valid transaction reference is required.",
                error_code="INVALID_TRANSACTION_REFERENCE",
            )

        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if wallet is None or wallet.user_id != user_id:
            raise ValidationException(detail="Wallet not found for user.", error_code="WALLET_NOT_FOUND")

        total, entries = await self.ledger_repository.find_by_wallet_user_reference(
            wallet_id=wallet_id,
            user_id=user_id,
            transaction_reference=reference,
        )
        return {
            "success": True,
            "data": {
                "total": total,
                "items": [self._serialize_ledger_entry(entry) for entry in entries],
            },
            "meta": {"source": "admin.wallet.ledger.reference"},
        }

    async def wallet_analytics(self, *, wallet_id: UUID, **payload: Any) -> dict[str, Any]:
        self._require_wallet_repository()
        await self._require_authorized_admin(payload)

        if self.wallet_statement_service is None:
            self._missing_operation_exception(
                operation="wallet_analytics",
                missing_service="WalletStatementService",
                missing_method="calculate_statement_summary",
                reason="Wallet statement service dependency is unavailable for admin orchestration.",
            )

        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if wallet is None:
            raise ValidationException(detail="Wallet not found.", error_code="WALLET_NOT_FOUND")

        summary = await self.wallet_statement_service.calculate_statement_summary(
            user_id=wallet.user_id,
            wallet_id=wallet.id,
            start_date=self._optional_text(payload.get("start_date")),
            end_date=self._optional_text(payload.get("end_date")),
            status=self._optional_text(payload.get("status")),
            transaction_type=self._optional_text(payload.get("transaction_type")),
        )
        return {
            "success": True,
            "data": summary,
            "meta": {"source": "admin.wallet"},
        }

    async def freeze_wallet(self, *, wallet_id: UUID, reason: str | None = None, **payload: Any) -> dict[str, Any]:
        await self._require_authorized_admin(payload)
        if self.wallet_service is None:
            self._missing_operation_exception(
                operation="freeze_wallet",
                missing_service="WalletService",
                missing_method="freeze_wallet",
                reason="Wallet service dependency is unavailable for admin orchestration.",
            )
        result = await self.wallet_service.freeze_wallet(wallet_id=wallet_id, reason=reason)
        return {"success": True, "data": result, "meta": {"source": "admin.wallet"}}

    async def unfreeze_wallet(self, *, wallet_id: UUID, **payload: Any) -> dict[str, Any]:
        await self._require_authorized_admin(payload)
        if self.wallet_service is None:
            self._missing_operation_exception(
                operation="unfreeze_wallet",
                missing_service="WalletService",
                missing_method="unfreeze_wallet",
                reason="Wallet service dependency is unavailable for admin orchestration.",
            )
        result = await self.wallet_service.unfreeze_wallet(wallet_id=wallet_id)
        return {"success": True, "data": result, "meta": {"source": "admin.wallet"}}

    async def lock_wallet(self, *, wallet_id: UUID, amount: float, reason: str, **payload: Any) -> dict[str, Any]:
        await self._require_authorized_admin(payload)
        if self.wallet_balance_service is None:
            self._missing_operation_exception(
                operation="lock_wallet",
                missing_service="WalletBalanceService",
                missing_method="lock_funds",
                reason="Wallet balance service dependency is unavailable for admin orchestration.",
            )
        reference = self._optional_text(payload.get("reference")) or self._make_reference("admin-lock")
        result = await self.wallet_balance_service.lock_funds(
            wallet_id=wallet_id,
            amount=abs(Decimal(str(amount))),
            transaction_reference=reference,
            description=reason,
        )
        return {"success": True, "data": result, "meta": {"source": "admin.wallet"}}

    async def unlock_wallet(self, *, wallet_id: UUID, amount: float, reason: str, **payload: Any) -> dict[str, Any]:
        await self._require_authorized_admin(payload)
        if self.wallet_balance_service is None:
            self._missing_operation_exception(
                operation="unlock_wallet",
                missing_service="WalletBalanceService",
                missing_method="unlock_funds",
                reason="Wallet balance service dependency is unavailable for admin orchestration.",
            )
        reference = self._optional_text(payload.get("reference")) or self._make_reference("admin-unlock")
        result = await self.wallet_balance_service.unlock_funds(
            wallet_id=wallet_id,
            amount=abs(Decimal(str(amount))),
            transaction_reference=reference,
            description=reason,
        )
        return {"success": True, "data": result, "meta": {"source": "admin.wallet"}}

    async def reconcile_wallet(self, *, wallet_id: UUID, dry_run: bool = True, **payload: Any) -> dict[str, Any]:
        await self._require_authorized_admin(payload)
        if self.wallet_reconciliation_service is None:
            self._missing_operation_exception(
                operation="reconcile_wallet",
                missing_service="WalletReconciliationService",
                missing_method="reconcile_wallet",
                reason="Wallet reconciliation service dependency is unavailable for admin orchestration.",
            )
        result = await self.wallet_reconciliation_service.reconcile_wallet(wallet_id=wallet_id, dry_run=dry_run)
        return {"success": True, "data": result, "meta": {"source": "admin.wallet"}}

    async def _require_authorized_admin(self, payload: dict[str, Any]) -> UUID:
        raw_admin_id = payload.get("admin_id")
        if raw_admin_id is None:
            raise AuthorizationException(detail="Admin identity is required.", error_code="ADMIN_ID_REQUIRED")
        try:
            admin_id = UUID(str(raw_admin_id))
        except (ValueError, TypeError) as exc:
            raise AuthorizationException(detail="Admin identity is invalid.", error_code="ADMIN_ID_INVALID") from exc

        await require_active_admin(self.admin_repository, admin_id)
        return admin_id

    def _require_wallet_repository(self) -> None:
        if self.wallet_repository is None:
            raise ValidationException(detail="Wallet administration dependencies are unavailable.", error_code="ADMIN_WALLET_UNAVAILABLE")

    def _serialize_wallet(self, wallet: Wallet) -> dict[str, Any]:
        user = wallet.user
        user_name = " ".join(
            part.strip()
            for part in (user.first_name, user.last_name)
            if part and part.strip()
        ) if user is not None else ""
        return {
            "id": str(wallet.id),
            "user_id": str(wallet.user_id),
            "user_name": user_name or (user.username if user is not None else None),
            "user_email": user.email if user is not None else None,
            "user_phone": user.phone if user is not None else None,
            "wallet_reference": wallet.wallet_reference,
            "wallet_type": wallet.wallet_type,
            "currency": wallet.currency,
            "status": wallet.status,
            "available_balance": str(wallet.available_balance),
            "ledger_balance": str(wallet.ledger_balance),
            "locked_balance": str(wallet.locked_balance),
            "is_active": wallet.is_active,
            "is_frozen": wallet.is_frozen,
            "is_suspended": wallet.is_suspended,
            "created_at": wallet.created_at.isoformat() if wallet.created_at else None,
            "updated_at": wallet.updated_at.isoformat() if wallet.updated_at else None,
        }

    def _serialize_ledger_entry(self, entry: Ledger) -> dict[str, Any]:
        metadata = entry.metadata_payload if isinstance(entry.metadata_payload, dict) else {}
        adjustment_reason = metadata.get("reason") if entry.transaction_type.upper() in {"ADMIN_CREDIT", "ADMIN_DEBIT"} else None
        return {
            "id": str(entry.ledger_id),
            "transaction_reference": entry.transaction_reference,
            "wallet_id": str(entry.wallet_id),
            "user_id": str(entry.user_id),
            "transaction_type": entry.transaction_type,
            "entry_type": entry.entry_type,
            "created_by": entry.created_by,
            "adjustment_reason": adjustment_reason if isinstance(adjustment_reason, str) else None,
            "description": entry.description,
            "status": entry.status,
            "currency": entry.currency,
            "debit_amount": str(entry.debit_amount),
            "credit_amount": str(entry.credit_amount),
            "opening_balance": str(entry.opening_balance),
            "closing_balance": str(entry.closing_balance),
            "related_transaction_id": str(entry.related_transaction_id) if entry.related_transaction_id else None,
            "created_at": entry.created_at.isoformat() if entry.created_at else None,
        }

    def _missing_operation_exception(
        self,
        *,
        operation: str,
        missing_service: str,
        missing_method: str,
        reason: str,
    ) -> None:
        raise ValidationException(
            detail=f"Admin wallet operation '{operation}' could not be completed: {reason}",
            error_code=f"ADMIN_WALLET_{operation.upper()}_UNAVAILABLE",
        )

    def _make_reference(self, prefix: str) -> str:
        from uuid import uuid4

        return f"{prefix}-{uuid4().hex[:12]}"

    def _build_notification_message(self, *, action: str, reason: str | None) -> str:
        action_label = action.replace("_", " ").strip() or "updated"
        if reason:
            return f"Your wallet has been {action_label} by an administrator. Reason: {reason}"
        return f"Your wallet has been {action_label} by an administrator."

    def _optional_text(self, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            value = str(value)
        trimmed = value.strip()
        return trimmed or None

    def _session_scope(self) -> Any:
        if self.session is None:
            return _NullSessionContext()
        if self.session.in_transaction():
            return _ActiveSessionContext(self.session)
        return self.session.begin()


class _ActiveSessionContext:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def __aenter__(self) -> None:
        if not self.session.in_transaction():
            raise RuntimeError("Expected active transaction for admin wallet operation.")
        return None

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class _NullSessionContext:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False
