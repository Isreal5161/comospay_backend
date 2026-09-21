from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bank_account import BankAccount
from app.repositories.bank_account_repository import BankAccountRepository
from app.repositories.user_repository import UserRepository
from app.services.security.bank_account_encryption import BankAccountEncryption
from app.utils.exceptions import DatabaseException, ProviderException, ValidationException


class ProviderBankAccountAdapter:
    """Adapter for provider-verification calls used by the bank account service."""

    async def verify_account(self, *, account_number: str, bank_code: str, account_name: str | None = None) -> dict[str, Any]:
        """Perform a lightweight local verification fallback when no provider adapter is configured."""
        normalized_account = account_number.strip()
        normalized_bank_code = bank_code.strip()
        if not normalized_account.isdigit() or len(normalized_account) != 10:
            return {"verified": False, "reason": "invalid_account_number"}
        if not normalized_bank_code.isdigit() or len(normalized_bank_code) < 3:
            return {"verified": False, "reason": "invalid_bank_code"}
        return {
            "verified": True,
            "account_name": account_name or "Verified Account",
            "bank_name": "Local Bank",
            "provider_reference": f"local:{uuid4()}",
            "provider_customer_reference": f"cust:{uuid4()}",
        }


class BankAccountService:
    """Coordinate bank account creation, verification, and ownership workflows."""

    def __init__(
        self,
        *,
        user_repository: UserRepository,
        bank_account_repository: BankAccountRepository,
        provider_service: ProviderBankAccountAdapter | None = None,
        notification_service: Any | None = None,
        audit_service: Any | None = None,
        session: AsyncSession | None = None,
        logger: logging.Logger | None = None,
        encryption: BankAccountEncryption | None = None,
    ) -> None:
        self.user_repository = user_repository
        self.bank_account_repository = bank_account_repository
        self.provider_service = provider_service or ProviderBankAccountAdapter()
        self.notification_service = notification_service
        self.audit_service = audit_service
        self.session = session
        self.logger = logger or logging.getLogger(__name__)
        self.encryption = encryption or BankAccountEncryption()

    async def add_bank_account(
        self,
        *,
        user_id: UUID,
        account_number: str,
        bank_code: str,
        bank_name: str | None = None,
        account_name: str | None = None,
        account_type: str | None = None,
        is_default: bool = False,
        provider_name: str | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Validate, verify, and persist a bank account for the authenticated user."""
        self._require_repository(self.user_repository)
        self._require_repository(self.bank_account_repository)

        self._validate_required_fields(account_number=account_number, bank_code=bank_code)
        self._validate_account_number(account_number)
        self._validate_bank_code(bank_code)

        user = await self.user_repository.get_by_id(user_id)
        if not user:
            raise ValidationException("User not found.")

        existing_accounts, _ = await self.bank_account_repository.get_user_accounts(user_id=user_id, page=1, page_size=100)
        for account in existing_accounts:
            if account.is_active and account.account_number_fingerprint == self.encryption.fingerprint(account_number) and account.bank_code == bank_code:
                raise ValidationException("A similar active bank account already exists.")

        verification_result = await self._verify_with_provider(
            account_number=account_number,
            bank_code=bank_code,
            account_name=account_name,
        )
        if not verification_result.get("verified"):
            raise ProviderException("Bank account verification failed.")

        try:
            async with self._session_scope():
                bank_account = BankAccount(
                    user_id=user.id,
                    account_name=verification_result.get("account_name") or account_name,
                    account_number_encrypted=self.encryption.encrypt(account_number),
                    account_number_fingerprint=self.encryption.fingerprint(account_number),
                    account_number_prefix=account_number[:2],
                    account_number_last4=account_number[-4:],
                    bank_name=verification_result.get("bank_name") or bank_name,
                    bank_code=bank_code,
                    account_type=account_type,
                    is_default=is_default,
                    is_active=True,
                    status="verified",
                    provider_name=provider_name,
                    provider_reference=verification_result.get("provider_reference"),
                    provider_customer_reference=verification_result.get("provider_customer_reference"),
                    verified_at=datetime.now(timezone.utc),
                    activated_at=datetime.now(timezone.utc),
                    metadata_payload=self._sanitize_metadata(metadata_payload),
                )
                await self.bank_account_repository.create_bank_account(bank_account)
                if is_default:
                    await self._ensure_single_default(user_id=user_id, bank_account_id=bank_account.id)
                await self._log_event("bank_account_added", user_id=user.id)
                return self._serialize_bank_account(bank_account)
        except ValidationException:
            raise
        except Exception as exc:
            raise DatabaseException("Bank account creation failed.") from exc

    async def verify_bank_account(self, *, account_number: str, bank_code: str, account_name: str | None = None) -> dict[str, Any]:
        """Verify an account number and bank code through the provider service."""
        self._validate_required_fields(account_number=account_number, bank_code=bank_code)
        self._validate_account_number(account_number)
        self._validate_bank_code(bank_code)
        return await self._verify_with_provider(account_number=account_number, bank_code=bank_code, account_name=account_name)

    async def list_bank_accounts(self, *, user_id: UUID, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        """Return active bank accounts belonging to the authenticated user."""
        self._require_repository(self.user_repository)
        self._require_repository(self.bank_account_repository)

        user = await self.user_repository.get_by_id(user_id)
        if not user:
            raise ValidationException("User not found.")

        accounts, total = await self.bank_account_repository.get_user_accounts(
            user_id=user_id,
            page=page,
            page_size=page_size,
        )
        active_accounts = [account for account in accounts if account.is_active]
        return {
            "items": [self._serialize_bank_account(account) for account in active_accounts],
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    async def get_bank_account(self, *, user_id: UUID, bank_account_id: UUID) -> dict[str, Any]:
        """Verify ownership and return the requested bank account."""
        self._require_repository(self.user_repository)
        self._require_repository(self.bank_account_repository)

        user = await self.user_repository.get_by_id(user_id)
        if not user:
            raise ValidationException("User not found.")

        account = await self.bank_account_repository.get_by_id(bank_account_id)
        if not account or account.user_id != user_id:
            raise ValidationException("Bank account not found.")
        return self._serialize_bank_account(account)

    async def resolve_withdrawal_account(self, *, user_id: UUID, bank_account_id: UUID) -> dict[str, Any]:
        """Resolve an authenticated user's verified account for withdrawal use."""
        self._require_repository(self.user_repository)
        self._require_repository(self.bank_account_repository)
        account = await self.bank_account_repository.get_by_id_for_user_for_update(
            bank_account_id=bank_account_id,
            user_id=user_id,
        )
        if account is None:
            raise ValidationException("Bank account not found.")
        if not account.is_active or account.status.lower() != "verified" or account.verified_at is None:
            raise ValidationException("Bank account is not verified for withdrawal.")
        encrypted = getattr(account, "account_number_encrypted", None)
        legacy_number = getattr(account, "account_number", None)
        if not encrypted and legacy_number and not hasattr(account, "account_number_encrypted"):
            account_number = legacy_number
        elif encrypted:
            account_number = self.encryption.decrypt(encrypted)
        else:
            raise ValidationException("Bank account details are incomplete.")
        if not account.bank_code:
            raise ValidationException("Bank account details are incomplete.")
        return {
            "id": account.id,
            "user_id": account.user_id,
            "account_number": account_number,
            "bank_code": account.bank_code,
            "account_name": account.account_name,
            "bank_name": account.bank_name,
        }

    async def set_default_bank_account(self, *, user_id: UUID, bank_account_id: UUID) -> dict[str, Any]:
        """Ensure a single default bank account exists for the user."""
        self._require_repository(self.user_repository)
        self._require_repository(self.bank_account_repository)

        user = await self.user_repository.get_by_id(user_id)
        if not user:
            raise ValidationException("User not found.")

        account = await self.bank_account_repository.get_by_id(bank_account_id)
        if not account or account.user_id != user_id:
            raise ValidationException("Bank account not found.")
        if not account.is_active:
            raise ValidationException("Inactive bank accounts cannot be made default.")

        try:
            async with self._session_scope():
                existing_accounts, _ = await self.bank_account_repository.get_user_accounts(user_id=user_id, page=1, page_size=100)
                for existing_account in existing_accounts:
                    if existing_account.id != account.id and existing_account.is_default:
                        await self.bank_account_repository.update_bank_account(existing_account, is_default=False)
                await self.bank_account_repository.update_bank_account(account, is_default=True)
                await self._log_event("default_bank_account_changed", user_id=user.id)
                return self._serialize_bank_account(account)
        except Exception as exc:
            raise DatabaseException("Default bank account update failed.") from exc

    async def remove_bank_account(self, *, user_id: UUID, bank_account_id: UUID) -> dict[str, Any]:
        """Soft-delete the specified bank account when the business rules allow it."""
        self._require_repository(self.user_repository)
        self._require_repository(self.bank_account_repository)

        user = await self.user_repository.get_by_id(user_id)
        if not user:
            raise ValidationException("User not found.")

        account = await self.bank_account_repository.get_by_id(bank_account_id)
        if not account or account.user_id != user_id:
            raise ValidationException("Bank account not found.")
        if account.is_default:
            raise ValidationException("Default bank accounts cannot be removed directly.")

        try:
            async with self._session_scope():
                account.is_active = False
                account.status = "removed"
                account.deactivated_at = datetime.now(timezone.utc)
                await self.bank_account_repository.update_bank_account(account, is_active=False, status="removed", deactivated_at=account.deactivated_at)
                await self._log_event("bank_account_removed", user_id=user.id)
                return self._serialize_bank_account(account)
        except Exception as exc:
            raise DatabaseException("Bank account removal failed.") from exc

    async def restore_bank_account(self, *, user_id: UUID, bank_account_id: UUID) -> dict[str, Any]:
        """Restore a previously removed bank account if it is still valid."""
        self._require_repository(self.user_repository)
        self._require_repository(self.bank_account_repository)

        user = await self.user_repository.get_by_id(user_id)
        if not user:
            raise ValidationException("User not found.")

        account = await self.bank_account_repository.get_by_id(bank_account_id)
        if not account or account.user_id != user_id:
            raise ValidationException("Bank account not found.")
        if account.is_active:
            raise ValidationException("Bank account is already active.")

        try:
            async with self._session_scope():
                account.is_active = True
                account.status = "verified"
                account.deactivated_at = None
                await self.bank_account_repository.update_bank_account(account, is_active=True, status="verified", deactivated_at=None)
                await self._log_event("bank_account_restored", user_id=user.id)
                return self._serialize_bank_account(account)
        except Exception as exc:
            raise DatabaseException("Bank account restoration failed.") from exc

    async def _verify_with_provider(self, *, account_number: str, bank_code: str, account_name: str | None = None) -> dict[str, Any]:
        try:
            result = await self.provider_service.verify_account(
                account_number=account_number,
                bank_code=bank_code,
                account_name=account_name,
            )
        except NotImplementedError as exc:
            raise ProviderException("Bank account verification is not configured.") from exc
        except Exception as exc:
            raise ProviderException("Bank account verification failed.") from exc

        if not isinstance(result, dict):
            raise ProviderException("Bank account verification failed.")
        return result

    def _validate_required_fields(self, **values: Any) -> None:
        for field_name, value in values.items():
            if value is None or (isinstance(value, str) and not value.strip()):
                raise ValidationException(f"{field_name} is required.")

    def _validate_account_number(self, account_number: str) -> None:
        if not account_number.isdigit() or len(account_number) < 10 or len(account_number) > 10:
            raise ValidationException("account_number must be a 10-digit Nigerian account number.")

    def _validate_bank_code(self, bank_code: str) -> None:
        if not bank_code.isdigit() or len(bank_code) < 3 or len(bank_code) > 6:
            raise ValidationException("bank_code must be a numeric bank code.")

    def _sanitize_metadata(self, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValidationException("metadata_payload must be a string.")
        sanitized = value.strip()
        if len(sanitized) > 1000:
            raise ValidationException("metadata_payload is too long.")
        return sanitized or None

    def _serialize_bank_account(self, bank_account: BankAccount) -> dict[str, Any]:
        return {
            "id": str(bank_account.id),
            "account_name": bank_account.account_name,
            "account_number": self._mask_account_number(bank_account),
            "bank_name": bank_account.bank_name,
            "bank_code": bank_account.bank_code,
            "account_type": bank_account.account_type,
            "is_default": bank_account.is_default,
            "is_active": bank_account.is_active,
            "status": bank_account.status,
            "provider_name": bank_account.provider_name,
            "verified_at": bank_account.verified_at.isoformat() if bank_account.verified_at else None,
            "created_at": bank_account.created_at.isoformat() if bank_account.created_at else None,
        }

    def _mask_account_number(self, bank_account: BankAccount | Any) -> str | None:
        prefix = getattr(bank_account, "account_number_prefix", None)
        last4 = getattr(bank_account, "account_number_last4", None)
        legacy_number = getattr(bank_account, "account_number", None)
        if not prefix and legacy_number:
            prefix = legacy_number[:2]
        if not last4 and legacy_number:
            last4 = legacy_number[-4:]
        if not last4:
            return None
        if prefix and len(last4) == 4:
            return f"{prefix}{'*' * 6}{last4[-2:]}"
        return f"{'*' * 6}{last4}"

    async def _ensure_single_default(self, *, user_id: UUID, bank_account_id: UUID) -> None:
        existing_accounts, _ = await self.bank_account_repository.get_user_accounts(user_id=user_id, page=1, page_size=100)
        for account in existing_accounts:
            if account.id != bank_account_id and account.is_default:
                await self.bank_account_repository.update_bank_account(account, is_default=False)

    async def _log_event(self, event_name: str, *, user_id: UUID | None = None, metadata: dict[str, Any] | None = None) -> None:
        self.logger.info(
            "bank_account_event",
            extra={"event": event_name, "user_id": str(user_id) if user_id else None, "metadata": metadata or {}},
        )
        if callable(getattr(self, "audit_service", None)):
            try:
                await self.audit_service(event_name, user_id=user_id, metadata=metadata)
            except TypeError:
                self.audit_service(event_name, user_id=user_id, metadata=metadata)

    def _require_repository(self, repository: Any | None) -> None:
        if repository is None:
            raise RuntimeError("Required repository is not configured for BankAccountService.")

    def _session_scope(self) -> Any:
        if self.session is None:
            return _NullSessionContext()
        return self.session.begin()


class _NullSessionContext:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False
