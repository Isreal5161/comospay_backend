"""Wallet transaction PIN management service.

Handles PIN creation, verification, and lifecycle management with configurable
policies, failed attempt tracking, and lockout mechanisms.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.security import hash_pin, validate_pin, verify_pin
from app.config.settings import settings
from app.models.wallet import Wallet
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.utils.exceptions import AuthenticationException, DatabaseException, ValidationException, WalletException


class WalletPinService:
    """Wallet transaction PIN lifecycle and security management."""

    # Default PIN policy constants (overridable via settings)
    DEFAULT_MIN_LENGTH = 4
    DEFAULT_MAX_LENGTH = 6
    DEFAULT_MAX_ATTEMPTS = 3
    DEFAULT_LOCK_DURATION_MINUTES = 15

    # Common PINs to prevent usage (blacklist)
    BLACKLISTED_PINS = {
        "0000",
        "1111",
        "2222",
        "3333",
        "4444",
        "5555",
        "6666",
        "7777",
        "8888",
        "9999",
        "1234",
        "4321",
        "0123",
        "1357",
        "2468",
    }

    def __init__(
        self,
        *,
        user_repository: UserRepository,
        wallet_repository: WalletRepository,
        session: AsyncSession | None = None,
        logger: logging.Logger | None = None,
        audit_service: Any | None = None,
        max_failed_attempts: int | None = None,
        pin_length: int | None = None,
    ) -> None:
        """Initialize PIN service with required dependencies.

        Args:
            user_repository: User repository instance.
            wallet_repository: Wallet repository instance.
            session: AsyncSession for transactions. Optional.
            logger: Logger instance. Defaults to module logger.
            audit_service: Optional audit logging service.
            max_failed_attempts: Max attempts before lockout. Defaults to 3.
            pin_length: PIN length requirement. Defaults from settings.

        Raises:
            RuntimeError: If repositories not configured when needed.
        """
        self.user_repository = user_repository
        self.wallet_repository = wallet_repository
        self.session = session
        self.logger = logger or logging.getLogger(__name__)
        self.audit_service = audit_service
        self.max_failed_attempts = max_failed_attempts or settings.max_login_attempts
        self.pin_length = pin_length or settings.pin_length

    async def create_transaction_pin(
        self,
        *,
        user_id: UUID,
        pin: str,
        confirm_pin: str,
        require_complexity: bool = False,
    ) -> dict[str, Any]:
        """Create a transaction PIN for a user wallet.

        Args:
            user_id: User identifier.
            pin: PIN value.
            confirm_pin: PIN confirmation for validation.
            require_complexity: Enforce additional complexity. Defaults to False.

        Returns:
            Dictionary with success message.

        Raises:
            ValidationException: If validation fails.
            WalletException: If PIN already exists.
            DatabaseException: If creation fails.
        """
        self._require_repository(self.user_repository)
        self._require_repository(self.wallet_repository)

        self.logger.debug(
            "pin_creation_started",
            extra={"user_id": str(user_id)},
        )

        user = await self.user_repository.get_by_id(user_id)
        if not user:
            raise ValidationException(
                detail="User not found.",
                error_code="USER_NOT_FOUND",
            )

        wallet = await self._get_wallet_for_user(user_id)
        if wallet.transaction_pin_reference:
            raise WalletException(
                detail="Transaction PIN already exists.",
                error_code="PIN_ALREADY_EXISTS",
            )

        await self.validate_pin_strength(pin, require_complexity=require_complexity)
        if pin != confirm_pin:
            raise ValidationException(
                detail="PIN confirmation does not match.",
                error_code="PIN_MISMATCH",
            )

        try:
            async with self._session_scope():
                wallet = await self._get_wallet_for_user(user_id, lock=True)
                if wallet.transaction_pin_reference:
                    raise WalletException(
                        detail="Transaction PIN already exists.",
                        error_code="PIN_ALREADY_EXISTS",
                    )
                wallet.transaction_pin_reference = hash_pin(pin)
                await self.wallet_repository.update_wallet(wallet, transaction_pin_reference=wallet.transaction_pin_reference)
                await self._reset_pin_state(wallet)
                await self._log_event("transaction_pin_created", user_id=user_id)

                self.logger.info(
                    "pin_created",
                    extra={"user_id": str(user_id)},
                )

                return {"message": "Transaction PIN created successfully."}
        except ValidationException:
            raise
        except WalletException:
            raise
        except Exception as exc:
            self.logger.error(
                "pin_creation_failed",
                extra={"user_id": str(user_id), "error": str(exc)},
            )
            raise DatabaseException(
                detail="Transaction PIN creation failed. Please try again.",
                error_code="PIN_CREATION_ERROR",
            ) from exc

    async def change_transaction_pin(
        self,
        *,
        user_id: UUID,
        current_pin: str,
        new_pin: str,
        confirm_pin: str,
        require_complexity: bool = False,
    ) -> dict[str, Any]:
        """Change an existing transaction PIN after validating the current one.

        Args:
            user_id: User identifier.
            current_pin: Current PIN for verification.
            new_pin: New PIN to set.
            confirm_pin: Confirmation of new PIN.
            require_complexity: Enforce additional complexity. Defaults to False.

        Returns:
            Dictionary with success message.

        Raises:
            ValidationException: If validation fails.
            AuthenticationException: If current PIN invalid.
            WalletException: If no PIN is set.
            DatabaseException: If operation fails.
        """
        self._require_repository(self.user_repository)
        self._require_repository(self.wallet_repository)

        self.logger.debug(
            "pin_change_started",
            extra={"user_id": str(user_id)},
        )

        user = await self.user_repository.get_by_id(user_id)
        if not user:
            raise ValidationException(
                detail="User not found.",
                error_code="USER_NOT_FOUND",
            )

        await self.validate_pin_strength(new_pin, require_complexity=require_complexity)
        if new_pin != confirm_pin:
            raise ValidationException(
                detail="PIN confirmation does not match.",
                error_code="PIN_MISMATCH",
            )

        wallet = await self._get_wallet_for_user(user_id)
        if not wallet.transaction_pin_reference:
            raise WalletException(
                detail="Transaction PIN is not set.",
                error_code="PIN_NOT_SET",
            )

        if not await self.verify_transaction_pin(user_id=user_id, pin=current_pin):
            raise AuthenticationException(
                detail="Current transaction PIN is invalid.",
                error_code="INVALID_PIN",
            )

        try:
            async with self._session_scope():
                wallet = await self._get_wallet_for_user(user_id, lock=True)
                if not wallet.transaction_pin_reference:
                    raise WalletException(
                        detail="Transaction PIN is not set.",
                        error_code="PIN_NOT_SET",
                    )
                wallet.transaction_pin_reference = hash_pin(new_pin)
                await self.wallet_repository.update_wallet(wallet, transaction_pin_reference=wallet.transaction_pin_reference)
                await self._reset_pin_state(wallet)
                await self._log_event("transaction_pin_changed", user_id=user_id)

                self.logger.info(
                    "pin_changed",
                    extra={"user_id": str(user_id)},
                )

                return {"message": "Transaction PIN changed successfully."}
        except AuthenticationException:
            raise
        except ValidationException:
            raise
        except WalletException:
            raise
        except Exception as exc:
            self.logger.error(
                "pin_change_failed",
                extra={"user_id": str(user_id), "error": str(exc)},
            )
            raise DatabaseException(
                detail="Transaction PIN change failed. Please try again.",
                error_code="PIN_CHANGE_ERROR",
            ) from exc

    async def reset_transaction_pin(
        self,
        *,
        user_id: UUID,
        new_pin: str,
        confirm_pin: str,
        require_complexity: bool = False,
    ) -> dict[str, Any]:
        """Reset the transaction PIN for a user wallet.

        Bypasses PIN verification and should only be called after successful
        alternative verification (OTP, email, BVN, admin approval, etc).

        Args:
            user_id: User identifier.
            new_pin: New PIN to set.
            confirm_pin: Confirmation of new PIN.
            require_complexity: Enforce additional complexity. Defaults to False.

        Returns:
            Dictionary with success message.

        Raises:
            ValidationException: If validation fails.
            DatabaseException: If operation fails.
        """
        self._require_repository(self.user_repository)
        self._require_repository(self.wallet_repository)

        self.logger.debug(
            "pin_reset_started",
            extra={"user_id": str(user_id)},
        )

        user = await self.user_repository.get_by_id(user_id)
        if not user:
            raise ValidationException(
                detail="User not found.",
                error_code="USER_NOT_FOUND",
            )

        await self.validate_pin_strength(new_pin, require_complexity=require_complexity)
        if new_pin != confirm_pin:
            raise ValidationException(
                detail="PIN confirmation does not match.",
                error_code="PIN_MISMATCH",
            )

        try:
            async with self._session_scope():
                wallet = await self._get_wallet_for_user(user_id, lock=True)
                wallet.transaction_pin_reference = hash_pin(new_pin)
                await self.wallet_repository.update_wallet(wallet, transaction_pin_reference=wallet.transaction_pin_reference)
                await self._reset_pin_state(wallet)
                await self._log_event("transaction_pin_reset", user_id=user_id)

                self.logger.info(
                    "pin_reset",
                    extra={"user_id": str(user_id)},
                )

                return {"message": "Transaction PIN reset successfully."}
        except ValidationException:
            raise
        except Exception as exc:
            self.logger.error(
                "pin_reset_failed",
                extra={"user_id": str(user_id), "error": str(exc)},
            )
            raise DatabaseException(
                detail="Transaction PIN reset failed. Please try again.",
                error_code="PIN_RESET_ERROR",
            ) from exc

    async def verify_transaction_pin(self, *, user_id: UUID, pin: str) -> bool:
        """Verify a transaction PIN.

        Args:
            user_id: User identifier.
            pin: PIN to verify.

        Returns:
            True if PIN is correct, False otherwise.

        Raises:
            ValidationException: If wallet not found or PIN not set.
            WalletException: If PIN is locked.
            DatabaseException: If operation fails.
        """
        self._require_repository(self.wallet_repository)

        self.logger.debug(
            "pin_verification_started",
            extra={"user_id": str(user_id)},
        )

        wallet = await self._get_wallet_for_user(user_id)
        if not wallet.transaction_pin_reference:
            raise ValidationException(
                detail="Transaction PIN is not set.",
                error_code="PIN_NOT_SET",
            )

        if self._is_pin_locked(wallet):
            raise WalletException(
                detail="Transaction PIN is locked. Please try again later.",
                error_code="PIN_LOCKED",
            )

        if not pin:
            raise ValidationException(
                detail="Transaction PIN is required.",
                error_code="PIN_REQUIRED",
            )

        is_valid = verify_pin(pin, wallet.transaction_pin_reference)
        if not is_valid:
            await self.increment_failed_attempts(user_id=user_id)
            self.logger.warning(
                "pin_verification_failed",
                extra={"user_id": str(user_id)},
            )
        else:
            await self.reset_failed_attempts(user_id=user_id)
            self.logger.debug(
                "pin_verified_success",
                extra={"user_id": str(user_id)},
            )

        return is_valid

    async def remove_transaction_pin(self, *, user_id: UUID, pin: str | None = None) -> dict[str, Any]:
        """Remove a transaction PIN from a wallet.

        If PIN is supplied, verifies the PIN before removal. If not supplied,
        removes without verification (admin only).

        Args:
            user_id: User identifier.
            pin: PIN for verification. Optional.

        Returns:
            Dictionary with success message.

        Raises:
            ValidationException: If wallet not found.
            AuthenticationException: If PIN supplied but invalid.
            WalletException: If no PIN is set.
            DatabaseException: If operation fails.
        """
        self._require_repository(self.wallet_repository)

        self.logger.debug(
            "pin_removal_started",
            extra={"user_id": str(user_id)},
        )

        wallet = await self._get_wallet_for_user(user_id)
        if not wallet.transaction_pin_reference:
            raise WalletException(
                detail="Transaction PIN is not set.",
                error_code="PIN_NOT_SET",
            )

        if pin is not None and not await self.verify_transaction_pin(user_id=user_id, pin=pin):
            raise AuthenticationException(
                detail="Current transaction PIN is invalid.",
                error_code="INVALID_PIN",
            )

        try:
            async with self._session_scope():
                wallet = await self._get_wallet_for_user(user_id, lock=True)
                if not wallet.transaction_pin_reference:
                    raise WalletException(
                        detail="Transaction PIN is not set.",
                        error_code="PIN_NOT_SET",
                    )
                wallet.transaction_pin_reference = None
                await self.wallet_repository.update_wallet(wallet, transaction_pin_reference=None)
                await self._reset_pin_state(wallet)
                await self._log_event("transaction_pin_removed", user_id=user_id)

                self.logger.info(
                    "pin_removed",
                    extra={"user_id": str(user_id)},
                )

                return {"message": "Transaction PIN removed successfully."}
        except AuthenticationException:
            raise
        except WalletException:
            raise
        except Exception as exc:
            self.logger.error(
                "pin_removal_failed",
                extra={"user_id": str(user_id), "error": str(exc)},
            )
            raise DatabaseException(
                detail="Transaction PIN removal failed. Please try again.",
                error_code="PIN_REMOVAL_ERROR",
            ) from exc

    async def has_transaction_pin(self, *, user_id: UUID) -> bool:
        """Return whether a wallet already has a transaction PIN configured.

        Args:
            user_id: Unique user identifier.

        Returns:
            True if wallet has PIN set, False otherwise.

        Raises:
            ValidationException: If wallet not found.
        """
        self._require_repository(self.wallet_repository)
        wallet = await self._get_wallet_for_user(user_id)
        return bool(wallet.transaction_pin_reference)

    async def validate_pin_strength(self, pin: str, *, require_complexity: bool = False) -> bool:
        """Validate PIN against security policies.

        Validates format, blacklist, sequential/repeated digits, and optional
        complexity requirements.

        Args:
            pin: PIN to validate.
            require_complexity: Enforce additional complexity. Defaults to False.

        Returns:
            True if valid.

        Raises:
            ValidationException: If PIN fails validation.
        """
        if not pin:
            raise ValidationException(
                detail="Transaction PIN is required.",
                error_code="PIN_REQUIRED",
            )

        # Use external validation from security config
        if not validate_pin(pin, length=self.pin_length):
            raise ValidationException(
                detail="Transaction PIN must be numeric and match the configured length.",
                error_code="PIN_INVALID_FORMAT",
            )

        # Check blacklist
        if pin in self.BLACKLISTED_PINS:
            raise ValidationException(
                detail="This PIN is not allowed. Please choose a different PIN.",
                error_code="PIN_BLACKLISTED",
            )

        # Prevent sequential digits
        if self._is_sequential(pin):
            raise ValidationException(
                detail="PIN cannot contain sequential digits.",
                error_code="PIN_SEQUENTIAL",
            )

        # Prevent repeated digits
        if self._is_all_same_digit(pin):
            raise ValidationException(
                detail="PIN cannot contain all same digits.",
                error_code="PIN_REPEATED",
            )

        # Check complexity if required
        if require_complexity and not self._has_complexity(pin):
            raise ValidationException(
                detail="Transaction PIN does not meet the minimum complexity requirements.",
                error_code="PIN_INSUFFICIENT_COMPLEXITY",
            )

        return True

    async def increment_failed_attempts(self, *, user_id: UUID) -> dict[str, Any]:
        """Increment failed PIN verification attempt and lock if threshold exceeded.

        Args:
            user_id: User identifier.

        Returns:
            Dictionary with attempt count, lock status, and lock expiration.

        Raises:
            DatabaseException: If operation fails.
        """
        self._require_repository(self.wallet_repository)

        try:
            async with self._session_scope():
                wallet = await self._get_wallet_for_user(user_id, lock=True)
                metadata = self._parse_metadata(wallet.metadata_payload)
                attempt_count = int(metadata.get("pin_failed_attempts", 0)) + 1
                metadata["pin_failed_attempts"] = attempt_count

                is_locked = False
                if attempt_count >= self.max_failed_attempts:
                    lock_until = (datetime.now(timezone.utc) + timedelta(minutes=self.DEFAULT_LOCK_DURATION_MINUTES)).isoformat()
                    metadata["pin_locked_until"] = lock_until
                    metadata["pin_lock_reason"] = "max_attempts_exceeded"
                    is_locked = True

                wallet.metadata_payload = self._serialize_metadata(metadata)
                await self.wallet_repository.update_wallet(wallet, metadata_payload=wallet.metadata_payload)
                await self._log_event("transaction_pin_attempt_failed", user_id=user_id, metadata={"attempt": attempt_count})

                self.logger.warning(
                    "pin_attempt_failed",
                    extra={"user_id": str(user_id), "attempt": attempt_count, "locked": is_locked},
                )

                return {
                    "failed_attempts": attempt_count,
                    "locked": is_locked,
                    "locked_until": metadata.get("pin_locked_until"),
                }
        except Exception as exc:
            self.logger.error(
                "increment_failed_attempts_failed",
                extra={"user_id": str(user_id), "error": str(exc)},
            )
            raise DatabaseException(
                detail="Failed to update PIN attempt state.",
                error_code="PIN_ATTEMPT_UPDATE_ERROR",
            ) from exc

    async def reset_failed_attempts(self, *, user_id: UUID) -> dict[str, Any]:
        """Clear failed PIN attempts and unlock the PIN.

        Args:
            user_id: User identifier.

        Returns:
            Dictionary with success message and attempts cleared.

        Raises:
            DatabaseException: If operation fails.
        """
        self._require_repository(self.wallet_repository)

        try:
            async with self._session_scope():
                wallet = await self._get_wallet_for_user(user_id, lock=True)
                metadata = self._parse_metadata(wallet.metadata_payload)
                cleared_attempts = int(metadata.pop("pin_failed_attempts", 0))
                metadata.pop("pin_locked_until", None)
                metadata.pop("pin_lock_reason", None)
                wallet.metadata_payload = self._serialize_metadata(metadata)
                await self.wallet_repository.update_wallet(wallet, metadata_payload=wallet.metadata_payload)
                await self._log_event("transaction_pin_attempts_reset", user_id=user_id)

                self.logger.debug(
                    "pin_attempts_reset",
                    extra={"user_id": str(user_id), "attempts_cleared": cleared_attempts},
                )

                return {
                    "message": "PIN attempt state reset.",
                    "attempts_cleared": cleared_attempts,
                }
        except Exception as exc:
            self.logger.error(
                "reset_failed_attempts_failed",
                extra={"user_id": str(user_id), "error": str(exc)},
            )
            raise DatabaseException(
                detail="Failed to reset PIN attempt state.",
                error_code="PIN_ATTEMPT_RESET_ERROR",
            ) from exc

    async def lock_transaction_pin(self, *, user_id: UUID, reason: str | None = None) -> dict[str, Any]:
        """Manually lock a wallet transaction PIN.

        Typically called by security/admin systems on suspicious activity or
        security incidents.

        Args:
            user_id: User identifier.
            reason: Reason for lock (e.g., "suspicious_activity"). Optional.

        Returns:
            Dictionary with lock status and expiration timestamp.

        Raises:
            ValidationException: If wallet not found.
            DatabaseException: If operation fails.
        """
        self._require_repository(self.wallet_repository)

        self.logger.warning(
            "pin_manual_lock_started",
            extra={"user_id": str(user_id), "reason": reason},
        )

        try:
            async with self._session_scope():
                wallet = await self._get_wallet_for_user(user_id, lock=True)
                metadata = self._parse_metadata(wallet.metadata_payload)
                lock_until = (datetime.now(timezone.utc) + timedelta(minutes=self.DEFAULT_LOCK_DURATION_MINUTES)).isoformat()
                metadata["pin_locked_until"] = lock_until
                metadata["pin_lock_reason"] = reason or "manual_lock"
                wallet.metadata_payload = self._serialize_metadata(metadata)
                await self.wallet_repository.update_wallet(wallet, metadata_payload=wallet.metadata_payload)
                await self._log_event("transaction_pin_locked", user_id=user_id, metadata={"reason": reason})

                self.logger.info(
                    "pin_locked",
                    extra={"user_id": str(user_id), "reason": reason},
                )

                return {
                    "message": "Transaction PIN locked.",
                    "status": "locked",
                    "locked_until": lock_until,
                }
        except Exception as exc:
            self.logger.error(
                "pin_lock_failed",
                extra={"user_id": str(user_id), "error": str(exc)},
            )
            raise DatabaseException(
                detail="Failed to lock transaction PIN.",
                error_code="PIN_LOCK_ERROR",
            ) from exc

    async def unlock_transaction_pin(self, *, user_id: UUID) -> dict[str, Any]:
        """Unlock a wallet transaction PIN.

        Removes PIN lockout immediately. Used after security incident resolution
        or manual admin unlock.

        Args:
            user_id: User identifier.

        Returns:
            Dictionary with success message.

        Raises:
            ValidationException: If wallet not found.
            DatabaseException: If operation fails.
        """
        self._require_repository(self.wallet_repository)

        self.logger.info(
            "pin_manual_unlock_started",
            extra={"user_id": str(user_id)},
        )

        try:
            async with self._session_scope():
                wallet = await self._get_wallet_for_user(user_id, lock=True)
                metadata = self._parse_metadata(wallet.metadata_payload)
                metadata.pop("pin_locked_until", None)
                metadata.pop("pin_lock_reason", None)
                wallet.metadata_payload = self._serialize_metadata(metadata)
                await self.wallet_repository.update_wallet(wallet, metadata_payload=wallet.metadata_payload)
                await self._log_event("transaction_pin_unlocked", user_id=user_id)

                self.logger.info(
                    "pin_unlocked",
                    extra={"user_id": str(user_id)},
                )

                return {
                    "message": "Transaction PIN unlocked.",
                    "status": "unlocked",
                }
        except Exception as exc:
            self.logger.error(
                "pin_unlock_failed",
                extra={"user_id": str(user_id), "error": str(exc)},
            )
            raise DatabaseException(
                detail="Failed to unlock transaction PIN.",
                error_code="PIN_UNLOCK_ERROR",
            ) from exc

    async def get_pin_status(self, *, user_id: UUID) -> dict[str, Any]:
        """Get comprehensive PIN status for wallet.

        Returns PIN configuration, lock status, and failed attempts.

        Args:
            user_id: User identifier.

        Returns:
            Dictionary with PIN status including has_pin, is_locked, failed
            attempts, and remaining attempts before lockout.

        Raises:
            ValidationException: If wallet not found.
        """
        self._require_repository(self.wallet_repository)

        wallet = await self._get_wallet_for_user(user_id)
        metadata = self._parse_metadata(wallet.metadata_payload)

        has_pin = bool(wallet.transaction_pin_reference)
        failed_attempts = int(metadata.get("pin_failed_attempts", 0))
        is_locked = self._is_pin_locked(wallet)
        lock_reason = metadata.get("pin_lock_reason")
        locked_until = metadata.get("pin_locked_until")
        attempts_remaining = max(0, self.max_failed_attempts - failed_attempts)

        return {
            "user_id": str(user_id),
            "has_pin": has_pin,
            "is_locked": is_locked,
            "locked_until": locked_until,
            "lock_reason": lock_reason,
            "failed_attempts": failed_attempts,
            "attempts_remaining": attempts_remaining,
            "status": "locked" if is_locked else ("set" if has_pin else "not_set"),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _get_wallet_for_user(self, user_id: UUID, *, lock: bool = False) -> Wallet:
        """Get wallet for user with optional row lock for updates."""
        wallet = await self.wallet_repository.get_user_wallet(user_id=user_id)
        if not wallet:
            raise ValidationException(
                detail="Wallet not found.",
                error_code="WALLET_NOT_FOUND",
            )
        if lock and self.session is not None:
            statement = select(Wallet).where(Wallet.user_id == user_id).with_for_update()
            result = await self.session.execute(statement)
            wallet = result.scalar_one_or_none()
            if not wallet:
                raise ValidationException(
                    detail="Wallet not found.",
                    error_code="WALLET_NOT_FOUND",
                )
        return wallet

    def _is_pin_locked(self, wallet: Wallet) -> bool:
        """Check if PIN is locked due to failed attempts or manual lock."""
        metadata = self._parse_metadata(wallet.metadata_payload)
        lock_until = metadata.get("pin_locked_until")
        if not lock_until:
            return False
        try:
            expires_at = datetime.fromisoformat(lock_until.replace("Z", "+00:00"))
        except ValueError:
            return False
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at > datetime.now(timezone.utc)

    async def _reset_pin_state(self, wallet: Wallet) -> None:
        """Clear failed attempts and lockout state."""
        metadata = self._parse_metadata(wallet.metadata_payload)
        metadata.pop("pin_failed_attempts", None)
        metadata.pop("pin_locked_until", None)
        metadata.pop("pin_lock_reason", None)
        wallet.metadata_payload = self._serialize_metadata(metadata)
        await self.wallet_repository.update_wallet(wallet, metadata_payload=wallet.metadata_payload)

    def _has_complexity(self, pin: str) -> bool:
        """Check if PIN meets minimum complexity requirements."""
        if len(pin) < 4:
            return False
        if len(set(pin)) <= 1:
            return False
        if self._is_sequential(pin):
            return False
        return True

    def _is_sequential(self, pin: str) -> bool:
        """Check if PIN contains sequential digits (1234, 4321, etc)."""
        if len(pin) < 2:
            return False
        digits = [int(char) for char in pin]
        ascending = all(b == a + 1 for a, b in zip(digits, digits[1:]))
        descending = all(b == a - 1 for a, b in zip(digits, digits[1:]))
        return ascending or descending

    def _is_all_same_digit(self, pin: str) -> bool:
        """Check if PIN consists of all same digits (0000, 1111, etc)."""
        return len(set(pin)) == 1

    def _parse_metadata(self, payload: str | None) -> dict[str, Any]:
        """Parse metadata JSON from wallet payload."""
        if not payload:
            return {}
        try:
            parsed = json.loads(payload)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except json.JSONDecodeError:
            return {"value": payload}

    def _serialize_metadata(self, metadata: dict[str, Any]) -> str | None:
        """Serialize metadata to JSON for wallet payload."""
        if not metadata:
            return None
        return json.dumps(metadata, default=str)

    async def _log_event(
        self,
        event_name: str,
        *,
        user_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Log PIN operation event for security and audit."""
        self.logger.info(
            "wallet_pin_event",
            extra={
                "event": event_name,
                "user_id": str(user_id) if user_id else None,
                "metadata": metadata or {},
            },
        )
        if self.audit_service is not None:
            try:
                await self.audit_service(event_name, user_id=user_id, metadata=metadata)
            except TypeError:
                # Fallback for sync audit service interface
                self.audit_service(event_name, user_id=user_id, metadata=metadata)

    def _require_repository(self, repository: Any | None) -> None:
        """Validate that required repository is configured."""
        if repository is None:
            raise RuntimeError("Required repository is not configured for WalletPinService.")

    def _session_scope(self) -> Any:
        """Get database session context manager for transaction management."""
        if self.session is None:
            return _NullSessionContext()
        return self.session.begin()


class _NullSessionContext:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False
