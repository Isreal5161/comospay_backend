"""Wallet lifecycle management service.

This service handles enterprise wallet creation, activation, state transitions,
and core wallet operations. It coordinates with the user repository, wallet
repository, and virtual account service to ensure atomic and transactionally
consistent wallet provisioning.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.helpers.generate_reference import generate_wallet_reference
from app.models.wallet import Wallet
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.utils.exceptions import (
    DatabaseException,
    ValidationException,
    WalletException,
)

if TYPE_CHECKING:
    from app.services.virtual_account_service import VirtualAccountService


class WalletManager:
    """Manages wallet lifecycle including creation, activation, and state transitions.

    This service is responsible for:
    - Wallet creation with atomic transaction handling
    - User validation and wallet ownership enforcement
    - Wallet state transitions and status management
    - Coordination with virtual account provisioning
    - Audit logging for all wallet operations

    Business logic remains in this service layer. Repositories handle only
    persistence. VirtualAccountService handles provider coordination.
    """

    # Supported wallet lifecycle states
    VALID_STATUSES = {"pending", "active", "suspended", "frozen", "closed"}

    # State transition rules: from_status -> allowed_to_statuses
    STATE_TRANSITIONS = {
        "pending": {"active", "closed"},
        "active": {"suspended", "frozen", "closed"},
        "suspended": {"active", "closed"},
        "frozen": {"active", "closed"},
        "closed": {"active"},  # Allow reopening
    }

    def __init__(
        self,
        *,
        user_repository: UserRepository,
        wallet_repository: WalletRepository,
        virtual_account_service: "VirtualAccountService | None" = None,
        session: AsyncSession,
        logger: logging.Logger | None = None,
        audit_service: Any | None = None,
    ) -> None:
        """Initialize the wallet manager with required dependencies.

        Args:
            user_repository: User persistence repository.
            wallet_repository: Wallet persistence repository.
            virtual_account_service: Service for virtual account coordination.
            session: AsyncSession for database transactions.
            logger: Optional logger instance.
        """
        self.user_repository = user_repository
        self.wallet_repository = wallet_repository
        self.virtual_account_service = virtual_account_service
        self.session = session
        self.logger = logger or logging.getLogger(__name__)
        self.audit_service = audit_service

    async def create_wallet(
        self,
        *,
        user_id: UUID,
        wallet_type: str = "customer",
        currency: str = "NGN",
        wallet_reference: str | None = None,
        metadata_payload: str | None = None,
        request_virtual_account: bool = True,
    ) -> dict[str, Any]:
        """Create a new wallet with atomic transaction guarantee.

        This method orchestrates the complete wallet creation workflow:
        1. Validate user exists and is active
        2. Check user does not already have a wallet
        3. Generate wallet reference
        4. Create wallet record in database
        5. Request virtual account creation (if enabled)
        6. Commit transaction on success, rollback on failure

        Args:
            user_id: Unique identifier of wallet owner.
            wallet_type: Category of wallet (default: "customer").
            currency: ISO currency code (default: "NGN").
            wallet_reference: Optional external wallet reference.
            metadata_payload: Optional non-sensitive metadata.
            request_virtual_account: Whether to request provider virtual account.

        Returns:
            Dictionary containing wallet details and virtual account info.

        Raises:
            ValidationException: If validation fails.
            DatabaseException: If database operation fails.
            WalletException: If wallet creation workflow fails.
        """
        self.logger.debug(
            "wallet_creation_started",
            extra={"user_id": str(user_id), "wallet_type": wallet_type},
        )

        try:
            # Validate user exists and is active
            user = await self.user_repository.get_by_id(user_id)
            if not user:
                raise ValidationException(
                    detail=f"User {user_id} does not exist.",
                    error_code="USER_NOT_FOUND",
                )

            if not getattr(user, "is_active", True):
                raise ValidationException(
                    detail="User account is not active.",
                    error_code="USER_INACTIVE",
                )

            # Check user does not already have a wallet
            existing_wallet = await self.wallet_repository.get_user_wallet(user_id=user_id)
            if existing_wallet:
                raise WalletException(
                    detail=f"User {user_id} already has an active wallet.",
                    error_code="WALLET_ALREADY_EXISTS",
                )

            # Validate wallet type
            if wallet_type not in {"customer", "merchant", "business", "vendor"}:
                raise ValidationException(
                    detail=f"Invalid wallet type: {wallet_type}",
                    error_code="INVALID_WALLET_TYPE",
                )

            # Validate currency
            if not currency or len(currency) != 3:
                raise ValidationException(
                    detail=f"Invalid currency code: {currency}",
                    error_code="INVALID_CURRENCY",
                )

            # Generate wallet reference if not provided
            reference = wallet_reference or generate_wallet_reference()

            # Create wallet in pending state
            wallet = Wallet(
                user_id=user_id,
                wallet_reference=reference,
                wallet_type=wallet_type,
                currency=currency.upper(),
                status="pending",
                is_active=False,
                is_frozen=False,
                is_suspended=False,
                available_balance=Decimal("0.00"),
                ledger_balance=Decimal("0.00"),
                locked_balance=Decimal("0.00"),
                metadata_payload=self._sanitize_metadata(metadata_payload),
            )

            # Persist wallet to database
            wallet = await self.wallet_repository.create_wallet(wallet)
            self.logger.info(
                "wallet_created",
                extra={"wallet_id": str(wallet.id), "user_id": str(user_id)},
            )
            await self._log_event("wallet_created", user_id=user_id)

            # Request virtual account creation if enabled
            virtual_account = None
            if request_virtual_account:
                virtual_account_service = self.virtual_account_service
                if virtual_account_service is None:
                    self.logger.warning(
                        "virtual_account_service_unavailable",
                        extra={"wallet_id": str(wallet.id), "user_id": str(user_id)},
                    )
                else:
                    try:
                        virtual_account = (
                            await virtual_account_service.create_virtual_account(
                                wallet_id=wallet.id,
                                user_id=user_id,
                                provider="flutterwave",
                                account_number="",  # Provider will generate
                                currency=currency,
                                is_primary=True,
                            )
                        )
                        self.logger.info(
                            "virtual_account_created",
                            extra={
                                "wallet_id": str(wallet.id),
                                "virtual_account_id": str(virtual_account.id),
                            },
                        )
                    except Exception as va_error:
                        self.logger.warning(
                            "virtual_account_creation_failed",
                            extra={
                                "wallet_id": str(wallet.id),
                                "error": str(va_error),
                            },
                        )

            # Activate wallet
            wallet = await self._activate_wallet_record(wallet_id=wallet.id)

            # Commit transaction
            await self.session.commit()

            return {
                "wallet_id": str(wallet.id),
                "user_id": str(user_id),
                "wallet_reference": wallet.wallet_reference,
                "wallet_type": wallet.wallet_type,
                "currency": wallet.currency,
                "status": wallet.status,
                "available_balance": float(wallet.available_balance),
                "ledger_balance": float(wallet.ledger_balance),
                "locked_balance": float(wallet.locked_balance),
                "is_active": wallet.is_active,
                "created_at": wallet.created_at.isoformat(),
                "virtual_account": {
                    "id": str(virtual_account.id) if virtual_account else None,
                    "provider": virtual_account.provider if virtual_account else None,
                    "account_number": (
                        virtual_account.account_number if virtual_account else None
                    ),
                    "account_name": (
                        virtual_account.account_name if virtual_account else None
                    ),
                    "bank_name": (
                        virtual_account.bank_name if virtual_account else None
                    ),
                }
                if virtual_account
                else None,
            }

        except (ValidationException, WalletException, DatabaseException):
            await self.session.rollback()
            raise
        except Exception as e:
            await self.session.rollback()
            self.logger.error(
                "wallet_creation_failed",
                extra={"user_id": str(user_id), "error": str(e)},
            )
            raise DatabaseException(
                detail="Wallet creation failed. Please try again.",
                error_code="WALLET_CREATION_ERROR",
            )

    async def initialize_wallet(self, *, wallet_id: UUID) -> dict[str, Any]:
        """Initialize a wallet after creation.

        This method ensures the wallet is in a consistent state and ready
        for transactions.

        Args:
            wallet_id: Unique wallet identifier.

        Returns:
            Dictionary containing wallet details.

        Raises:
            ValidationException: If wallet not found.
            DatabaseException: If database operation fails.
        """
        self.logger.debug("wallet_initialization", extra={"wallet_id": str(wallet_id)})

        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if not wallet:
            raise ValidationException(
                detail=f"Wallet {wallet_id} not found.",
                error_code="WALLET_NOT_FOUND",
            )

        return await self.get_wallet(wallet_id=wallet_id)

    async def activate_wallet(self, *, wallet_id: UUID) -> dict[str, Any]:
        """Transition wallet to active state and return a serialized wallet payload."""
        wallet = await self._activate_wallet_record(wallet_id=wallet_id)
        return self._serialize_wallet(wallet)

    async def suspend_wallet(self, *, wallet_id: UUID, reason: str | None = None) -> dict[str, Any]:
        """Transition wallet to suspended state.

        Suspended wallets cannot transact but can be reactivated.

        Args:
            wallet_id: Unique wallet identifier.
            reason: Optional reason for suspension.

        Returns:
            Dictionary containing updated wallet details.

        Raises:
            ValidationException: If wallet not found or transition invalid.
            DatabaseException: If database operation fails.
        """
        self.logger.debug(
            "wallet_suspension",
            extra={"wallet_id": str(wallet_id), "reason": reason},
        )

        wallet = await self.wallet_repository.get_by_id_for_update(wallet_id)
        if not wallet:
            raise ValidationException(
                detail=f"Wallet {wallet_id} not found.",
                error_code="WALLET_NOT_FOUND",
            )

        if wallet.status not in self.STATE_TRANSITIONS.get(wallet.status, set()):
            if "suspended" not in self.STATE_TRANSITIONS.get(wallet.status, set()):
                raise WalletException(
                    detail=f"Cannot suspend wallet in {wallet.status} state.",
                    error_code="INVALID_STATE_TRANSITION",
                )

        wallet = await self.wallet_repository.update_wallet(
            wallet,
            status="suspended",
            is_suspended=True,
            metadata_payload=reason or wallet.metadata_payload,
        )

        self.logger.info(
            "wallet_suspended",
            extra={"wallet_id": str(wallet_id)},
        )
        await self._log_event("wallet_suspended", user_id=wallet.user_id)

        return await self.get_wallet(wallet_id=wallet_id)

    async def freeze_wallet(self, *, wallet_id: UUID, reason: str | None = None) -> dict[str, Any]:
        """Transition wallet to frozen state.

        Frozen wallets cannot perform any operations and may require
        compliance review before reactivation.

        Args:
            wallet_id: Unique wallet identifier.
            reason: Optional reason for freeze.

        Returns:
            Dictionary containing updated wallet details.

        Raises:
            ValidationException: If wallet not found or transition invalid.
            DatabaseException: If database operation fails.
        """
        self.logger.debug(
            "wallet_freezing",
            extra={"wallet_id": str(wallet_id), "reason": reason},
        )

        wallet = await self.wallet_repository.get_by_id_for_update(wallet_id)
        if not wallet:
            raise ValidationException(
                detail=f"Wallet {wallet_id} not found.",
                error_code="WALLET_NOT_FOUND",
            )

        if wallet.status not in self.STATE_TRANSITIONS.get(wallet.status, set()):
            if "frozen" not in self.STATE_TRANSITIONS.get(wallet.status, set()):
                raise WalletException(
                    detail=f"Cannot freeze wallet in {wallet.status} state.",
                    error_code="INVALID_STATE_TRANSITION",
                )

        wallet = await self.wallet_repository.freeze_wallet(wallet, reason=reason)

        self.logger.info(
            "wallet_frozen",
            extra={"wallet_id": str(wallet_id)},
        )
        await self._log_event("wallet_frozen", user_id=wallet.user_id, metadata={"reason": reason})

        return await self.get_wallet(wallet_id=wallet_id)

    async def unfreeze_wallet(self, *, wallet_id: UUID) -> dict[str, Any]:
        """Transition wallet from frozen state to active.

        Args:
            wallet_id: Unique wallet identifier.

        Returns:
            Dictionary containing updated wallet details.

        Raises:
            ValidationException: If wallet not found or not frozen.
            DatabaseException: If database operation fails.
        """
        self.logger.debug("wallet_unfreezing", extra={"wallet_id": str(wallet_id)})

        wallet = await self.wallet_repository.get_by_id_for_update(wallet_id)
        if not wallet:
            raise ValidationException(
                detail=f"Wallet {wallet_id} not found.",
                error_code="WALLET_NOT_FOUND",
            )

        if wallet.status != "frozen":
            raise WalletException(
                detail=f"Cannot unfreeze wallet in {wallet.status} state.",
                error_code="INVALID_STATE_TRANSITION",
            )

        wallet = await self.wallet_repository.update_wallet(
            wallet, status="active", is_frozen=False
        )

        self.logger.info(
            "wallet_unfrozen",
            extra={"wallet_id": str(wallet_id)},
        )
        await self._log_event("wallet_unfrozen", user_id=wallet.user_id)

        return await self.get_wallet(wallet_id=wallet_id)

    async def close_wallet(self, *, wallet_id: UUID) -> dict[str, Any]:
        """Transition wallet to closed state.

        Closed wallets cannot transact and are archived. They may be
        reopened if needed, but this is a significant action.

        Args:
            wallet_id: Unique wallet identifier.

        Returns:
            Dictionary containing updated wallet details.

        Raises:
            ValidationException: If wallet not found or transition invalid.
            DatabaseException: If database operation fails.
        """
        self.logger.debug("wallet_closure", extra={"wallet_id": str(wallet_id)})

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

        wallet = await self.wallet_repository.update_wallet(
            wallet, status="closed", is_active=False
        )

        self.logger.info(
            "wallet_closed",
            extra={"wallet_id": str(wallet_id)},
        )
        await self._log_event("wallet_closed", user_id=wallet.user_id)

        return await self.get_wallet(wallet_id=wallet_id)

    async def reopen_wallet(self, *, wallet_id: UUID) -> dict[str, Any]:
        """Transition wallet from closed state to active.

        Args:
            wallet_id: Unique wallet identifier.

        Returns:
            Dictionary containing updated wallet details.

        Raises:
            ValidationException: If wallet not found or not closed.
            DatabaseException: If database operation fails.
        """
        self.logger.debug("wallet_reopening", extra={"wallet_id": str(wallet_id)})

        wallet = await self.wallet_repository.get_by_id_for_update(wallet_id)
        if not wallet:
            raise ValidationException(
                detail=f"Wallet {wallet_id} not found.",
                error_code="WALLET_NOT_FOUND",
            )

        if wallet.status != "closed":
            raise WalletException(
                detail=f"Cannot reopen wallet in {wallet.status} state.",
                error_code="INVALID_STATE_TRANSITION",
            )

        wallet = await self.wallet_repository.update_wallet(
            wallet, status="active", is_active=True
        )

        self.logger.info(
            "wallet_reopened",
            extra={"wallet_id": str(wallet_id)},
        )
        await self._log_event("wallet_reopened", user_id=wallet.user_id)

        return await self.get_wallet(wallet_id=wallet_id)

    async def get_wallet(self, *, wallet_id: UUID, user_id: UUID | None = None) -> dict[str, Any]:
        """Retrieve wallet details by unique identifier.

        Args:
            wallet_id: Unique wallet identifier.
            user_id: Optional authenticated user used to enforce object ownership.

        Returns:
            Dictionary containing wallet details.

        Raises:
            ValidationException: If wallet not found.
        """
        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if not wallet:
            raise ValidationException(
                detail=f"Wallet {wallet_id} not found.",
                error_code="WALLET_NOT_FOUND",
            )
        if user_id is not None and wallet.user_id != user_id:
            raise ValidationException(
                detail="Wallet does not belong to the provided user.",
                error_code="WALLET_ACCESS_DENIED",
            )

        return self._serialize_wallet(wallet)

    async def get_wallet_by_user(
        self, *, user_id: UUID, wallet_type: str | None = None
    ) -> dict[str, Any]:
        """Retrieve wallet details by user identifier.

        Args:
            user_id: Unique user identifier.
            wallet_type: Optional wallet type filter.

        Returns:
            Dictionary containing wallet details.

        Raises:
            ValidationException: If wallet not found.
        """
        wallet = await self.wallet_repository.get_user_wallet(
            user_id=user_id, wallet_type=wallet_type
        )
        if not wallet:
            raise ValidationException(
                detail=f"No wallet found for user {user_id}.",
                error_code="WALLET_NOT_FOUND",
            )

        return self._serialize_wallet(wallet)

    async def wallet_exists(self, *, user_id: UUID, wallet_type: str | None = None) -> bool:
        """Check if a user has an existing wallet.

        Args:
            user_id: Unique user identifier.
            wallet_type: Optional wallet type filter.

        Returns:
            True if wallet exists, False otherwise.
        """
        wallet = await self.wallet_repository.get_user_wallet(
            user_id=user_id, wallet_type=wallet_type
        )
        return wallet is not None

    async def get_wallet_balance(self, *, wallet_id: UUID, user_id: UUID | None = None) -> dict[str, Any]:
        """Return a safe wallet balance summary without exposing sensitive ledger details."""
        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if not wallet:
            raise ValidationException(
                detail=f"Wallet {wallet_id} not found.",
                error_code="WALLET_NOT_FOUND",
            )
        if user_id is not None and wallet.user_id != user_id:
            raise ValidationException(
                detail="Wallet does not belong to the provided user.",
                error_code="WALLET_ACCESS_DENIED",
            )
        return {
            "wallet_id": str(wallet.id),
            "balance": {
                "available": str(wallet.available_balance),
                "ledger": str(wallet.ledger_balance),
                "locked": str(wallet.locked_balance),
            },
            "status": wallet.status,
        }

    async def validate_wallet_status(self, *, wallet_id: UUID, user_id: UUID | None = None) -> dict[str, Any]:
        """Return wallet status validation details without performing financial operations."""
        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if not wallet:
            raise ValidationException(
                detail=f"Wallet {wallet_id} not found.",
                error_code="WALLET_NOT_FOUND",
            )
        if user_id is not None and wallet.user_id != user_id:
            raise ValidationException(
                detail="Wallet does not belong to the provided user.",
                error_code="WALLET_ACCESS_DENIED",
            )
        return {
            "wallet_id": str(wallet.id),
            "is_active": wallet.is_active,
            "is_frozen": wallet.is_frozen,
            "is_suspended": wallet.is_suspended,
            "status": wallet.status,
            "valid": wallet.is_active and not wallet.is_frozen and not wallet.is_suspended,
        }

    async def validate_wallet_state(
        self, *, wallet_id: UUID, required_status: str | None = None, user_id: UUID | None = None
    ) -> dict[str, Any]:
        """Validate wallet state and optional status requirement.

        Args:
            wallet_id: Unique wallet identifier.
            required_status: Optional required status for validation.
            user_id: Optional authenticated user used to enforce object ownership.

        Returns:
            Dictionary containing wallet details.

        Raises:
            ValidationException: If wallet not found or status invalid.
        """
        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if not wallet:
            raise ValidationException(
                detail=f"Wallet {wallet_id} not found.",
                error_code="WALLET_NOT_FOUND",
            )
        if user_id is not None and wallet.user_id != user_id:
            raise ValidationException(
                detail="Wallet does not belong to the provided user.",
                error_code="WALLET_ACCESS_DENIED",
            )

        if required_status and wallet.status != required_status:
            raise WalletException(
                detail=f"Wallet status is {wallet.status}, expected {required_status}.",
                error_code="INVALID_WALLET_STATUS",
            )

        return self._serialize_wallet(wallet)

    async def update_wallet_status(
        self, *, wallet_id: UUID, new_status: str
    ) -> dict[str, Any]:
        """Update wallet status with state transition validation.

        Args:
            wallet_id: Unique wallet identifier.
            new_status: Target status.

        Returns:
            Dictionary containing updated wallet details.

        Raises:
            ValidationException: If wallet not found or status invalid.
            WalletException: If state transition is not allowed.
        """
        if new_status not in self.VALID_STATUSES:
            raise ValidationException(
                detail=f"Invalid wallet status: {new_status}",
                error_code="INVALID_STATUS",
            )

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

        allowed_transitions = self.STATE_TRANSITIONS[wallet.status]
        if new_status not in allowed_transitions:
            raise WalletException(
                detail=f"Cannot transition from {wallet.status} to {new_status}.",
                error_code="INVALID_STATE_TRANSITION",
            )

        wallet = await self.wallet_repository.update_wallet(wallet, status=new_status)

        self.logger.info(
            "wallet_status_updated",
            extra={
                "wallet_id": str(wallet_id),
                "previous_status": wallet.status,
                "new_status": new_status,
            },
        )

        return self._serialize_wallet(wallet)

    async def _activate_wallet_record(self, *, wallet_id: UUID) -> Wallet:
        self.logger.debug("wallet_activation", extra={"wallet_id": str(wallet_id)})

        wallet = await self.wallet_repository.get_by_id_for_update(wallet_id)
        if not wallet:
            raise ValidationException(
                detail=f"Wallet {wallet_id} not found.",
                error_code="WALLET_NOT_FOUND",
            )

        if wallet.status not in {"pending", "suspended", "frozen", "closed"}:
            raise WalletException(
                detail=f"Cannot activate wallet in {wallet.status} state.",
                error_code="INVALID_STATE_TRANSITION",
            )

        wallet = await self.wallet_repository.update_wallet(
            wallet, status="active", is_active=True, is_frozen=False, is_suspended=False
        )

        self.logger.info(
            "wallet_activated",
            extra={"wallet_id": str(wallet_id)},
        )
        await self._log_event("wallet_activated", user_id=wallet.user_id)

        return wallet

    def _sanitize_metadata(self, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValidationException("metadata_payload must be a string.")
        sanitized = value.strip()
        if len(sanitized) > 1000:
            raise ValidationException("metadata_payload is too long.")
        return sanitized or None

    async def _log_event(
        self,
        event_name: str,
        *,
        user_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.logger.info(
            "wallet_event",
            extra={"event": event_name, "user_id": str(user_id) if user_id else None, "metadata": metadata or {}},
        )
        audit_service = self.audit_service
        if audit_service is not None:
            try:
                await audit_service(event_name, user_id=user_id, metadata=metadata)
            except TypeError:
                audit_service(event_name, user_id=user_id, metadata=metadata)

    def _serialize_wallet(self, wallet: Wallet) -> dict[str, Any]:
        """Serialize a Wallet ORM model to a response dictionary.

        Args:
            wallet: Wallet ORM instance.

        Returns:
            Dictionary containing serialized wallet data.
        """
        return {
            "id": str(wallet.id),
            "user_id": str(wallet.user_id),
            "wallet_reference": wallet.wallet_reference,
            "wallet_type": wallet.wallet_type,
            "currency": wallet.currency,
            "status": wallet.status,
            "available_balance": float(wallet.available_balance),
            "ledger_balance": float(wallet.ledger_balance),
            "locked_balance": float(wallet.locked_balance),
            "is_active": wallet.is_active,
            "is_frozen": wallet.is_frozen,
            "is_suspended": wallet.is_suspended,
            "created_at": wallet.created_at.isoformat(),
            "updated_at": wallet.updated_at.isoformat(),
        }
