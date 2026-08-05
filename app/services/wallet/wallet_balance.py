"""Wallet balance management service.

This service handles enterprise wallet balance operations including credit,
debit, fund locking, and balance synchronization. It maintains consistency
between three independent balance types: available, locked, and ledger.
Every balance mutation is atomic and coordinated with the ledger service.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.wallet import Wallet
from app.repositories.wallet_repository import WalletRepository
from app.services.ledger_service import LedgerService
from app.utils.exceptions import (
    DatabaseException,
    ValidationException,
    WalletException,
)


class WalletBalanceService:
    """Manages wallet balance operations and maintains consistency.

    This service is responsible for:
    - Managing three independent balance types
    - Atomic credit and debit operations
    - Fund locking and unlocking
    - Balance validation and synchronization
    - Ledger coordination for audit trail
    - Financial operation integrity

    Balance Types:
    - Available Balance: Spendable funds
    - Locked Balance: Temporarily held funds
    - Ledger Balance: Total accounting balance

    Every balance mutation creates an immutable ledger entry.
    All operations are atomic and rollback on failure.
    """

    # Minimum transaction amount
    MIN_AMOUNT = Decimal("0.01")

    # Maximum transaction amount (configurable per deployment)
    MAX_AMOUNT = Decimal("999999999.99")

    def __init__(
        self,
        *,
        wallet_repository: WalletRepository,
        ledger_service: LedgerService,
        session: AsyncSession,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize balance service with required dependencies.

        Args:
            wallet_repository: Wallet persistence repository.
            ledger_service: Service for ledger entry creation.
            session: AsyncSession for database transactions.
            logger: Optional logger instance.
        """
        self.wallet_repository = wallet_repository
        self.ledger_service = ledger_service
        self.session = session
        self.logger = logger or logging.getLogger(__name__)

    async def get_balance(self, *, wallet_id: UUID) -> dict[str, Any]:
        """Retrieve all balance types for a wallet.

        Args:
            wallet_id: Unique wallet identifier.

        Returns:
            Dictionary containing all balance types.

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
            "available_balance": float(wallet.available_balance),
            "locked_balance": float(wallet.locked_balance),
            "ledger_balance": float(wallet.ledger_balance),
            "total_balance": float(
                wallet.available_balance + wallet.locked_balance
            ),
            "currency": wallet.currency,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    async def get_available_balance(self, *, wallet_id: UUID) -> Decimal:
        """Retrieve available (spendable) balance.

        Args:
            wallet_id: Unique wallet identifier.

        Returns:
            Available balance as Decimal.

        Raises:
            ValidationException: If wallet not found.
        """
        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if not wallet:
            raise ValidationException(
                detail=f"Wallet {wallet_id} not found.",
                error_code="WALLET_NOT_FOUND",
            )

        return wallet.available_balance

    async def get_locked_balance(self, *, wallet_id: UUID) -> Decimal:
        """Retrieve locked balance.

        Args:
            wallet_id: Unique wallet identifier.

        Returns:
            Locked balance as Decimal.

        Raises:
            ValidationException: If wallet not found.
        """
        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if not wallet:
            raise ValidationException(
                detail=f"Wallet {wallet_id} not found.",
                error_code="WALLET_NOT_FOUND",
            )

        return wallet.locked_balance

    async def get_ledger_balance(self, *, wallet_id: UUID) -> Decimal:
        """Retrieve ledger (accounting) balance.

        Args:
            wallet_id: Unique wallet identifier.

        Returns:
            Ledger balance as Decimal.

        Raises:
            ValidationException: If wallet not found.
        """
        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if not wallet:
            raise ValidationException(
                detail=f"Wallet {wallet_id} not found.",
                error_code="WALLET_NOT_FOUND",
            )

        return wallet.ledger_balance

    async def credit_wallet(
        self,
        *,
        wallet_id: UUID,
        amount: Decimal | float | int,
        transaction_reference: str,
        transaction_type: str = "CREDIT",
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Credit wallet with atomic transaction guarantee.

        This method:
        1. Validates wallet and amount
        2. Updates available and ledger balances
        3. Creates immutable ledger entry
        4. Commits atomically or rolls back on failure

        Args:
            wallet_id: Unique wallet identifier.
            amount: Credit amount.
            transaction_reference: Unique transaction reference.
            transaction_type: Type of credit (default: "CREDIT").
            description: Optional operation description.
            metadata: Optional metadata dictionary.

        Returns:
            Dictionary containing updated balance state.

        Raises:
            ValidationException: If validation fails.
            WalletException: If wallet state invalid.
            DatabaseException: If operation fails.
        """
        self.logger.debug(
            "wallet_credit_started",
            extra={
                "wallet_id": str(wallet_id),
                "amount": float(amount),
                "reference": transaction_reference,
            },
        )

        try:
            # Convert and validate amount
            amount_decimal = Decimal(str(amount))
            await self.validate_balance_operation(
                wallet_id=wallet_id, amount=amount_decimal
            )

            # Acquire row lock for financial operation
            wallet = await self.wallet_repository.get_by_id_for_update(wallet_id)
            if not wallet:
                raise ValidationException(
                    detail=f"Wallet {wallet_id} not found.",
                    error_code="WALLET_NOT_FOUND",
                )

            self._validate_wallet_active(wallet)

            # Calculate new balances
            opening_balance = wallet.available_balance
            new_available = wallet.available_balance + amount_decimal
            new_ledger = wallet.ledger_balance + amount_decimal

            # Update wallet balances
            wallet = await self.wallet_repository.update_balance_fields(
                wallet,
                available_balance=new_available,
                ledger_balance=new_ledger,
            )

            # Create ledger entry
            ledger_entry = await self.ledger_service.create_ledger_entry(
                wallet_id=wallet_id,
                user_id=wallet.user_id,
                transaction_reference=transaction_reference,
                transaction_type=transaction_type,
                entry_type="CREDIT",
                opening_balance=opening_balance,
                credit_amount=amount_decimal,
                debit_amount=Decimal("0"),
                closing_balance=new_available,
                status="POSTED",
                currency=wallet.currency,
                description=description,
                metadata=metadata,
            )

            # Commit transaction
            await self.session.commit()

            self.logger.info(
                "wallet_credited",
                extra={
                    "wallet_id": str(wallet_id),
                    "amount": float(amount_decimal),
                    "new_balance": float(new_available),
                },
            )

            return {
                "wallet_id": str(wallet_id),
                "transaction_reference": transaction_reference,
                "previous_balance": float(opening_balance),
                "amount": float(amount_decimal),
                "new_available_balance": float(new_available),
                "new_ledger_balance": float(new_ledger),
                "ledger_entry_id": str(ledger_entry.ledger_id),
                "status": "success",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except (ValidationException, WalletException, DatabaseException):
            await self.session.rollback()
            raise
        except Exception as e:
            await self.session.rollback()
            self.logger.error(
                "wallet_credit_failed",
                extra={
                    "wallet_id": str(wallet_id),
                    "amount": float(amount),
                    "error": str(e),
                },
            )
            raise DatabaseException(
                detail="Wallet credit failed. Please try again.",
                error_code="BALANCE_CREDIT_ERROR",
            )

    async def debit_wallet(
        self,
        *,
        wallet_id: UUID,
        amount: Decimal | float | int,
        transaction_reference: str,
        transaction_type: str = "DEBIT",
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Debit wallet with sufficient balance validation.

        This method:
        1. Validates wallet and amount
        2. Checks sufficient available balance
        3. Updates balances atomically
        4. Creates immutable ledger entry
        5. Commits or rolls back

        Args:
            wallet_id: Unique wallet identifier.
            amount: Debit amount.
            transaction_reference: Unique transaction reference.
            transaction_type: Type of debit (default: "DEBIT").
            description: Optional operation description.
            metadata: Optional metadata dictionary.

        Returns:
            Dictionary containing updated balance state.

        Raises:
            ValidationException: If validation fails.
            WalletException: If insufficient balance.
            DatabaseException: If operation fails.
        """
        self.logger.debug(
            "wallet_debit_started",
            extra={
                "wallet_id": str(wallet_id),
                "amount": float(amount),
                "reference": transaction_reference,
            },
        )

        try:
            # Convert and validate amount
            amount_decimal = Decimal(str(amount))
            await self.validate_balance_operation(
                wallet_id=wallet_id, amount=amount_decimal
            )

            # Acquire row lock for financial operation
            wallet = await self.wallet_repository.get_by_id_for_update(wallet_id)
            if not wallet:
                raise ValidationException(
                    detail=f"Wallet {wallet_id} not found.",
                    error_code="WALLET_NOT_FOUND",
                )

            self._validate_wallet_active(wallet)

            # Check sufficient balance
            if wallet.available_balance < amount_decimal:
                raise WalletException(
                    detail=f"Insufficient balance. Available: {wallet.available_balance}, Required: {amount_decimal}",
                    error_code="INSUFFICIENT_BALANCE",
                )

            # Calculate new balances
            opening_balance = wallet.available_balance
            new_available = wallet.available_balance - amount_decimal
            new_ledger = wallet.ledger_balance - amount_decimal

            # Update wallet balances
            wallet = await self.wallet_repository.update_balance_fields(
                wallet,
                available_balance=new_available,
                ledger_balance=new_ledger,
            )

            # Create ledger entry
            ledger_entry = await self.ledger_service.create_ledger_entry(
                wallet_id=wallet_id,
                user_id=wallet.user_id,
                transaction_reference=transaction_reference,
                transaction_type=transaction_type,
                entry_type="DEBIT",
                opening_balance=opening_balance,
                credit_amount=Decimal("0"),
                debit_amount=amount_decimal,
                closing_balance=new_available,
                status="POSTED",
                currency=wallet.currency,
                description=description,
                metadata=metadata,
            )

            # Commit transaction
            await self.session.commit()

            self.logger.info(
                "wallet_debited",
                extra={
                    "wallet_id": str(wallet_id),
                    "amount": float(amount_decimal),
                    "new_balance": float(new_available),
                },
            )

            return {
                "wallet_id": str(wallet_id),
                "transaction_reference": transaction_reference,
                "previous_balance": float(opening_balance),
                "amount": float(amount_decimal),
                "new_available_balance": float(new_available),
                "new_ledger_balance": float(new_ledger),
                "ledger_entry_id": str(ledger_entry.ledger_id),
                "status": "success",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except (ValidationException, WalletException, DatabaseException):
            await self.session.rollback()
            raise
        except Exception as e:
            await self.session.rollback()
            self.logger.error(
                "wallet_debit_failed",
                extra={
                    "wallet_id": str(wallet_id),
                    "amount": float(amount),
                    "error": str(e),
                },
            )
            raise DatabaseException(
                detail="Wallet debit failed. Please try again.",
                error_code="BALANCE_DEBIT_ERROR",
            )

    async def lock_funds(
        self,
        *,
        wallet_id: UUID,
        amount: Decimal | float | int,
        transaction_reference: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Lock funds by moving from available to locked balance.

        Total balance remains unchanged. Locked funds cannot be spent.

        Args:
            wallet_id: Unique wallet identifier.
            amount: Amount to lock.
            transaction_reference: Unique transaction reference.
            description: Optional operation description.

        Returns:
            Dictionary containing updated balance state.

        Raises:
            ValidationException: If validation fails.
            WalletException: If insufficient available balance.
            DatabaseException: If operation fails.
        """
        self.logger.debug(
            "wallet_lock_started",
            extra={
                "wallet_id": str(wallet_id),
                "amount": float(amount),
            },
        )

        try:
            # Convert and validate amount
            amount_decimal = Decimal(str(amount))
            await self.validate_balance_operation(
                wallet_id=wallet_id, amount=amount_decimal
            )

            # Acquire row lock
            wallet = await self.wallet_repository.get_by_id_for_update(wallet_id)
            if not wallet:
                raise ValidationException(
                    detail=f"Wallet {wallet_id} not found.",
                    error_code="WALLET_NOT_FOUND",
                )

            self._validate_wallet_active(wallet)

            # Check sufficient available balance
            if wallet.available_balance < amount_decimal:
                raise WalletException(
                    detail=f"Insufficient available balance to lock. Available: {wallet.available_balance}, Required: {amount_decimal}",
                    error_code="INSUFFICIENT_BALANCE",
                )

            # Calculate new balances
            new_available = wallet.available_balance - amount_decimal
            new_locked = wallet.locked_balance + amount_decimal

            # Update wallet
            wallet = await self.wallet_repository.update_balance_fields(
                wallet,
                available_balance=new_available,
                locked_balance=new_locked,
            )

            # Commit (no ledger entry for lock operations)
            await self.session.commit()

            self.logger.info(
                "wallet_funds_locked",
                extra={
                    "wallet_id": str(wallet_id),
                    "amount": float(amount_decimal),
                    "new_available": float(new_available),
                    "new_locked": float(new_locked),
                },
            )

            return {
                "wallet_id": str(wallet_id),
                "transaction_reference": transaction_reference,
                "amount_locked": float(amount_decimal),
                "new_available_balance": float(new_available),
                "new_locked_balance": float(new_locked),
                "ledger_balance": float(wallet.ledger_balance),
                "status": "success",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except (ValidationException, WalletException, DatabaseException):
            await self.session.rollback()
            raise
        except Exception as e:
            await self.session.rollback()
            self.logger.error(
                "wallet_lock_failed",
                extra={
                    "wallet_id": str(wallet_id),
                    "amount": float(amount),
                    "error": str(e),
                },
            )
            raise DatabaseException(
                detail="Fund locking failed. Please try again.",
                error_code="LOCK_FUNDS_ERROR",
            )

    async def unlock_funds(
        self,
        *,
        wallet_id: UUID,
        amount: Decimal | float | int,
        transaction_reference: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Unlock funds by moving from locked to available balance.

        Total balance remains unchanged. Unlocked funds become spendable.

        Args:
            wallet_id: Unique wallet identifier.
            amount: Amount to unlock.
            transaction_reference: Unique transaction reference.
            description: Optional operation description.

        Returns:
            Dictionary containing updated balance state.

        Raises:
            ValidationException: If validation fails.
            WalletException: If insufficient locked balance.
            DatabaseException: If operation fails.
        """
        self.logger.debug(
            "wallet_unlock_started",
            extra={
                "wallet_id": str(wallet_id),
                "amount": float(amount),
            },
        )

        try:
            # Convert and validate amount
            amount_decimal = Decimal(str(amount))
            await self.validate_balance_operation(
                wallet_id=wallet_id, amount=amount_decimal
            )

            # Acquire row lock
            wallet = await self.wallet_repository.get_by_id_for_update(wallet_id)
            if not wallet:
                raise ValidationException(
                    detail=f"Wallet {wallet_id} not found.",
                    error_code="WALLET_NOT_FOUND",
                )

            # Check sufficient locked balance
            if wallet.locked_balance < amount_decimal:
                raise WalletException(
                    detail=f"Insufficient locked balance to unlock. Locked: {wallet.locked_balance}, Required: {amount_decimal}",
                    error_code="INSUFFICIENT_LOCKED_BALANCE",
                )

            # Calculate new balances
            new_available = wallet.available_balance + amount_decimal
            new_locked = wallet.locked_balance - amount_decimal

            # Update wallet
            wallet = await self.wallet_repository.update_balance_fields(
                wallet,
                available_balance=new_available,
                locked_balance=new_locked,
            )

            # Commit (no ledger entry for unlock operations)
            await self.session.commit()

            self.logger.info(
                "wallet_funds_unlocked",
                extra={
                    "wallet_id": str(wallet_id),
                    "amount": float(amount_decimal),
                    "new_available": float(new_available),
                    "new_locked": float(new_locked),
                },
            )

            return {
                "wallet_id": str(wallet_id),
                "transaction_reference": transaction_reference,
                "amount_unlocked": float(amount_decimal),
                "new_available_balance": float(new_available),
                "new_locked_balance": float(new_locked),
                "ledger_balance": float(wallet.ledger_balance),
                "status": "success",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except (ValidationException, WalletException, DatabaseException):
            await self.session.rollback()
            raise
        except Exception as e:
            await self.session.rollback()
            self.logger.error(
                "wallet_unlock_failed",
                extra={
                    "wallet_id": str(wallet_id),
                    "amount": float(amount),
                    "error": str(e),
                },
            )
            raise DatabaseException(
                detail="Fund unlocking failed. Please try again.",
                error_code="UNLOCK_FUNDS_ERROR",
            )

    async def reserve_funds(
        self,
        *,
        wallet_id: UUID,
        amount: Decimal | float | int,
        transaction_reference: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Reserve funds (same as lock_funds for semantic clarity).

        Reserves funds for pending operations. Alias for lock_funds.

        Args:
            wallet_id: Unique wallet identifier.
            amount: Amount to reserve.
            transaction_reference: Unique transaction reference.
            description: Optional operation description.

        Returns:
            Dictionary containing updated balance state.
        """
        return await self.lock_funds(
            wallet_id=wallet_id,
            amount=amount,
            transaction_reference=transaction_reference,
            description=description,
        )

    async def release_reserved_funds(
        self,
        *,
        wallet_id: UUID,
        amount: Decimal | float | int,
        transaction_reference: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Release reserved funds (same as unlock_funds for semantic clarity).

        Releases funds from reserve. Alias for unlock_funds.

        Args:
            wallet_id: Unique wallet identifier.
            amount: Amount to release.
            transaction_reference: Unique transaction reference.
            description: Optional operation description.

        Returns:
            Dictionary containing updated balance state.
        """
        return await self.unlock_funds(
            wallet_id=wallet_id,
            amount=amount,
            transaction_reference=transaction_reference,
            description=description,
        )

    async def has_sufficient_balance(
        self,
        *,
        wallet_id: UUID,
        amount: Decimal | float | int,
        include_locked: bool = False,
    ) -> bool:
        """Check if wallet has sufficient balance for operation.

        Args:
            wallet_id: Unique wallet identifier.
            amount: Amount to check.
            include_locked: Whether to include locked balance (default: False).

        Returns:
            True if sufficient balance, False otherwise.

        Raises:
            ValidationException: If wallet not found.
        """
        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if not wallet:
            raise ValidationException(
                detail=f"Wallet {wallet_id} not found.",
                error_code="WALLET_NOT_FOUND",
            )

        amount_decimal = Decimal(str(amount))
        available = wallet.available_balance

        if include_locked:
            available += wallet.locked_balance

        return available >= amount_decimal

    async def validate_balance_operation(
        self,
        *,
        wallet_id: UUID,
        amount: Decimal | float | int,
    ) -> None:
        """Validate balance operation parameters.

        Args:
            wallet_id: Unique wallet identifier.
            amount: Operation amount.

        Raises:
            ValidationException: If validation fails.
        """
        # Validate amount
        amount_decimal = Decimal(str(amount))

        if amount_decimal <= 0:
            raise ValidationException(
                detail="Amount must be greater than zero.",
                error_code="INVALID_AMOUNT",
            )

        if amount_decimal < self.MIN_AMOUNT:
            raise ValidationException(
                detail=f"Amount below minimum: {self.MIN_AMOUNT}",
                error_code="AMOUNT_BELOW_MINIMUM",
            )

        if amount_decimal > self.MAX_AMOUNT:
            raise ValidationException(
                detail=f"Amount exceeds maximum: {self.MAX_AMOUNT}",
                error_code="AMOUNT_EXCEEDS_MAXIMUM",
            )

        # Validate decimal precision (max 2 decimal places)
        if amount_decimal.as_tuple().exponent < -2:
            raise ValidationException(
                detail="Amount must have at most 2 decimal places.",
                error_code="INVALID_DECIMAL_PRECISION",
            )

    async def recalculate_balance(self, *, wallet_id: UUID) -> dict[str, Any]:
        """Recalculate wallet balance from ledger entries.

        Ensures wallet balance matches accounting records. This is a
        reconciliation operation that should rarely be needed.

        Args:
            wallet_id: Unique wallet identifier.

        Returns:
            Dictionary containing recalculated balances.

        Raises:
            ValidationException: If wallet not found.
            DatabaseException: If operation fails.
        """
        self.logger.debug(
            "wallet_balance_recalculation",
            extra={"wallet_id": str(wallet_id)},
        )

        try:
            wallet = await self.wallet_repository.get_by_id_for_update(wallet_id)
            if not wallet:
                raise ValidationException(
                    detail=f"Wallet {wallet_id} not found.",
                    error_code="WALLET_NOT_FOUND",
                )

            # Retrieve ledger entries for wallet
            ledger_summary = (
                await self.ledger_service.get_wallet_balance_summary(
                    wallet_id=wallet_id
                )
            )

            # Update wallet with recalculated balance
            new_ledger_balance = Decimal(
                str(ledger_summary.get("total_credits", 0))
            ) - Decimal(str(ledger_summary.get("total_debits", 0)))

            wallet = await self.wallet_repository.update_balance_fields(
                wallet, ledger_balance=new_ledger_balance
            )

            await self.session.commit()

            self.logger.info(
                "wallet_balance_recalculated",
                extra={
                    "wallet_id": str(wallet_id),
                    "new_ledger_balance": float(new_ledger_balance),
                },
            )

            return {
                "wallet_id": str(wallet_id),
                "available_balance": float(wallet.available_balance),
                "locked_balance": float(wallet.locked_balance),
                "ledger_balance": float(wallet.ledger_balance),
                "status": "recalculated",
            }

        except (ValidationException, DatabaseException):
            await self.session.rollback()
            raise
        except Exception as e:
            await self.session.rollback()
            self.logger.error(
                "balance_recalculation_failed",
                extra={"wallet_id": str(wallet_id), "error": str(e)},
            )
            raise DatabaseException(
                detail="Balance recalculation failed.",
                error_code="BALANCE_RECALCULATION_ERROR",
            )

    async def synchronize_balance(self, *, wallet_id: UUID) -> dict[str, Any]:
        """Synchronize wallet balance with ledger entries.

        Ensures wallet balances are consistent with ledger. This is a
        reconciliation operation for audit and compliance.

        Args:
            wallet_id: Unique wallet identifier.

        Returns:
            Dictionary containing synchronized balance state.

        Raises:
            ValidationException: If wallet not found.
            DatabaseException: If operation fails.
        """
        self.logger.debug(
            "wallet_balance_synchronization",
            extra={"wallet_id": str(wallet_id)},
        )

        try:
            wallet = await self.wallet_repository.get_by_id_for_update(wallet_id)
            if not wallet:
                raise ValidationException(
                    detail=f"Wallet {wallet_id} not found.",
                    error_code="WALLET_NOT_FOUND",
                )

            # Get ledger summary
            ledger_summary = (
                await self.ledger_service.get_wallet_balance_summary(
                    wallet_id=wallet_id
                )
            )

            # Verify consistency
            expected_ledger = Decimal(
                str(ledger_summary.get("total_credits", 0))
            ) - Decimal(str(ledger_summary.get("total_debits", 0)))

            if wallet.ledger_balance != expected_ledger:
                self.logger.warning(
                    "balance_mismatch_detected",
                    extra={
                        "wallet_id": str(wallet_id),
                        "current_ledger": float(wallet.ledger_balance),
                        "expected_ledger": float(expected_ledger),
                    },
                )

                # Update to expected value
                wallet = await self.wallet_repository.update_balance_fields(
                    wallet, ledger_balance=expected_ledger
                )

            await self.session.commit()

            self.logger.info(
                "wallet_balance_synchronized",
                extra={"wallet_id": str(wallet_id)},
            )

            return {
                "wallet_id": str(wallet_id),
                "available_balance": float(wallet.available_balance),
                "locked_balance": float(wallet.locked_balance),
                "ledger_balance": float(wallet.ledger_balance),
                "synchronized": True,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except (ValidationException, DatabaseException):
            await self.session.rollback()
            raise
        except Exception as e:
            await self.session.rollback()
            self.logger.error(
                "balance_synchronization_failed",
                extra={"wallet_id": str(wallet_id), "error": str(e)},
            )
            raise DatabaseException(
                detail="Balance synchronization failed.",
                error_code="BALANCE_SYNC_ERROR",
            )

    async def get_balance_summary(self, *, wallet_id: UUID) -> dict[str, Any]:
        """Get comprehensive balance summary with metadata.

        Args:
            wallet_id: Unique wallet identifier.

        Returns:
            Dictionary containing complete balance information.

        Raises:
            ValidationException: If wallet not found.
        """
        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if not wallet:
            raise ValidationException(
                detail=f"Wallet {wallet_id} not found.",
                error_code="WALLET_NOT_FOUND",
            )

        total_balance = wallet.available_balance + wallet.locked_balance

        return {
            "wallet_id": str(wallet_id),
            "user_id": str(wallet.user_id),
            "wallet_type": wallet.wallet_type,
            "currency": wallet.currency,
            "status": wallet.status,
            "balances": {
                "available": float(wallet.available_balance),
                "locked": float(wallet.locked_balance),
                "ledger": float(wallet.ledger_balance),
                "total": float(total_balance),
            },
            "flags": {
                "is_active": wallet.is_active,
                "is_frozen": wallet.is_frozen,
                "is_suspended": wallet.is_suspended,
            },
            "timestamps": {
                "created_at": wallet.created_at.isoformat(),
                "updated_at": wallet.updated_at.isoformat(),
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
            },
        }

    def _validate_wallet_active(self, wallet: Wallet) -> None:
        """Validate wallet is in active state for financial operations.

        Args:
            wallet: Wallet instance.

        Raises:
            WalletException: If wallet is not active.
        """
        if wallet.is_frozen:
            raise WalletException(
                detail="Wallet is frozen and cannot perform operations.",
                error_code="WALLET_FROZEN",
            )

        if wallet.is_suspended:
            raise WalletException(
                detail="Wallet is suspended and cannot perform operations.",
                error_code="WALLET_SUSPENDED",
            )

        if not wallet.is_active:
            raise WalletException(
                detail="Wallet is not active.",
                error_code="WALLET_INACTIVE",
            )
