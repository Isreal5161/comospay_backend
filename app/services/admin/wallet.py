from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.wallet import Wallet
from app.repositories.admin_repository import AdminRepository
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.wallet_repository import WalletRepository
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
        self.admin_repository = admin_repository
        self.audit_repository = audit_repository
        self.notification_service = notification_service
        self.wallet_service = wallet_service
        self.wallet_balance_service = wallet_balance_service
        self.wallet_statement_service = wallet_statement_service
        self.wallet_reconciliation_service = wallet_reconciliation_service
        self.session = session

    async def adjust_wallet(self, *, user_id: UUID, amount: float, reason: str, **payload: Any) -> dict[str, Any]:
        self._require_wallet_repository()
        admin_id = await self._require_authorized_admin(payload)

        wallet = await self.wallet_repository.get_user_wallet(user_id=user_id)
        if wallet is None:
            raise ValidationException(detail="Wallet not found for user.", error_code="WALLET_NOT_FOUND")

        if self.wallet_balance_service is None:
            self._missing_operation_exception(
                operation="adjust_wallet",
                missing_service="WalletBalanceService",
                missing_method="credit_wallet/debit_wallet",
                reason="Wallet balance service dependency is unavailable for admin orchestration.",
            )

        amount_value = Decimal(str(amount))
        if amount_value == Decimal("0"):
            raise ValidationException(detail="Adjustment amount must be non-zero.", error_code="INVALID_ADJUSTMENT_AMOUNT")

        reference = self._optional_text(payload.get("reference")) or self._make_reference("admin-wallet")
        metadata = {
            "source": "admin.wallet",
            "admin_id": str(admin_id),
            "reason": reason,
        }

        if amount_value > 0:
            operation = "manual_credit"
            result = await self.wallet_balance_service.credit_wallet(
                wallet_id=wallet.id,
                amount=amount_value,
                transaction_reference=reference,
                transaction_type="ADMIN_CREDIT",
                description=reason,
                metadata=metadata,
            )
        else:
            operation = "manual_debit"
            result = await self.wallet_balance_service.debit_wallet(
                wallet_id=wallet.id,
                amount=abs(amount_value),
                transaction_reference=reference,
                transaction_type="ADMIN_DEBIT",
                description=reason,
                metadata=metadata,
            )

        await self._record_admin_action(
            action=operation,
            admin_id=admin_id,
            wallet=wallet,
            reason=reason,
            result=result,
        )
        return {
            "success": True,
            "data": {
                "wallet_id": str(wallet.id),
                "user_id": str(wallet.user_id),
                "operation": operation,
                "amount": str(amount_value),
                "reference": reference,
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
        items = [self._serialize_wallet(item) for item in wallets]

        return {
            "success": True,
            "data": {"items": items, "total": total},
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

    async def _record_admin_action(
        self,
        *,
        action: str,
        admin_id: UUID,
        wallet: Wallet,
        reason: str | None,
        result: dict[str, Any],
    ) -> None:
        async with self._session_scope():
            if self.audit_repository is not None:
                await self.audit_repository.create_audit_log(
                    AuditLog(
                        actor_type="admin",
                        actor_id=str(admin_id),
                        action=f"admin.wallet.{action}",
                        category="admin_wallet",
                        description=f"Admin performed '{action}' on wallet.",
                        resource_type="wallet",
                        resource_id=str(wallet.id),
                        metadata_payload=json.dumps({"reason": reason, "wallet_reference": wallet.wallet_reference}, default=str),
                        new_value=json.dumps(result, default=str),
                    )
                )

            if self.notification_service is not None:
                await self.notification_service.create_notification(
                    user_id=wallet.user_id,
                    title="Wallet Update",
                    message=self._build_notification_message(action=action, reason=reason),
                    notification_type="admin_wallet",
                    category="wallet",
                    reference=wallet.wallet_reference,
                    metadata={"action": action, "wallet_id": str(wallet.id), "reason": reason},
                    channel="in_app",
                )

    async def _require_authorized_admin(self, payload: dict[str, Any]) -> UUID:
        raw_admin_id = payload.get("admin_id")
        if raw_admin_id is None:
            raise AuthorizationException(detail="Admin identity is required.", error_code="ADMIN_ID_REQUIRED")
        try:
            admin_id = UUID(str(raw_admin_id))
        except (ValueError, TypeError) as exc:
            raise AuthorizationException(detail="Admin identity is invalid.", error_code="ADMIN_ID_INVALID") from exc

        if self.admin_repository is not None:
            admin = await self.admin_repository.get_by_id(admin_id)
            if admin is None or not bool(getattr(admin, "is_active", False)):
                raise AuthorizationException(detail="Admin is not authorized.", error_code="ADMIN_NOT_AUTHORIZED")
        return admin_id

    def _require_wallet_repository(self) -> None:
        if self.wallet_repository is None:
            raise ValidationException(detail="Wallet administration dependencies are unavailable.", error_code="ADMIN_WALLET_UNAVAILABLE")

    def _serialize_wallet(self, wallet: Wallet) -> dict[str, Any]:
        return {
            "id": str(wallet.id),
            "user_id": str(wallet.user_id),
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
