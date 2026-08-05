"""Wallet freeze and status lifecycle management service.

This service handles wallet freeze, suspension, closure, and status transitions.
It maintains wallet state integrity through atomic transactions, audit logging,
and security event creation. Every status change is validated, recorded, and
tracked for compliance and forensics.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.wallet import Wallet
from app.repositories.wallet_repository import WalletRepository
from app.utils.exceptions import (
    DatabaseException,
    ValidationException,
    WalletException,
)


class WalletFreezeService:
    """Manages wallet freeze lifecycle and status transitions.

    This service is responsible for:
    - Wallet freeze and unfreeze operations
    - Wallet suspension and restoration
    - Wallet closure and archive
    - Status transition validation
    - Audit logging and security events
    - Transaction state enforcement

    Wallet States:
    - Pending: Initial state after creation
    - Active: Fully operational
    - Frozen: Cannot transact, can view only
    - Suspended: Restricted, under review
    - Closed: Archived, no operations

    All operations are atomic and transactional with full rollback support.
    """

    # Supported wallet states
    VALID_STATUSES = {"pending", "active", "frozen", "suspended", "closed"}

    # State transition rules: from_status -> allowed_to_statuses
    STATE_TRANSITIONS = {
        "pending": {"active", "closed"},
        "active": {"frozen", "suspended", "closed"},
        "frozen": {"active", "closed"},
        "suspended": {"active", "closed"},
        "closed": set(),  # Closed is terminal unless policy allows reopening
    }

    # Readonly operations (operations that can be performed on frozen wallets)
    READONLY_OPERATIONS = {
        "view_balance",
        "view_history",
        "view_statement",
        "view_transactions",
    }

    # Transactional operations (operations that cannot be performed on frozen wallets)
    TRANSACTIONAL_OPERATIONS = {
        "transfer",
        "receive",
        "withdraw",
        "bill_payment",
        "vtu_purchase",
        "fund_wallet",
        "lock_funds",
        "debit",
    }

    def __init__(
        self,
        *,
        wallet_repository: WalletRepository,
        session: AsyncSession,
        audit_service: Any | None = None,
        security_service: Any | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize freeze service with required dependencies.

        Args:
            wallet_repository: Wallet persistence repository.
            session: AsyncSession for database transactions.
            audit_service: Optional audit logging service.
            security_service: Optional security events service.
            logger: Optional logger instance.
        """
        self.wallet_repository = wallet_repository
        self.session = session
        self.audit_service = audit_service
        self.security_service = security_service
        self.logger = logger or logging.getLogger(__name__)

    async def freeze_wallet(
        self,
        *,
        wallet_id: UUID,
        reason: str | None = None,
        freeze_type: str = "manual",
        initiated_by: str = "system",
    ) -> dict[str, Any]:
        """Freeze wallet to prevent all transactional operations.

        This method:
        1. Validates wallet exists
        2. Checks wallet is not already frozen
        3. Validates state transition
        4. Updates wallet status to frozen
        5. Records audit log
        6. Creates security event
        7. Commits atomically or rolls back

        Args:
            wallet_id: Unique wallet identifier.
            reason: Optional reason for freeze.
            freeze_type: Type of freeze (manual, automatic, compliance).
            initiated_by: User or system that initiated freeze.

        Returns:
            Dictionary containing wallet status and freeze details.

        Raises:
            ValidationException: If validation fails.
            WalletException: If state transition invalid.
            DatabaseException: If operation fails.
        """
        self.logger.debug(
            "wallet_freeze_started",
            extra={
                "wallet_id": str(wallet_id),
                "freeze_type": freeze_type,
                "initiated_by": initiated_by,
            },
        )

        try:
            # Acquire row lock for financial operation
            wallet = await self.wallet_repository.get_by_id_for_update(wallet_id)
            if not wallet:
                raise ValidationException(
                    detail=f"Wallet {wallet_id} not found.",
                    error_code="WALLET_NOT_FOUND",
                )

            # Check wallet not already frozen
            if wallet.is_frozen:
                raise WalletException(
                    detail="Wallet is already frozen.",
                    error_code="WALLET_ALREADY_FROZEN",
                )

            # Validate state transition
            if wallet.status not in self.STATE_TRANSITIONS:
                raise WalletException(
                    detail=f"Wallet in unknown state: {wallet.status}",
                    error_code="UNKNOWN_WALLET_STATE",
                )

            if "frozen" not in self.STATE_TRANSITIONS.get(wallet.status, set()):
                raise WalletException(
                    detail=f"Cannot freeze wallet in {wallet.status} state.",
                    error_code="INVALID_STATE_TRANSITION",
                )

            # Store previous state for audit
            previous_status = wallet.status
            previous_frozen = wallet.is_frozen

            # Update wallet
            wallet = await self.wallet_repository.update_wallet(
                wallet,
                status="frozen",
                is_frozen=True,
                metadata_payload=reason or wallet.metadata_payload,
            )

            # Record audit log
            await self._record_status_change(
                wallet_id=wallet_id,
                previous_status=previous_status,
                new_status="frozen",
                change_type="freeze",
                reason=reason,
                initiated_by=initiated_by,
                metadata={
                    "freeze_type": freeze_type,
                    "previous_frozen": previous_frozen,
                },
            )

            # Create security event
            await self._create_security_event(
                wallet_id=wallet_id,
                event_type="WALLET_FROZEN",
                severity="warning",
                details={
                    "reason": reason,
                    "freeze_type": freeze_type,
                    "initiated_by": initiated_by,
                },
            )

            # Commit transaction
            await self.session.commit()

            self.logger.info(
                "wallet_frozen",
                extra={
                    "wallet_id": str(wallet_id),
                    "freeze_type": freeze_type,
                },
            )

            return {
                "wallet_id": str(wallet_id),
                "status": "frozen",
                "is_frozen": True,
                "is_suspended": wallet.is_suspended,
                "is_active": wallet.is_active,
                "previous_status": previous_status,
                "freeze_type": freeze_type,
                "reason": reason,
                "frozen_at": datetime.now(timezone.utc).isoformat(),
            }

        except (ValidationException, WalletException, DatabaseException):
            await self.session.rollback()
            raise
        except Exception as e:
            await self.session.rollback()
            self.logger.error(
                "wallet_freeze_failed",
                extra={"wallet_id": str(wallet_id), "error": str(e)},
            )
            raise DatabaseException(
                detail="Wallet freeze failed. Please try again.",
                error_code="FREEZE_ERROR",
            )

    async def unfreeze_wallet(
        self,
        *,
        wallet_id: UUID,
        reason: str | None = None,
        initiated_by: str = "system",
    ) -> dict[str, Any]:
        """Unfreeze wallet to restore transactional capabilities.

        Args:
            wallet_id: Unique wallet identifier.
            reason: Optional reason for unfreezing.
            initiated_by: User or system that initiated unfreeze.

        Returns:
            Dictionary containing wallet status and unfreeze details.

        Raises:
            ValidationException: If validation fails.
            WalletException: If wallet not frozen.
            DatabaseException: If operation fails.
        """
        self.logger.debug(
            "wallet_unfreeze_started",
            extra={"wallet_id": str(wallet_id), "initiated_by": initiated_by},
        )

        try:
            # Acquire row lock
            wallet = await self.wallet_repository.get_by_id_for_update(wallet_id)
            if not wallet:
                raise ValidationException(
                    detail=f"Wallet {wallet_id} not found.",
                    error_code="WALLET_NOT_FOUND",
                )

            # Validate wallet is frozen
            if not wallet.is_frozen:
                raise WalletException(
                    detail="Wallet is not frozen.",
                    error_code="WALLET_NOT_FROZEN",
                )

            # Update wallet
            wallet = await self.wallet_repository.update_wallet(
                wallet,
                status="active",
                is_frozen=False,
            )

            # Record audit log
            await self._record_status_change(
                wallet_id=wallet_id,
                previous_status="frozen",
                new_status="active",
                change_type="unfreeze",
                reason=reason,
                initiated_by=initiated_by,
            )

            # Create security event
            await self._create_security_event(
                wallet_id=wallet_id,
                event_type="WALLET_UNFROZEN",
                severity="info",
                details={"reason": reason, "initiated_by": initiated_by},
            )

            # Commit transaction
            await self.session.commit()

            self.logger.info(
                "wallet_unfrozen",
                extra={"wallet_id": str(wallet_id)},
            )

            return {
                "wallet_id": str(wallet_id),
                "status": "active",
                "is_frozen": False,
                "is_suspended": wallet.is_suspended,
                "is_active": wallet.is_active,
                "reason": reason,
                "unfrozen_at": datetime.now(timezone.utc).isoformat(),
            }

        except (ValidationException, WalletException, DatabaseException):
            await self.session.rollback()
            raise
        except Exception as e:
            await self.session.rollback()
            self.logger.error(
                "wallet_unfreeze_failed",
                extra={"wallet_id": str(wallet_id), "error": str(e)},
            )
            raise DatabaseException(
                detail="Wallet unfreeze failed. Please try again.",
                error_code="UNFREEZE_ERROR",
            )

    async def suspend_wallet(
        self,
        *,
        wallet_id: UUID,
        reason: str | None = None,
        initiated_by: str = "system",
    ) -> dict[str, Any]:
        """Suspend wallet for temporary restrictions.

        Suspended wallets are under review and cannot transact.

        Args:
            wallet_id: Unique wallet identifier.
            reason: Optional reason for suspension.
            initiated_by: User or system that initiated suspension.

        Returns:
            Dictionary containing wallet status and suspension details.

        Raises:
            ValidationException: If validation fails.
            WalletException: If state transition invalid.
            DatabaseException: If operation fails.
        """
        self.logger.debug(
            "wallet_suspend_started",
            extra={"wallet_id": str(wallet_id)},
        )

        try:
            # Acquire row lock
            wallet = await self.wallet_repository.get_by_id_for_update(wallet_id)
            if not wallet:
                raise ValidationException(
                    detail=f"Wallet {wallet_id} not found.",
                    error_code="WALLET_NOT_FOUND",
                )

            # Validate state transition
            if wallet.status not in self.STATE_TRANSITIONS:
                raise WalletException(
                    detail=f"Wallet in unknown state: {wallet.status}",
                    error_code="UNKNOWN_WALLET_STATE",
                )

            if "suspended" not in self.STATE_TRANSITIONS.get(wallet.status, set()):
                raise WalletException(
                    detail=f"Cannot suspend wallet in {wallet.status} state.",
                    error_code="INVALID_STATE_TRANSITION",
                )

            previous_status = wallet.status

            # Update wallet
            wallet = await self.wallet_repository.update_wallet(
                wallet,
                status="suspended",
                is_suspended=True,
                metadata_payload=reason or wallet.metadata_payload,
            )

            # Record audit log
            await self._record_status_change(
                wallet_id=wallet_id,
                previous_status=previous_status,
                new_status="suspended",
                change_type="suspend",
                reason=reason,
                initiated_by=initiated_by,
            )

            # Create security event
            await self._create_security_event(
                wallet_id=wallet_id,
                event_type="WALLET_SUSPENDED",
                severity="warning",
                details={"reason": reason, "initiated_by": initiated_by},
            )

            await self.session.commit()

            self.logger.info(
                "wallet_suspended",
                extra={"wallet_id": str(wallet_id)},
            )

            return {
                "wallet_id": str(wallet_id),
                "status": "suspended",
                "is_suspended": True,
                "is_frozen": wallet.is_frozen,
                "reason": reason,
                "suspended_at": datetime.now(timezone.utc).isoformat(),
            }

        except (ValidationException, WalletException, DatabaseException):
            await self.session.rollback()
            raise
        except Exception as e:
            await self.session.rollback()
            self.logger.error(
                "wallet_suspend_failed",
                extra={"wallet_id": str(wallet_id), "error": str(e)},
            )
            raise DatabaseException(
                detail="Wallet suspension failed. Please try again.",
                error_code="SUSPEND_ERROR",
            )

    async def unsuspend_wallet(
        self,
        *,
        wallet_id: UUID,
        reason: str | None = None,
        initiated_by: str = "system",
    ) -> dict[str, Any]:
        """Restore suspended wallet to active state.

        Args:
            wallet_id: Unique wallet identifier.
            reason: Optional reason for restoration.
            initiated_by: User or system that initiated restoration.

        Returns:
            Dictionary containing wallet status and restoration details.

        Raises:
            ValidationException: If validation fails.
            WalletException: If wallet not suspended.
            DatabaseException: If operation fails.
        """
        self.logger.debug(
            "wallet_unsuspend_started",
            extra={"wallet_id": str(wallet_id)},
        )

        try:
            wallet = await self.wallet_repository.get_by_id_for_update(wallet_id)
            if not wallet:
                raise ValidationException(
                    detail=f"Wallet {wallet_id} not found.",
                    error_code="WALLET_NOT_FOUND",
                )

            if not wallet.is_suspended:
                raise WalletException(
                    detail="Wallet is not suspended.",
                    error_code="WALLET_NOT_SUSPENDED",
                )

            # Update wallet
            wallet = await self.wallet_repository.update_wallet(
                wallet,
                status="active",
                is_suspended=False,
            )

            # Record audit log
            await self._record_status_change(
                wallet_id=wallet_id,
                previous_status="suspended",
                new_status="active",
                change_type="unsuspend",
                reason=reason,
                initiated_by=initiated_by,
            )

            # Create security event
            await self._create_security_event(
                wallet_id=wallet_id,
                event_type="WALLET_UNSUSPENDED",
                severity="info",
                details={"reason": reason, "initiated_by": initiated_by},
            )

            await self.session.commit()

            self.logger.info(
                "wallet_unsuspended",
                extra={"wallet_id": str(wallet_id)},
            )

            return {
                "wallet_id": str(wallet_id),
                "status": "active",
                "is_suspended": False,
                "is_frozen": wallet.is_frozen,
                "reason": reason,
                "unsuspended_at": datetime.now(timezone.utc).isoformat(),
            }

        except (ValidationException, WalletException, DatabaseException):
            await self.session.rollback()
            raise
        except Exception as e:
            await self.session.rollback()
            self.logger.error(
                "wallet_unsuspend_failed",
                extra={"wallet_id": str(wallet_id), "error": str(e)},
            )
            raise DatabaseException(
                detail="Wallet restoration failed. Please try again.",
                error_code="UNSUSPEND_ERROR",
            )

    async def permanently_close_wallet(
        self,
        *,
        wallet_id: UUID,
        reason: str | None = None,
        initiated_by: str = "system",
    ) -> dict[str, Any]:
        """Permanently close wallet (terminal state).

        Closed wallets cannot perform any operations. This is an irreversible
        action unless explicitly allowed by business policy.

        Args:
            wallet_id: Unique wallet identifier.
            reason: Optional reason for closure.
            initiated_by: User or system that initiated closure.

        Returns:
            Dictionary containing wallet closure details.

        Raises:
            ValidationException: If validation fails.
            WalletException: If wallet already closed.
            DatabaseException: If operation fails.
        """
        self.logger.debug(
            "wallet_closure_started",
            extra={"wallet_id": str(wallet_id)},
        )

        try:
            wallet = await self.wallet_repository.get_by_id_for_update(wallet_id)
            if not wallet:
                raise ValidationException(
                    detail=f"Wallet {wallet_id} not found.",
                    error_code="WALLET_NOT_FOUND",
                )

            if wallet.status == "closed":
                raise WalletException(
                    detail="Wallet is already closed.",
                    error_code="WALLET_ALREADY_CLOSED",
                )

            previous_status = wallet.status

            # Update wallet
            wallet = await self.wallet_repository.update_wallet(
                wallet,
                status="closed",
                is_active=False,
                metadata_payload=reason or wallet.metadata_payload,
            )

            # Record audit log
            await self._record_status_change(
                wallet_id=wallet_id,
                previous_status=previous_status,
                new_status="closed",
                change_type="close",
                reason=reason,
                initiated_by=initiated_by,
            )

            # Create security event
            await self._create_security_event(
                wallet_id=wallet_id,
                event_type="WALLET_CLOSED",
                severity="critical",
                details={"reason": reason, "initiated_by": initiated_by},
            )

            await self.session.commit()

            self.logger.info(
                "wallet_closed",
                extra={"wallet_id": str(wallet_id)},
            )

            return {
                "wallet_id": str(wallet_id),
                "status": "closed",
                "is_active": False,
                "previous_status": previous_status,
                "reason": reason,
                "closed_at": datetime.now(timezone.utc).isoformat(),
            }

        except (ValidationException, WalletException, DatabaseException):
            await self.session.rollback()
            raise
        except Exception as e:
            await self.session.rollback()
            self.logger.error(
                "wallet_closure_failed",
                extra={"wallet_id": str(wallet_id), "error": str(e)},
            )
            raise DatabaseException(
                detail="Wallet closure failed. Please try again.",
                error_code="CLOSE_ERROR",
            )

    async def temporarily_lock_wallet(
        self,
        *,
        wallet_id: UUID,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Temporarily lock wallet (alias for freeze).

        Args:
            wallet_id: Unique wallet identifier.
            reason: Optional reason for lock.

        Returns:
            Dictionary containing lock details.
        """
        return await self.freeze_wallet(
            wallet_id=wallet_id,
            reason=reason,
            freeze_type="temporary",
        )

    async def unlock_wallet(
        self,
        *,
        wallet_id: UUID,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Unlock wallet (alias for unfreeze).

        Args:
            wallet_id: Unique wallet identifier.
            reason: Optional reason for unlock.

        Returns:
            Dictionary containing unlock details.
        """
        return await self.unfreeze_wallet(
            wallet_id=wallet_id,
            reason=reason,
        )

    async def can_perform_transaction(
        self,
        *,
        wallet_id: UUID,
        operation_type: str,
    ) -> bool:
        """Check if wallet can perform a specific operation.

        Args:
            wallet_id: Unique wallet identifier.
            operation_type: Type of operation to check.

        Returns:
            True if operation is allowed, False otherwise.

        Raises:
            ValidationException: If wallet not found.
        """
        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if not wallet:
            raise ValidationException(
                detail=f"Wallet {wallet_id} not found.",
                error_code="WALLET_NOT_FOUND",
            )

        # Readonly operations always allowed for non-closed wallets
        if operation_type in self.READONLY_OPERATIONS:
            return wallet.status != "closed"

        # Transactional operations require active status
        if operation_type in self.TRANSACTIONAL_OPERATIONS:
            return (
                wallet.is_active
                and not wallet.is_frozen
                and not wallet.is_suspended
                and wallet.status == "active"
            )

        # Unknown operations denied by default
        return False

    async def validate_wallet_status(
        self,
        *,
        wallet_id: UUID,
        allow_frozen: bool = False,
        allow_suspended: bool = False,
    ) -> bool:
        """Validate wallet status for operation.

        Args:
            wallet_id: Unique wallet identifier.
            allow_frozen: Whether frozen status is acceptable.
            allow_suspended: Whether suspended status is acceptable.

        Returns:
            True if wallet is in acceptable state.

        Raises:
            ValidationException: If wallet not found or status unacceptable.
        """
        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if not wallet:
            raise ValidationException(
                detail=f"Wallet {wallet_id} not found.",
                error_code="WALLET_NOT_FOUND",
            )

        if wallet.status == "closed":
            raise WalletException(
                detail="Wallet is closed and cannot perform operations.",
                error_code="WALLET_CLOSED",
            )

        if wallet.is_frozen and not allow_frozen:
            raise WalletException(
                detail="Wallet is frozen.",
                error_code="WALLET_FROZEN",
            )

        if wallet.is_suspended and not allow_suspended:
            raise WalletException(
                detail="Wallet is suspended.",
                error_code="WALLET_SUSPENDED",
            )

        return True

    async def get_wallet_status(self, *, wallet_id: UUID) -> dict[str, Any]:
        """Retrieve comprehensive wallet status.

        Args:
            wallet_id: Unique wallet identifier.

        Returns:
            Dictionary containing wallet status and flags.

        Raises:
            ValidationException: If wallet not found.
        """
        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if not wallet:
            raise ValidationException(
                detail=f"Wallet {wallet_id} not found.",
                error_code="WALLET_NOT_FOUND",
            )

        return {
            "wallet_id": str(wallet_id),
            "status": wallet.status,
            "is_active": wallet.is_active,
            "is_frozen": wallet.is_frozen,
            "is_suspended": wallet.is_suspended,
            "can_transact": (
                wallet.is_active
                and not wallet.is_frozen
                and not wallet.is_suspended
                and wallet.status == "active"
            ),
            "wallet_type": wallet.wallet_type,
            "currency": wallet.currency,
            "created_at": wallet.created_at.isoformat(),
            "updated_at": wallet.updated_at.isoformat(),
        }

    async def update_wallet_status(
        self,
        *,
        wallet_id: UUID,
        new_status: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Update wallet status with validation.

        Args:
            wallet_id: Unique wallet identifier.
            new_status: Target status.
            reason: Optional reason for change.

        Returns:
            Dictionary containing updated status.

        Raises:
            ValidationException: If validation fails.
            WalletException: If transition invalid.
        """
        if new_status not in self.VALID_STATUSES:
            raise ValidationException(
                detail=f"Invalid wallet status: {new_status}",
                error_code="INVALID_STATUS",
            )

        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if not wallet:
            raise ValidationException(
                detail=f"Wallet {wallet_id} not found.",
                error_code="WALLET_NOT_FOUND",
            )

        # Validate transition
        if wallet.status not in self.STATE_TRANSITIONS:
            raise WalletException(
                detail=f"Wallet in unknown state: {wallet.status}",
                error_code="UNKNOWN_WALLET_STATE",
            )

        allowed = self.STATE_TRANSITIONS.get(wallet.status, set())
        if new_status not in allowed:
            raise WalletException(
                detail=f"Cannot transition from {wallet.status} to {new_status}.",
                error_code="INVALID_STATE_TRANSITION",
            )

        # Use appropriate method for transition
        if new_status == "frozen":
            return await self.freeze_wallet(wallet_id=wallet_id, reason=reason)
        elif new_status == "suspended":
            return await self.suspend_wallet(wallet_id=wallet_id, reason=reason)
        elif new_status == "closed":
            return await self.permanently_close_wallet(wallet_id=wallet_id, reason=reason)
        elif new_status == "active":
            if wallet.is_frozen:
                return await self.unfreeze_wallet(wallet_id=wallet_id, reason=reason)
            elif wallet.is_suspended:
                return await self.unsuspend_wallet(wallet_id=wallet_id, reason=reason)
            else:
                return await self.get_wallet_status(wallet_id=wallet_id)
        else:
            return await self.get_wallet_status(wallet_id=wallet_id)

    async def record_status_change(
        self,
        *,
        wallet_id: UUID,
        previous_status: str,
        new_status: str,
        change_type: str,
        reason: str | None = None,
        initiated_by: str = "system",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record a wallet status change in audit log.

        Args:
            wallet_id: Unique wallet identifier.
            previous_status: Previous wallet status.
            new_status: New wallet status.
            change_type: Type of change.
            reason: Optional reason.
            initiated_by: Who initiated the change.
            metadata: Optional additional metadata.
        """
        await self._record_status_change(
            wallet_id=wallet_id,
            previous_status=previous_status,
            new_status=new_status,
            change_type=change_type,
            reason=reason,
            initiated_by=initiated_by,
            metadata=metadata,
        )

    async def get_freeze_history(
        self,
        *,
        wallet_id: UUID,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Retrieve freeze history for wallet.

        Args:
            wallet_id: Unique wallet identifier.
            limit: Maximum records to retrieve.

        Returns:
            List of freeze/status change records.

        Raises:
            ValidationException: If wallet not found.
        """
        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if not wallet:
            raise ValidationException(
                detail=f"Wallet {wallet_id} not found.",
                error_code="WALLET_NOT_FOUND",
            )

        # This would typically query an audit log table
        # For now, return empty list as placeholder
        # In production, implement with audit service
        self.logger.debug(
            "freeze_history_retrieved",
            extra={"wallet_id": str(wallet_id), "limit": limit},
        )

        return []

    async def _record_status_change(
        self,
        *,
        wallet_id: UUID,
        previous_status: str,
        new_status: str,
        change_type: str,
        reason: str | None = None,
        initiated_by: str = "system",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record status change to audit log.

        Args:
            wallet_id: Unique wallet identifier.
            previous_status: Previous status.
            new_status: New status.
            change_type: Type of change.
            reason: Optional reason.
            initiated_by: User or system that initiated change.
            metadata: Optional additional metadata.
        """
        if not self.audit_service:
            self.logger.debug(
                "audit_service_not_configured",
                extra={"wallet_id": str(wallet_id)},
            )
            return

        try:
            await self.audit_service.log_wallet_status_change(
                wallet_id=wallet_id,
                previous_status=previous_status,
                new_status=new_status,
                change_type=change_type,
                reason=reason,
                initiated_by=initiated_by,
                metadata=metadata or {},
            )
        except Exception as e:
            self.logger.warning(
                "audit_log_failed",
                extra={
                    "wallet_id": str(wallet_id),
                    "error": str(e),
                },
            )

    async def _create_security_event(
        self,
        *,
        wallet_id: UUID,
        event_type: str,
        severity: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Create security event for wallet operation.

        Args:
            wallet_id: Unique wallet identifier.
            event_type: Type of security event.
            severity: Event severity level.
            details: Optional event details.
        """
        if not self.security_service:
            self.logger.debug(
                "security_service_not_configured",
                extra={"wallet_id": str(wallet_id)},
            )
            return

        try:
            await self.security_service.create_event(
                event_type=event_type,
                severity=severity,
                entity_type="wallet",
                entity_id=str(wallet_id),
                details=details or {},
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            self.logger.warning(
                "security_event_failed",
                extra={
                    "wallet_id": str(wallet_id),
                    "event_type": event_type,
                    "error": str(e),
                },
            )
