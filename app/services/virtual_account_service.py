from __future__ import annotations

import inspect
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from uuid import UUID, uuid4

from app.config.settings import settings
from app.helpers.generate_reference import generate_virtual_account_reference
from ..models.virtual_account import VirtualAccount
from app.repositories.user_repository import UserRepository
from app.repositories.virtual_account_repository import VirtualAccountRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.notification_service import build_notification_service
from app.utils.exceptions import DatabaseException, ProviderException, ValidationException, WalletException


class VirtualAccountService:
    """Business service for provider-issued virtual account workflows.

    This service contains all domain business logic for virtual accounts,
    including validation, provider coordination, persistence orchestration,
    and lifecycle state changes. Repositories remain persistence-only and
    integrations remain provider-facing.
    """

    def __init__(
        self,
        *,
        virtual_account_repository: VirtualAccountRepository,
        wallet_repository: WalletRepository,
        user_repository: UserRepository,
        provider_services: Mapping[str, Any] | None = None,
        provider_router: Any | None = None,
        audit_logger: Any | None = None,
        notification_service: Any | None = None,
        session: Any | None = None,
        logger: logging.Logger | None = None,
        redis_client: Any | None = None,
        lock_timeout_seconds: int | None = None,
        retry_interval_seconds: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        self.virtual_account_repository = virtual_account_repository
        self.wallet_repository = wallet_repository
        self.user_repository = user_repository
        self.provider_services = dict(provider_services or {})
        self.provider_router = provider_router
        self.audit_logger = audit_logger
        self.session = session
        self.logger = logger or logging.getLogger(__name__)
        self.notification_service = notification_service
        self.redis_client = redis_client
        self.lock_timeout_seconds = lock_timeout_seconds or settings.virtual_account_retry_job_lock_timeout_seconds
        self.retry_interval_seconds = retry_interval_seconds or settings.virtual_account_retry_job_retry_interval_seconds
        self.max_retries = max_retries if max_retries is not None else settings.virtual_account_max_retries
        if self.notification_service is None and self.session is not None:
            try:
                self.notification_service = build_notification_service(session=self.session, redis_client=None)
            except Exception as exc:
                self.logger.warning(
                    "failed_to_build_notification_service",
                    extra={"error": str(exc)},
                )

    async def create_virtual_account(
        self,
        *,
        wallet_id: UUID,
        user_id: UUID,
        provider: str,
        account_number: str,
        account_name: str | None = None,
        bank_name: str | None = None,
        currency: str = "NGN",
        metadata: Mapping[str, Any] | None = None,
        is_primary: bool | None = None,
    ) -> VirtualAccount:
        """Create a virtual account directly from domain data.

        This method performs validation, duplicate checks, wallet ownership
        validation, and primary-account coordination without delegating to
        providers.
        """
        await self._ensure_wallet_context(wallet_id=wallet_id, user_id=user_id)
        self._validate_required_fields(provider=provider, account_number=account_number)
        await self._check_duplicate_account(wallet_id=wallet_id, provider=provider, account_number=account_number)

        should_be_primary = await self._resolve_primary_flag(wallet_id=wallet_id, is_primary=is_primary)

        virtual_account = VirtualAccount(
            id=uuid4(),
            wallet_id=wallet_id,
            user_id=user_id,
            provider=provider.strip().lower(),
            account_number=account_number.strip(),
            account_name=account_name.strip() if account_name else None,
            bank_name=bank_name.strip() if bank_name else None,
            provider_reference=generate_virtual_account_reference(prefix=provider.upper()),
            currency=currency.upper(),
            status="PENDING",
            kyc_status="NOT_STARTED",
            verification_status="PENDING",
            is_active=True,
            is_primary=should_be_primary,
            metadata=dict(metadata or {}),
        )

        try:
            created = await self._run_in_transaction(self.virtual_account_repository.create, virtual_account)
            if should_be_primary:
                await self._clear_other_primary_accounts(wallet_id=wallet_id, current_id=created.id)
            self.logger.info(
                "virtual_account_created",
                extra={
                    "event": "virtual_account_created",
                    "wallet_id": str(wallet_id),
                    "user_id": str(user_id),
                    "provider": provider,
                    "account_number": account_number,
                    "is_primary": should_be_primary,
                },
            )
            await self._emit_audit("virtual_account_created", wallet_id=wallet_id, user_id=user_id, provider=provider)
            return created
        except ValidationException:
            raise
        except WalletException:
            raise
        except DatabaseException:
            raise
        except Exception as exc:  # pragma: no cover - defensive guard
            self.logger.exception("Failed to create virtual account")
            raise DatabaseException(detail="Unable to create virtual account record.") from exc

    async def create_provider_virtual_account(
        self,
        *,
        wallet_id: UUID,
        user_id: UUID,
        provider: str,
        customer: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        is_primary: bool | None = None,
    ) -> VirtualAccount:
        """Create a provider-backed virtual account using the integrations layer."""
        await self._ensure_wallet_context(wallet_id=wallet_id, user_id=user_id)
        provider_name = (provider or "flutterwave").strip().lower()
        self._validate_required_fields(provider=provider_name)

        provider_service = self._resolve_provider_service(provider_name)
        if provider_service is None:
            raise ProviderException(detail=f"No provider integration is configured for {provider_name}.")

        should_be_primary = await self._resolve_primary_flag(wallet_id=wallet_id, is_primary=is_primary)

        response_payload: Any | None = None
        try:
            response_payload = await provider_service.create_virtual_account(
                customer=dict(customer or {}),
                metadata=dict(metadata or {}),
            )
        except Exception as exc:  # pragma: no cover - defensive guard
            self.logger.exception("Provider virtual account creation failed", extra={"provider": provider_name})
            raise ProviderException(detail=f"Provider {provider_name} failed to create the virtual account.") from exc

        normalized = self._normalize_provider_payload(response_payload, provider_name=provider_name)
        await self._check_duplicate_account(
            wallet_id=wallet_id,
            provider=provider_name,
            account_number=normalized.get("account_number"),
            provider_reference=normalized.get("provider_reference"),
            provider_account_id=normalized.get("provider_account_id"),
        )

        virtual_account = VirtualAccount(
            id=uuid4(),
            wallet_id=wallet_id,
            user_id=user_id,
            provider=provider_name,
            account_number=normalized.get("account_number") or "",
            account_name=normalized.get("account_name"),
            bank_name=normalized.get("bank_name"),
            provider_reference=normalized.get("provider_reference"),
            provider_customer_id=normalized.get("provider_customer_id"),
            provider_account_id=normalized.get("provider_account_id"),
            currency=normalized.get("currency") or "NGN",
            status=normalized.get("status") or "PENDING",
            kyc_status=normalized.get("kyc_status") or "NOT_STARTED",
            verification_status=normalized.get("verification_status") or "PENDING",
            is_active=bool(normalized.get("is_active", True)),
            is_primary=should_be_primary,
            metadata=self._merge_metadata(normalized.get("metadata"), metadata),
        )

        try:
            created = await self._run_in_transaction(self.virtual_account_repository.create, virtual_account)
            if should_be_primary:
                await self._clear_other_primary_accounts(wallet_id=wallet_id, current_id=created.id)
            self.logger.info(
                "provider_virtual_account_created",
                extra={
                    "event": "provider_virtual_account_created",
                    "wallet_id": str(wallet_id),
                    "user_id": str(user_id),
                    "provider": provider_name,
                    "account_number": created.account_number,
                },
            )
            await self._emit_audit("provider_virtual_account_created", wallet_id=wallet_id, provider=provider_name)
            return created
        except ValidationException:
            raise
        except WalletException:
            raise
        except DatabaseException:
            raise
        except Exception as exc:  # pragma: no cover - defensive guard
            self.logger.exception("Failed to persist provider virtual account")
            raise DatabaseException(detail="Unable to persist the provider virtual account.") from exc

    async def get_virtual_account(self, *, virtual_account_id: UUID) -> VirtualAccount:
        """Retrieve a virtual account by identifier."""
        account = await self.virtual_account_repository.get_by_id(virtual_account_id)
        if account is None:
            raise ValidationException(detail="Virtual account was not found.")
        return account

    async def get_primary_virtual_account(self, *, wallet_id: UUID) -> VirtualAccount:
        """Retrieve the primary virtual account for a wallet."""
        account = await self.virtual_account_repository.get_primary_by_wallet(wallet_id)
        if account is None:
            raise ValidationException(detail="No primary virtual account was found for the wallet.")
        return account

    async def list_virtual_accounts(
        self,
        *,
        wallet_id: UUID | None = None,
        user_id: UUID | None = None,
        provider: str | None = None,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
    ) -> tuple[list[VirtualAccount], int]:
        """List virtual accounts using the appropriate repository query."""
        if wallet_id is not None:
            return await self.virtual_account_repository.list_by_wallet(
                wallet_id=wallet_id,
                page=page,
                page_size=page_size,
                status=status,
            )
        if user_id is not None:
            return await self.virtual_account_repository.list_by_user(
                user_id=user_id,
                page=page,
                page_size=page_size,
                status=status,
            )
        if provider is not None:
            return await self.virtual_account_repository.list_by_provider(
                provider=provider,
                page=page,
                page_size=page_size,
                status=status,
            )
        raise ValidationException(detail="Either wallet_id, user_id, or provider must be provided.")

    async def get_pending_accounts(self, *, limit: int = 100) -> list[VirtualAccount]:
        """Return a bounded batch of retryable virtual accounts."""
        return await self.virtual_account_repository.list_retryable_accounts(limit=limit)

    async def process_retryable_account(
        self,
        *,
        virtual_account: VirtualAccount,
        max_retries: int | None = None,
    ) -> VirtualAccount:
        """Process a single retryable account while avoiding duplicate worker execution."""
        effective_max_retries = self.max_retries if max_retries is None else max_retries
        if (virtual_account.retry_count or 0) >= effective_max_retries:
            virtual_account.status = "FAILED"
            virtual_account.next_retry_at = None
            virtual_account.last_error = "Max retries exceeded"
            await self.virtual_account_repository.update(virtual_account)
            self.logger.info(
                "virtual_account_retry_exhausted",
                extra={
                    "wallet_id": str(virtual_account.wallet_id),
                    "virtual_account_id": str(virtual_account.id),
                    "provider": virtual_account.provider,
                    "retry_count": virtual_account.retry_count or 0,
                    "status": virtual_account.status,
                    "duration": 0,
                    "error": "Max retries exceeded",
                },
            )
            return virtual_account

        if not await self._acquire_retry_lock(virtual_account):
            self.logger.info(
                "virtual_account_retry_skipped",
                extra={
                    "wallet_id": str(virtual_account.wallet_id),
                    "virtual_account_id": str(virtual_account.id),
                    "provider": virtual_account.provider,
                    "retry_count": virtual_account.retry_count or 0,
                    "status": virtual_account.status,
                    "duration": 0,
                    "error": "Retry lock already held",
                },
            )
            return virtual_account

        started_at = datetime.now(timezone.utc)
        try:
            updated = await self.provision_existing_account(virtual_account=virtual_account)
            duration = int((datetime.now(timezone.utc) - started_at).total_seconds())
            self.logger.info(
                "virtual_account_retry_processed",
                extra={
                    "wallet_id": str(updated.wallet_id),
                    "virtual_account_id": str(updated.id),
                    "provider": updated.provider,
                    "retry_count": updated.retry_count or 0,
                    "status": updated.status,
                    "duration": duration,
                    "error": updated.last_error,
                },
            )
            return updated
        except Exception as exc:
            duration = int((datetime.now(timezone.utc) - started_at).total_seconds())
            self.logger.exception(
                "virtual_account_retry_failed",
                extra={
                    "wallet_id": str(virtual_account.wallet_id),
                    "virtual_account_id": str(virtual_account.id),
                    "provider": virtual_account.provider,
                    "retry_count": virtual_account.retry_count or 0,
                    "status": virtual_account.status,
                    "duration": duration,
                    "error": str(exc),
                },
            )
            return virtual_account
        finally:
            await self._release_retry_lock(virtual_account)

    async def set_primary_virtual_account(self, *, virtual_account_id: UUID, wallet_id: UUID) -> VirtualAccount:
        """Set an existing virtual account as the wallet's primary account."""
        account = await self.get_virtual_account(virtual_account_id=virtual_account_id)
        if account.wallet_id != wallet_id:
            raise ValidationException(detail="Virtual account does not belong to the provided wallet.")

        current_primary = await self.virtual_account_repository.get_primary_by_wallet(wallet_id)
        if current_primary is not None and current_primary.id != account.id:
            await self._run_in_transaction(self.virtual_account_repository.unset_primary, current_primary)

        updated = await self._run_in_transaction(self.virtual_account_repository.set_primary, account)
        self.logger.info(
            "virtual_account_primary_changed",
            extra={"event": "virtual_account_primary_changed", "wallet_id": str(wallet_id), "virtual_account_id": str(virtual_account_id)},
        )
        await self._emit_audit("virtual_account_primary_changed", wallet_id=wallet_id, virtual_account_id=virtual_account_id)
        return updated

    async def activate_virtual_account(self, *, virtual_account_id: UUID) -> VirtualAccount:
        """Activate a virtual account."""
        account = await self.get_virtual_account(virtual_account_id=virtual_account_id)
        updated = await self._run_in_transaction(self.virtual_account_repository.activate, account)
        self.logger.info(
            "virtual_account_activated",
            extra={"event": "virtual_account_activated", "virtual_account_id": str(virtual_account_id)},
        )
        await self._emit_audit("virtual_account_activated", virtual_account_id=virtual_account_id)
        return updated

    async def suspend_virtual_account(self, *, virtual_account_id: UUID) -> VirtualAccount:
        """Suspend a virtual account."""
        account = await self.get_virtual_account(virtual_account_id=virtual_account_id)
        updated = await self._run_in_transaction(self.virtual_account_repository.suspend, account)
        self.logger.info(
            "virtual_account_suspended",
            extra={"event": "virtual_account_suspended", "virtual_account_id": str(virtual_account_id)},
        )
        await self._emit_audit("virtual_account_suspended", virtual_account_id=virtual_account_id)
        return updated

    async def close_virtual_account(self, *, virtual_account_id: UUID) -> VirtualAccount:
        """Close a virtual account."""
        account = await self.get_virtual_account(virtual_account_id=virtual_account_id)
        updated = await self._run_in_transaction(self.virtual_account_repository.close, account)
        self.logger.info(
            "virtual_account_closed",
            extra={"event": "virtual_account_closed", "virtual_account_id": str(virtual_account_id)},
        )
        await self._emit_audit("virtual_account_closed", virtual_account_id=virtual_account_id)
        return updated

    async def synchronize_provider_account(self, *, virtual_account_id: UUID, provider: str | None = None) -> VirtualAccount:
        """Synchronize a persisted virtual account with its provider state."""
        account = await self.get_virtual_account(virtual_account_id=virtual_account_id)
        provider_name = (provider or account.provider).strip().lower()
        provider_service = self._resolve_provider_service(provider_name)
        if provider_service is None:
            raise ProviderException(detail=f"No provider integration is configured for {provider_name}.")

        try:
            response_payload = await provider_service.get_virtual_account(
                identifier=account.provider_reference or account.provider_account_id or account.account_number,
            )
        except Exception as exc:  # pragma: no cover - defensive guard
            self.logger.exception("Provider synchronization failed", extra={"provider": provider_name})
            raise ProviderException(detail=f"Provider {provider_name} failed to synchronize the virtual account.") from exc

        normalized = self._normalize_provider_payload(response_payload, provider_name=provider_name)
        if normalized.get("account_number") and normalized.get("account_number") != account.account_number:
            account.account_number = normalized["account_number"]
        if normalized.get("account_name") is not None:
            account.account_name = normalized["account_name"]
        if normalized.get("bank_name") is not None:
            account.bank_name = normalized["bank_name"]
        if normalized.get("provider_reference") is not None:
            account.provider_reference = normalized["provider_reference"]
        if normalized.get("provider_customer_id") is not None:
            account.provider_customer_id = normalized["provider_customer_id"]
        if normalized.get("provider_account_id") is not None:
            account.provider_account_id = normalized["provider_account_id"]
        if normalized.get("currency") is not None:
            account.currency = normalized["currency"]
        if normalized.get("status") is not None:
            account.status = normalized["status"]
        if normalized.get("kyc_status") is not None:
            account.kyc_status = normalized["kyc_status"]
        if normalized.get("verification_status") is not None:
            account.verification_status = normalized["verification_status"]
        if normalized.get("is_active") is not None:
            account.is_active = bool(normalized["is_active"])
        if normalized.get("metadata") is not None:
            account.metadata_payload = self._merge_metadata(account.metadata_payload, normalized["metadata"])

        updated = await self._run_in_transaction(self.virtual_account_repository.update, account)
        self.logger.info(
            "virtual_account_synchronized",
            extra={"event": "virtual_account_synchronized", "virtual_account_id": str(virtual_account_id), "provider": provider_name},
        )
        await self._emit_audit("virtual_account_synchronized", virtual_account_id=virtual_account_id, provider=provider_name)
        return updated

    async def verify_virtual_account(self, *, virtual_account_id: UUID, provider: str | None = None) -> VirtualAccount:
        """Verify a virtual account with the provider and update verification metadata."""
        account = await self.get_virtual_account(virtual_account_id=virtual_account_id)
        provider_name = (provider or account.provider).strip().lower()
        provider_service = self._resolve_provider_service(provider_name)
        if provider_service is None:
            raise ProviderException(detail=f"No provider integration is configured for {provider_name}.")

        try:
            response_payload = await provider_service.get_virtual_account(
                identifier=account.provider_reference or account.provider_account_id or account.account_number,
            )
        except Exception as exc:  # pragma: no cover - defensive guard
            self.logger.exception("Provider verification failed", extra={"provider": provider_name})
            raise ProviderException(detail=f"Provider {provider_name} failed to verify the virtual account.") from exc

        normalized = self._normalize_provider_payload(response_payload, provider_name=provider_name)
        if normalized.get("verification_status") is not None:
            account.verification_status = normalized["verification_status"]
        if normalized.get("kyc_status") is not None:
            account.kyc_status = normalized["kyc_status"]
        if normalized.get("status") is not None:
            account.status = normalized["status"]
        updated = await self._run_in_transaction(self.virtual_account_repository.update, account)
        self.logger.info(
            "virtual_account_verified",
            extra={"event": "virtual_account_verified", "virtual_account_id": str(virtual_account_id), "provider": provider_name},
        )
        await self._emit_audit("virtual_account_verified", virtual_account_id=virtual_account_id, provider=provider_name)
        return updated

    async def update_virtual_account(
        self,
        *,
        virtual_account_id: UUID,
        **fields: Any,
    ) -> VirtualAccount:
        """Update mutable virtual account fields using repository persistence."""
        account = await self.get_virtual_account(virtual_account_id=virtual_account_id)
        normalized_fields = dict(fields)
        if "provider" in normalized_fields:
            normalized_fields["provider"] = str(normalized_fields["provider"]).strip().lower()
        if "currency" in normalized_fields:
            normalized_fields["currency"] = str(normalized_fields["currency"]).upper()
        if "account_number" in normalized_fields:
            self._validate_required_fields(account_number=str(normalized_fields["account_number"]).strip())
        updated = await self._run_in_transaction(self.virtual_account_repository.update, account, **normalized_fields)
        self.logger.info(
            "virtual_account_updated",
            extra={"event": "virtual_account_updated", "virtual_account_id": str(virtual_account_id)},
        )
        await self._emit_audit("virtual_account_updated", virtual_account_id=virtual_account_id)
        return updated

    async def delete_virtual_account(self, *, virtual_account_id: UUID) -> None:
        """Delete a virtual account and preserve the primary-account invariant."""
        account = await self.get_virtual_account(virtual_account_id=virtual_account_id)
        if account.is_primary:
            remaining_accounts, _ = await self.virtual_account_repository.list_by_wallet(
                wallet_id=account.wallet_id,
                page=1,
                page_size=100,
            )
            remaining = [item for item in remaining_accounts if item.id != account.id]
            if remaining:
                await self._run_in_transaction(self.virtual_account_repository.set_primary, remaining[0])
        await self._run_in_transaction(self.virtual_account_repository.delete, virtual_account_id)
        self.logger.info(
            "virtual_account_deleted",
            extra={"event": "virtual_account_deleted", "virtual_account_id": str(virtual_account_id)},
        )
        await self._emit_audit("virtual_account_deleted", virtual_account_id=virtual_account_id)

    async def validate_virtual_account(self, *, virtual_account: VirtualAccount | Mapping[str, Any]) -> VirtualAccount:
        """Validate a virtual account object or mapping and return a model instance."""
        if isinstance(virtual_account, VirtualAccount):
            account = virtual_account
        else:
            payload = dict(virtual_account or {})
            account = VirtualAccount(**payload)
        self._validate_required_fields(
            provider=account.provider,
            account_number=account.account_number,
            wallet_id=account.wallet_id,
        )
        if not account.account_number or not account.account_number.strip():
            raise ValidationException(detail="Virtual account number is required.")
        return account

    async def check_virtual_account_exists(
        self,
        *,
        wallet_id: UUID | None = None,
        provider: str | None = None,
        provider_reference: str | None = None,
        provider_account_id: str | None = None,
        account_number: str | None = None,
    ) -> bool:
        """Check whether a virtual account already exists for the given attributes."""
        if provider_reference:
            return await self.virtual_account_repository.get_by_provider_reference(provider_reference) is not None
        if provider_account_id:
            return await self.virtual_account_repository.get_by_provider_account_id(provider_account_id) is not None
        if account_number:
            existing = await self.virtual_account_repository.get_by_account_number(account_number)
            if existing is None:
                return False
            if wallet_id is not None and existing.wallet_id != wallet_id:
                return False
            if provider is not None and existing.provider != provider.strip().lower():
                return False
            return True
        if wallet_id is not None:
            return await self.virtual_account_repository.count_by_wallet(wallet_id) > 0
        return False

    async def get_virtual_account_statistics(
        self,
        *,
        wallet_id: UUID | None = None,
        user_id: UUID | None = None,
        provider: str | None = None,
    ) -> dict[str, Any]:
        """Build a lightweight statistics payload for virtual accounts."""
        if wallet_id is not None:
            accounts, _ = await self.virtual_account_repository.list_by_wallet(
                wallet_id=wallet_id,
                page=1,
                page_size=1000,
            )
        elif user_id is not None:
            accounts, _ = await self.virtual_account_repository.list_by_user(
                user_id=user_id,
                page=1,
                page_size=1000,
            )
        elif provider is not None:
            accounts, _ = await self.virtual_account_repository.list_by_provider(
                provider=provider,
                page=1,
                page_size=1000,
            )
        else:
            return {
                "total_accounts": 0,
                "active_accounts": 0,
                "primary_accounts": 0,
                "pending_accounts": 0,
                "providers": [],
            }

        provider_names = sorted({account.provider for account in accounts})
        return {
            "total_accounts": len(accounts),
            "active_accounts": sum(1 for account in accounts if account.is_active),
            "primary_accounts": sum(1 for account in accounts if account.is_primary),
            "pending_accounts": sum(1 for account in accounts if account.status.upper() == "PENDING"),
            "providers": provider_names,
        }

    async def _ensure_wallet_context(self, *, wallet_id: UUID, user_id: UUID) -> None:
        """Validate the wallet exists and belongs to the supplied user."""
        wallet = await self.wallet_repository.get_by_id(wallet_id)
        if wallet is None:
            raise WalletException(detail="Wallet was not found.")
        if wallet.user_id != user_id:
            raise ValidationException(detail="Wallet does not belong to the provided user.")
        if not getattr(wallet, "is_active", True):
            raise WalletException(detail="Wallet is not active.")
        if getattr(wallet, "is_frozen", False) or getattr(wallet, "is_suspended", False):
            raise WalletException(detail="Wallet is frozen or suspended.")
        if getattr(wallet, "status", "active").lower() not in {"active", "enabled", "open"}:
            raise WalletException(detail="Wallet is not in a valid state for virtual account creation.")

        user = await self.user_repository.get_by_id(user_id)
        if user is None:
            raise ValidationException(detail="User was not found.")

    async def _check_duplicate_account(
        self,
        *,
        wallet_id: UUID,
        provider: str,
        account_number: str | None = None,
        provider_reference: str | None = None,
        provider_account_id: str | None = None,
    ) -> None:
        """Prevent duplicate virtual accounts based on provider account identifiers."""
        if provider_reference:
            existing = await self.virtual_account_repository.get_by_provider_reference(provider_reference)
            if existing is not None and existing.wallet_id != wallet_id:
                raise ValidationException(detail="A virtual account already exists for the provided provider reference.")
        if provider_account_id:
            existing = await self.virtual_account_repository.get_by_provider_account_id(provider_account_id)
            if existing is not None and existing.wallet_id != wallet_id:
                raise ValidationException(detail="A virtual account already exists for the provided provider account identifier.")
        if account_number:
            existing = await self.virtual_account_repository.get_by_account_number(account_number)
            if existing is not None and existing.wallet_id != wallet_id and existing.provider == provider.strip().lower():
                raise ValidationException(detail="A virtual account already exists for the provided account number for this provider.")

    async def _clear_other_primary_accounts(self, *, wallet_id: UUID, current_id: UUID) -> None:
        """Ensure only one primary virtual account remains for a wallet."""
        primary_account = await self.virtual_account_repository.get_primary_by_wallet(wallet_id)
        if primary_account is not None and primary_account.id != current_id:
            await self.virtual_account_repository.unset_primary(primary_account)

    async def _run_in_transaction(self, action: Any, *args: Any, **kwargs: Any) -> Any:
        """Execute repository actions within an optional transaction context."""
        if self.session is None:
            return await action(*args, **kwargs)
        async with self.session.begin():
            return await action(*args, **kwargs)

    def _resolve_provider_service(self, provider_name: str) -> Any | None:
        """Resolve a provider integration service using the injected provider map or router."""
        provider_key = provider_name.strip().lower()
        if provider_key in self.provider_services:
            return self.provider_services[provider_key]
        if self.provider_router is not None:
            try:
                return self.provider_router(provider_key)
            except TypeError:
                return self.provider_router(provider_key, None)
        return None

    def _normalize_provider_payload(self, payload: Any, *, provider_name: str) -> dict[str, Any]:
        """Normalize provider payloads from various integration response shapes."""
        if payload is None:
            return {}
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        elif hasattr(payload, "dict"):
            payload = payload.dict()

        if isinstance(payload, Mapping):
            normalized = dict(payload)
        else:
            normalized = {}

        if not normalized and isinstance(payload, str):
            normalized = {"provider_reference": payload}

        if not normalized:
            raise ValidationException(detail=f"Invalid response returned by provider {provider_name}.")

        return normalized

    def _merge_metadata(self, *mappings: Mapping[str, Any] | None) -> dict[str, Any]:
        """Merge metadata payloads into a dictionary without mutating input mappings."""
        merged: dict[str, Any] = {}
        for mapping in mappings:
            if not mapping:
                continue
            merged.update(dict(mapping))
        return merged

    async def _resolve_primary_flag(self, *, wallet_id: UUID, is_primary: bool | None) -> bool:
        """Determine whether the account should be marked as primary."""
        if is_primary is not None:
            return is_primary
        existing_primary = await self._get_primary_for_wallet_sync(wallet_id)
        return existing_primary is None

    async def _get_primary_for_wallet_sync(self, wallet_id: UUID) -> VirtualAccount | None:
        """Safely resolve whether a wallet already has a primary account."""
        return await self.virtual_account_repository.get_primary_by_wallet(wallet_id)

    async def _acquire_retry_lock(self, virtual_account: VirtualAccount) -> bool:
        """Acquire a short-lived Redis lock for an account retry cycle when Redis is available."""
        if self.redis_client is None:
            return True
        if not hasattr(self.redis_client, "set") or not callable(self.redis_client.set):
            return True
        try:
            key = f"virtual-account-retry:{virtual_account.id}"
            result = self.redis_client.set(key, "1", nx=True, ex=self.lock_timeout_seconds)
            if inspect.isawaitable(result):
                result = await result
            return bool(result)
        except Exception as exc:
            self.logger.warning(
                "virtual_account_retry_lock_unavailable",
                extra={
                    "wallet_id": str(virtual_account.wallet_id),
                    "virtual_account_id": str(virtual_account.id),
                    "provider": virtual_account.provider,
                    "error": str(exc),
                },
            )
            return True

    async def _release_retry_lock(self, virtual_account: VirtualAccount) -> None:
        """Release a previously acquired Redis retry lock when available."""
        if self.redis_client is None:
            return
        if not hasattr(self.redis_client, "delete") or not callable(self.redis_client.delete):
            return
        try:
            result = self.redis_client.delete(f"virtual-account-retry:{virtual_account.id}")
            if inspect.isawaitable(result):
                await result
        except Exception:
            return

    def _validate_required_fields(self, *, provider: str | None = None, account_number: str | None = None, wallet_id: UUID | None = None) -> None:
        """Validate domain fields required for virtual account creation."""
        if wallet_id is not None and not isinstance(wallet_id, UUID):
            raise ValidationException(detail="wallet_id must be a valid UUID.")
        if not provider or not str(provider).strip():
            raise ValidationException(detail="Provider is required.")
        if not account_number or not str(account_number).strip():
            raise ValidationException(detail="Account number is required.")

    async def _emit_audit(self, event_name: str, **payload: Any) -> None:
        """Emit audit logging when an audit logger is available."""
        if self.audit_logger is None:
            return
        if hasattr(self.audit_logger, "log_event"):
            result = self.audit_logger.log_event(event_name=event_name, **payload)
        elif hasattr(self.audit_logger, "log"):
            result = self.audit_logger.log(event_name, payload)
        elif callable(self.audit_logger):
            result = self.audit_logger(event_name, payload)
        else:
            self.audit_logger.info(event_name, extra=payload)
            return

        if inspect.isawaitable(result):
            await result

    async def _notify_virtual_account_activated(self, virtual_account: "VirtualAccount") -> None:
        """Notify the user when a virtual account becomes active."""
        if self.notification_service is None:
            return

        try:
            identifier = virtual_account.account_number or virtual_account.provider_reference or str(virtual_account.id)
            await self.notification_service.create_notification(
                user_id=virtual_account.user_id,
                title="Virtual account activated",
                message=(
                    f"Your virtual account {identifier} for wallet {virtual_account.wallet_id} is now active."
                ),
                notification_type="virtual_account",
                category="wallet",
                reference=str(virtual_account.id),
                metadata={
                    "provider": virtual_account.provider,
                    "account_number": virtual_account.account_number,
                    "wallet_id": str(virtual_account.wallet_id),
                },
            )
        except Exception:
            pass

    async def provision_existing_account(self, *, virtual_account: "VirtualAccount") -> "VirtualAccount":
        """Attempt to provision an already-persisted virtual account using the configured provider service.

        This method will call the provider integration, update provider identifiers,
        and transition lifecycle status on success. On failure it will record the
        error and increment retry counters and compute next_retry_at using
        exponential backoff. It does not raise to callers; callers should handle
        retry scheduling as needed.
        """
        provider_name = (virtual_account.provider or "flutterwave").strip().lower()
        provider_service = self._resolve_provider_service(provider_name)
        if provider_service is None:
            # No provider available; mark as FAILED and schedule retry
            now = datetime.now(timezone.utc)
            virtual_account.status = "FAILED"
            virtual_account.last_error = f"No provider configured for {provider_name}"
            virtual_account.last_retry_at = now
            virtual_account.retry_count = (virtual_account.retry_count or 0) + 1
            virtual_account.next_retry_at = now
            await self.virtual_account_repository.update(virtual_account)
            return virtual_account

        try:
            # Build customer payload from persisted record when possible
            customer = {
                "email": getattr(virtual_account, "metadata_payload", {}).get("email"),
                "phone_number": getattr(virtual_account, "metadata_payload", {}).get("phone_number"),
                "first_name": getattr(virtual_account, "metadata_payload", {}).get("first_name"),
                "last_name": getattr(virtual_account, "metadata_payload", {}).get("last_name"),
            }

            response = await provider_service.create_virtual_account(customer=customer, metadata=virtual_account.metadata_payload or {})
            normalized = self._normalize_provider_payload(response, provider_name=provider_name)

            # Update persisted virtual account with provider data
            updates: dict[str, Any] = {}
            if normalized.get("account_number"):
                updates["account_number"] = normalized.get("account_number")
            if normalized.get("provider_reference"):
                updates["provider_reference"] = normalized.get("provider_reference")
            if normalized.get("provider_account_id"):
                updates["provider_account_id"] = normalized.get("provider_account_id")
            updates["status"] = "ACTIVE"
            updates["provisioned_at"] = datetime.now(timezone.utc)
            updates["next_retry_at"] = None
            updates["last_error"] = None

            updated = await self.virtual_account_repository.update(virtual_account, **updates)
            # emit audit and optional notification
            await self._emit_audit("virtual_account_provisioned", wallet_id=virtual_account.wallet_id, virtual_account_id=virtual_account.id)
            await self._notify_virtual_account_activated(updated)

            return updated
        except Exception as exc:
            # Record failure and schedule next retry using exponential backoff
            now = datetime.now(timezone.utc)
            virtual_account.last_error = str(exc)
            virtual_account.last_retry_at = now
            virtual_account.retry_count = (virtual_account.retry_count or 0) + 1
            base = self.retry_interval_seconds or settings.virtual_account_initial_retry_delay_seconds
            multiplier = settings.virtual_account_backoff_multiplier
            backoff_seconds = base * (multiplier ** max(0, (virtual_account.retry_count or 1) - 1))
            backoff_seconds = min(backoff_seconds, settings.virtual_account_max_retry_delay_seconds)
            virtual_account.next_retry_at = now + timedelta(seconds=backoff_seconds)
            virtual_account.status = "PENDING"
            if virtual_account.retry_count >= self.max_retries:
                virtual_account.status = "FAILED"
                virtual_account.next_retry_at = None
            await self.virtual_account_repository.update(virtual_account)
            return virtual_account
