from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.transaction import Transaction
from app.repositories.admin_repository import AdminRepository
from app.repositories.audit_log_repository import AuditLogRepository
from app.repositories.transaction_repository import TransactionRepository
from app.services.airtime_service import AirtimeService
from app.services.data_service import DataService
from app.services.education_service import EducationService
from app.services.electricity_service import ElectricityService
from app.services.notification_service import NotificationService
from app.services.payment_service import PaymentService
from app.services.transaction_owner import TransactionOwnerResolver
from app.services.tv_service import TVService
from app.services.wallet.funding import WalletFundingService
from app.services.wallet.transfer import WalletTransferService
from app.services.wallet.withdrawal import WalletWithdrawalService
from app.utils.exceptions import AuthorizationException, ValidationException


class TransactionAdministrationService:
    """Service for transaction oversight and reversal operations."""

    def __init__(
        self,
        logger: logging.Logger | None = None,
        transaction_repository: TransactionRepository | None = None,
        admin_repository: AdminRepository | None = None,
        audit_repository: AuditLogRepository | None = None,
        notification_service: NotificationService | None = None,
        wallet_funding_service: WalletFundingService | None = None,
        wallet_transfer_service: WalletTransferService | None = None,
        wallet_withdrawal_service: WalletWithdrawalService | None = None,
        airtime_service: AirtimeService | None = None,
        data_service: DataService | None = None,
        electricity_service: ElectricityService | None = None,
        tv_service: TVService | None = None,
        education_service: EducationService | None = None,
        payment_service: PaymentService | None = None,
        transaction_owner_resolver: TransactionOwnerResolver | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.transaction_repository = transaction_repository
        self.admin_repository = admin_repository
        self.audit_repository = audit_repository
        self.notification_service = notification_service
        self.wallet_funding_service = wallet_funding_service
        self.wallet_transfer_service = wallet_transfer_service
        self.wallet_withdrawal_service = wallet_withdrawal_service
        self.airtime_service = airtime_service
        self.data_service = data_service
        self.electricity_service = electricity_service
        self.tv_service = tv_service
        self.education_service = education_service
        self.payment_service = payment_service
        self.transaction_owner_resolver = transaction_owner_resolver
        self.session = session

    async def list_transactions(self, **payload: Any) -> dict[str, Any]:
        self._require_transaction_repository()
        await self._require_authorized_admin(payload)

        page = max(1, int(payload.get("page") or 1))
        page_size = max(1, min(100, int(payload.get("page_size") or 20)))
        status = self._optional_text(payload.get("status"))
        category = self._optional_text(payload.get("category"))
        transaction_type = self._optional_text(payload.get("transaction_type"))
        reference = self._optional_text(payload.get("reference"))
        user_id = self._optional_text(payload.get("user_id"))
        order_by = str(payload.get("order_by") or "created_at")
        descending = bool(payload.get("descending", True))

        items, total = await self.transaction_repository.get_admin_transactions(
            page=page,
            page_size=page_size,
            status=status,
            category=category,
            transaction_type=transaction_type,
            reference=reference,
            user_id=user_id,
            order_by=order_by,
            descending=descending,
        )
        serialized_items = [self._serialize_transaction(item) for item in items]

        return {
            "success": True,
            "data": {"items": serialized_items, "total": total},
            "meta": {"source": "admin.transactions", "page": page, "page_size": page_size},
        }

    async def get_transaction_details(self, *, transaction_id: UUID, **payload: Any) -> dict[str, Any]:
        self._require_transaction_repository()
        await self._require_authorized_admin(payload)

        transaction = await self.transaction_repository.get_by_id(transaction_id)
        if transaction is None:
            raise ValidationException(detail="Transaction not found.", error_code="TRANSACTION_NOT_FOUND")
        return {
            "success": True,
            "data": self._serialize_transaction(transaction, include_metadata=True),
            "meta": {"source": "admin.transactions"},
        }

    async def reverse_transaction(self, *, transaction_id: str | UUID, **payload: Any) -> dict[str, Any]:
        self._require_transaction_repository()
        admin_id = await self._require_authorized_admin(payload)
        reason = self._optional_text(payload.get("reason"))

        transaction = await self._get_transaction_or_raise(transaction_id)
        owner = self._resolve_transaction_owner(transaction)

        if owner == "wallet_funding":
            if self.wallet_funding_service is None:
                self._missing_operation_exception(
                    operation="reverse_transaction",
                    missing_service="WalletFundingService",
                    missing_method="reverse_wallet_credit",
                    reason="Wallet funding service dependency is unavailable for orchestration.",
                )
            result = await self.wallet_funding_service.reverse_wallet_credit(transaction_id=transaction.id, reason=reason)
        elif owner == "wallet_transfer":
            if self.wallet_transfer_service is None:
                self._missing_operation_exception(
                    operation="reverse_transaction",
                    missing_service="WalletTransferService",
                    missing_method="reverse_transfer",
                    reason="Wallet transfer service dependency is unavailable for orchestration.",
                )
            result = await self.wallet_transfer_service.reverse_transfer(transaction_id=transaction.id, reason=reason)
        elif owner == "wallet_withdrawal":
            if self.wallet_withdrawal_service is None:
                self._missing_operation_exception(
                    operation="reverse_transaction",
                    missing_service="WalletWithdrawalService",
                    missing_method="reverse_withdrawal",
                    reason="Wallet withdrawal service dependency is unavailable for orchestration.",
                )
            result = await self.wallet_withdrawal_service.reverse_withdrawal(transaction_id=transaction.id, reason=reason)
        elif owner == "airtime":
            if self.airtime_service is None:
                self._missing_operation_exception(
                    operation="reverse_transaction",
                    missing_service="AirtimeService",
                    missing_method="reverse_airtime_purchase",
                    reason="Airtime service dependency is unavailable for orchestration.",
                )
            result = await self.airtime_service.reverse_airtime_purchase(reference=transaction.reference, reason=reason)
        elif owner == "data":
            if self.data_service is None:
                self._missing_operation_exception(
                    operation="reverse_transaction",
                    missing_service="DataService",
                    missing_method="reverse_data_purchase",
                    reason="Data service dependency is unavailable for orchestration.",
                )
            result = await self.data_service.reverse_data_purchase(reference=transaction.reference, reason=reason)
        elif owner == "electricity":
            if self.electricity_service is None:
                self._missing_operation_exception(
                    operation="reverse_transaction",
                    missing_service="ElectricityService",
                    missing_method="reverse_electricity_purchase",
                    reason="Electricity service dependency is unavailable for orchestration.",
                )
            result = await self.electricity_service.reverse_electricity_purchase(reference=transaction.reference, reason=reason)
        elif owner == "tv":
            self._missing_operation_exception(
                operation="reverse_transaction",
                missing_service="TVPurchaseService",
                missing_method="reverse_tv_purchase",
                reason="TV domain does not expose a public reversal method for admin orchestration.",
            )
        elif owner == "education":
            self._missing_operation_exception(
                operation="reverse_transaction",
                missing_service="EducationPurchaseService",
                missing_method="reverse_education_purchase",
                reason="Education domain does not expose a public reversal method for admin orchestration.",
            )
        elif owner == "payment":
            if self.payment_service is None:
                self._missing_operation_exception(
                    operation="reverse_transaction",
                    missing_service="PaymentService",
                    missing_method="refund_payment",
                    reason="Payment service dependency is unavailable for orchestration.",
                )
            result = await self.payment_service.refund_payment(reference=transaction.reference, reason=reason)
        else:
            self._missing_operation_exception(
                operation="reverse_transaction",
                missing_service="TransactionDomainOwner",
                missing_method="owner_resolution",
                reason=f"Unsupported transaction owner for type '{transaction.transaction_type}'.",
            )

        await self._record_admin_action(
            action="reverse",
            admin_id=admin_id,
            transaction=transaction,
            reason=reason,
            result=result,
        )
        return {
            "success": True,
            "data": {
                "transaction_id": str(transaction.id),
                "reference": transaction.reference,
                "owner": owner,
                "result": result,
            },
            "meta": {"source": "admin.transactions"},
        }

    async def retry_failed_transaction(self, *, transaction_id: UUID, **payload: Any) -> dict[str, Any]:
        self._require_transaction_repository()
        admin_id = await self._require_authorized_admin(payload)
        reason = self._optional_text(payload.get("reason"))

        transaction = await self._get_transaction_or_raise(transaction_id)
        owner = self._resolve_transaction_owner(transaction)

        if owner == "wallet_funding":
            if self.wallet_funding_service is None:
                self._missing_operation_exception(
                    operation="retry_failed_transaction",
                    missing_service="WalletFundingService",
                    missing_method="verify_wallet_funding",
                    reason="Wallet funding service dependency is unavailable for orchestration.",
                )
            result = await self.wallet_funding_service.verify_wallet_funding(reference=transaction.reference)
        elif owner == "airtime":
            if self.airtime_service is None:
                self._missing_operation_exception(
                    operation="retry_failed_transaction",
                    missing_service="AirtimeService",
                    missing_method="retry_airtime_purchase",
                    reason="Airtime service dependency is unavailable for orchestration.",
                )
            result = await self.airtime_service.retry_airtime_purchase(reference=transaction.reference)
        elif owner == "data":
            if self.data_service is None:
                self._missing_operation_exception(
                    operation="retry_failed_transaction",
                    missing_service="DataService",
                    missing_method="retry_data_purchase",
                    reason="Data service dependency is unavailable for orchestration.",
                )
            result = await self.data_service.retry_data_purchase(reference=transaction.reference)
        elif owner == "electricity":
            if self.electricity_service is None:
                self._missing_operation_exception(
                    operation="retry_failed_transaction",
                    missing_service="ElectricityService",
                    missing_method="retry_electricity_purchase",
                    reason="Electricity service dependency is unavailable for orchestration.",
                )
            result = await self.electricity_service.retry_electricity_purchase(reference=transaction.reference)
        elif owner == "tv":
            if self.tv_service is None:
                self._missing_operation_exception(
                    operation="retry_failed_transaction",
                    missing_service="TVService",
                    missing_method="retry_tv_purchase",
                    reason="TV service dependency is unavailable for orchestration.",
                )
            result = await self.tv_service.retry_tv_purchase(reference=transaction.reference)
        elif owner == "education":
            if self.education_service is None:
                self._missing_operation_exception(
                    operation="retry_failed_transaction",
                    missing_service="EducationService",
                    missing_method="retry_education_purchase",
                    reason="Education service dependency is unavailable for orchestration.",
                )
            try:
                result = await self.education_service.retry_education_purchase(reference=transaction.reference)
            except ValidationException:
                self._missing_operation_exception(
                    operation="retry_failed_transaction",
                    missing_service="EducationService",
                    missing_method="retry_education_purchase(reference) without provider callback",
                    reason="Education retry requires provider callback flow that is not exposed for admin orchestration.",
                )
        else:
            self._missing_operation_exception(
                operation="retry_failed_transaction",
                missing_service=f"{owner}_service",
                missing_method="retry",
                reason="No retry operation is exposed for this transaction owner.",
            )

        await self._record_admin_action(
            action="retry",
            admin_id=admin_id,
            transaction=transaction,
            reason=reason,
            result=result,
        )
        return {
            "success": True,
            "data": {
                "transaction_id": str(transaction.id),
                "reference": transaction.reference,
                "owner": owner,
                "result": result,
            },
            "meta": {"source": "admin.transactions"},
        }

    async def resolve_transaction(self, *, transaction_id: UUID, **payload: Any) -> dict[str, Any]:
        self._require_transaction_repository()
        admin_id = await self._require_authorized_admin(payload)

        transaction = await self._get_transaction_or_raise(transaction_id)
        owner = self._resolve_transaction_owner(transaction)
        provider_status = self._optional_text(payload.get("provider_status"))

        if owner == "wallet_funding":
            if self.wallet_funding_service is None:
                self._missing_operation_exception(
                    operation="resolve_transaction",
                    missing_service="WalletFundingService",
                    missing_method="reconcile_wallet_funding",
                    reason="Wallet funding service dependency is unavailable for orchestration.",
                )
            result = await self.wallet_funding_service.reconcile_wallet_funding(reference=transaction.reference)
        elif owner == "airtime":
            if self.airtime_service is None:
                self._missing_operation_exception(
                    operation="resolve_transaction",
                    missing_service="AirtimeService",
                    missing_method="reconcile_transaction",
                    reason="Airtime service dependency is unavailable for orchestration.",
                )
            result = await self.airtime_service.reconcile_transaction(reference=transaction.reference)
        elif owner == "data":
            if self.data_service is None:
                self._missing_operation_exception(
                    operation="resolve_transaction",
                    missing_service="DataService",
                    missing_method="reconcile_transaction",
                    reason="Data service dependency is unavailable for orchestration.",
                )
            result = await self.data_service.reconcile_transaction(reference=transaction.reference)
        elif owner == "electricity":
            if self.electricity_service is None:
                self._missing_operation_exception(
                    operation="resolve_transaction",
                    missing_service="ElectricityService",
                    missing_method="reconcile_transaction",
                    reason="Electricity service dependency is unavailable for orchestration.",
                )
            result = await self.electricity_service.reconcile_transaction(reference=transaction.reference)
        elif owner == "tv":
            if self.tv_service is None:
                self._missing_operation_exception(
                    operation="resolve_transaction",
                    missing_service="TVService",
                    missing_method="reconcile_transaction",
                    reason="TV service dependency is unavailable for orchestration.",
                )
            result = await self.tv_service.reconcile_transaction(reference=transaction.reference)
        elif owner == "education":
            if self.education_service is None:
                self._missing_operation_exception(
                    operation="resolve_transaction",
                    missing_service="EducationService",
                    missing_method="reconcile_transaction",
                    reason="Education service dependency is unavailable for orchestration.",
                )
            result = await self.education_service.reconcile_transaction(reference=transaction.reference)
        elif owner == "payment":
            if self.payment_service is None:
                self._missing_operation_exception(
                    operation="resolve_transaction",
                    missing_service="PaymentService",
                    missing_method="reconcile_payment",
                    reason="Payment service dependency is unavailable for orchestration.",
                )
            result = await self.payment_service.reconcile_payment(reference=transaction.reference, provider_status=provider_status)
        else:
            self._missing_operation_exception(
                operation="resolve_transaction",
                missing_service=f"{owner}_service",
                missing_method="reconcile",
                reason="No reconciliation operation is exposed for this transaction owner.",
            )

        await self._record_admin_action(
            action="resolve",
            admin_id=admin_id,
            transaction=transaction,
            reason=provider_status,
            result=result,
        )
        return {
            "success": True,
            "data": {
                "transaction_id": str(transaction.id),
                "reference": transaction.reference,
                "owner": owner,
                "result": result,
            },
            "meta": {"source": "admin.transactions"},
        }

    async def transaction_timeline(self, *, transaction_id: UUID, **payload: Any) -> dict[str, Any]:
        self._require_transaction_repository()
        await self._require_authorized_admin(payload)

        transaction = await self._get_transaction_or_raise(transaction_id)
        timeline: list[dict[str, Any]] = []

        if self.audit_repository is not None:
            audit_items = await self.audit_repository.get_transaction_timeline_events(transaction_id=transaction.id)
            for item in audit_items:
                timeline.append(
                    {
                        "at": item.created_at.isoformat() if item.created_at else None,
                        "event": item.action,
                        "actor_type": item.actor_type,
                        "actor_id": item.actor_id,
                        "description": item.description,
                    }
                )

        return {
            "success": True,
            "data": {
                "transaction": self._serialize_transaction(transaction, include_metadata=True),
                "timeline": timeline,
            },
            "meta": {"source": "admin.transactions"},
        }

    async def _record_admin_action(
        self,
        *,
        action: str,
        admin_id: UUID,
        transaction: Transaction,
        reason: str | None,
        result: dict[str, Any],
    ) -> None:
        async with self._session_scope():
            if self.audit_repository is not None:
                await self.audit_repository.create_audit_log(
                    AuditLog(
                        actor_type="admin",
                        actor_id=str(admin_id),
                        action=f"admin.transaction.{action}",
                        category="admin_transaction",
                        description=f"Admin performed '{action}' on transaction.",
                        resource_type="transaction",
                        resource_id=str(transaction.id),
                        metadata_payload=json.dumps({"reason": reason, "reference": transaction.reference}, default=str),
                        old_value=json.dumps({"status": transaction.status}, default=str),
                        new_value=json.dumps(result, default=str),
                    )
                )

            if self.notification_service is not None:
                await self.notification_service.create_notification(
                    user_id=transaction.user_id,
                    title="Transaction Update",
                    message=self._build_notification_message(action=action, reason=reason),
                    notification_type="admin_transaction",
                    category="transaction",
                    reference=transaction.reference,
                    metadata={"action": action, "transaction_id": str(transaction.id), "reason": reason},
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

    def _resolve_transaction_owner(self, transaction: Transaction) -> str:
        if self.transaction_owner_resolver is None:
            return "unknown"
        return self.transaction_owner_resolver.resolve(transaction)

    async def _get_transaction_or_raise(self, transaction_id: UUID | str) -> Transaction:
        self._require_transaction_repository()
        resolved_id = transaction_id if isinstance(transaction_id, UUID) else UUID(str(transaction_id))
        transaction = await self.transaction_repository.get_by_id(resolved_id)
        if transaction is None:
            raise ValidationException(detail="Transaction not found.", error_code="TRANSACTION_NOT_FOUND")
        return transaction

    def _require_transaction_repository(self) -> None:
        if self.transaction_repository is None:
            raise ValidationException(detail="Transaction administration dependencies are unavailable.", error_code="ADMIN_TRANSACTION_UNAVAILABLE")

    def _serialize_transaction(self, transaction: Transaction, *, include_metadata: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": str(transaction.id),
            "reference": transaction.reference,
            "user_id": str(transaction.user_id),
            "wallet_id": str(transaction.wallet_id) if transaction.wallet_id else None,
            "transaction_type": transaction.transaction_type,
            "category": transaction.category,
            "amount": str(transaction.amount),
            "currency": transaction.currency,
            "charges": str(transaction.charges),
            "total_amount": str(transaction.total_amount),
            "status": transaction.status,
            "provider_name": transaction.provider_name,
            "provider_reference": transaction.provider_reference,
            "provider_transaction_id": transaction.provider_transaction_id,
            "external_reference": transaction.external_reference,
            "description": transaction.description,
            "created_at": transaction.created_at.isoformat() if transaction.created_at else None,
            "updated_at": transaction.updated_at.isoformat() if transaction.updated_at else None,
        }
        if include_metadata:
            data["metadata_payload"] = self._parse_json(transaction.metadata_payload)
        return data

    def _parse_json(self, raw_value: str | None) -> dict[str, Any]:
        if not raw_value:
            return {}
        try:
            parsed = json.loads(raw_value)
            if isinstance(parsed, dict):
                return parsed
            return {"value": parsed}
        except json.JSONDecodeError:
            return {"value": raw_value}

    def _missing_operation_exception(
        self,
        *,
        operation: str,
        missing_service: str,
        missing_method: str,
        reason: str,
    ) -> None:
        raise ValidationException(
            detail=f"Admin transaction '{operation}' could not be completed: {reason}",
            error_code=f"ADMIN_TRANSACTION_{operation.upper()}_UNAVAILABLE",
        )

    def _build_notification_message(self, *, action: str, reason: str | None) -> str:
        action_label = action.replace("_", " ").strip() or "updated"
        if reason:
            return f"Your transaction has been {action_label} by an administrator. Reason: {reason}"
        return f"Your transaction has been {action_label} by an administrator."

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
            raise RuntimeError("Expected active transaction for admin transaction operation.")
        return None

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class _NullSessionContext:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False
