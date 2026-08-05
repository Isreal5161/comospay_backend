from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.services.user import BankAccountService, DeviceService, KYCService, ProfileService


class UserService:
    """Facade for user-domain workflows, delegating to internal service modules."""

    def __init__(
        self,
        *,
        profile_service: ProfileService,
        kyc_service: KYCService,
        bank_account_service: BankAccountService,
        device_service: DeviceService,
        logger: logging.Logger | None = None,
    ) -> None:
        self.profile_service = profile_service
        self.kyc_service = kyc_service
        self.bank_account_service = bank_account_service
        self.device_service = device_service
        self.logger = logger or logging.getLogger(__name__)

    async def get_profile(self, *, user_id: UUID) -> dict[str, Any]:
        """Delegate profile retrieval to the profile service."""
        self._log_entry("get_profile", user_id=user_id)
        return await self.profile_service.get_profile(user_id=user_id)

    async def update_profile(self, *, user_id: UUID, profile_data: dict[str, Any]) -> dict[str, Any]:
        """Delegate profile updates to the profile service."""
        self._log_entry("update_profile", user_id=user_id)
        return await self.profile_service.update_profile(user_id=user_id, profile_data=profile_data)

    async def upload_profile_photo(self, *, user_id: UUID, file_path: str, public_id: str | None = None) -> dict[str, Any]:
        """Delegate profile photo uploads to the profile service."""
        self._log_entry("upload_profile_photo", user_id=user_id)
        return await self.profile_service.upload_profile_photo(user_id=user_id, file_path=file_path, public_id=public_id)

    async def remove_profile_photo(self, *, user_id: UUID) -> dict[str, Any]:
        """Delegate profile photo removal to the profile service."""
        self._log_entry("remove_profile_photo", user_id=user_id)
        return await self.profile_service.remove_profile_photo(user_id=user_id)

    async def submit_kyc(
        self,
        *,
        user_id: UUID,
        document_type: str,
        document_reference: str | None = None,
        verification_level: str = "basic",
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Delegate KYC submission to the KYC service."""
        self._log_entry("submit_kyc", user_id=user_id)
        return await self.kyc_service.submit_kyc(
            user_id=user_id,
            document_type=document_type,
            document_reference=document_reference,
            verification_level=verification_level,
            metadata_payload=metadata_payload,
        )

    async def get_kyc_status(self, *, user_id: UUID) -> dict[str, Any]:
        """Delegate KYC status retrieval to the KYC service."""
        self._log_entry("get_kyc_status", user_id=user_id)
        return await self.kyc_service.get_kyc_status(user_id=user_id)

    async def get_kyc_details(self, *, user_id: UUID) -> dict[str, Any]:
        """Delegate KYC details retrieval to the KYC service."""
        self._log_entry("get_kyc_details", user_id=user_id)
        return await self.kyc_service.get_kyc_details(user_id=user_id)

    async def update_kyc(
        self,
        *,
        user_id: UUID,
        document_type: str | None = None,
        document_reference: str | None = None,
        verification_level: str | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Delegate KYC updates to the KYC service."""
        self._log_entry("update_kyc", user_id=user_id)
        return await self.kyc_service.update_kyc(
            user_id=user_id,
            document_type=document_type,
            document_reference=document_reference,
            verification_level=verification_level,
            metadata_payload=metadata_payload,
        )

    async def resubmit_kyc(self, *, user_id: UUID, **payload: Any) -> dict[str, Any]:
        """Delegate KYC resubmission to the KYC service."""
        self._log_entry("resubmit_kyc", user_id=user_id)
        return await self.kyc_service.resubmit_kyc(user_id=user_id, **payload)

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
        """Delegate bank account creation to the bank account service."""
        self._log_entry("add_bank_account", user_id=user_id)
        return await self.bank_account_service.add_bank_account(
            user_id=user_id,
            account_number=account_number,
            bank_code=bank_code,
            bank_name=bank_name,
            account_name=account_name,
            account_type=account_type,
            is_default=is_default,
            provider_name=provider_name,
            metadata_payload=metadata_payload,
        )

    async def verify_bank_account(self, *, account_number: str, bank_code: str, account_name: str | None = None) -> dict[str, Any]:
        """Delegate bank account verification to the bank account service."""
        self._log_entry("verify_bank_account")
        return await self.bank_account_service.verify_bank_account(
            account_number=account_number,
            bank_code=bank_code,
            account_name=account_name,
        )

    async def list_bank_accounts(self, *, user_id: UUID, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        """Delegate bank account listing to the bank account service."""
        self._log_entry("list_bank_accounts", user_id=user_id)
        return await self.bank_account_service.list_bank_accounts(user_id=user_id, page=page, page_size=page_size)

    async def get_bank_account(self, *, user_id: UUID, bank_account_id: UUID) -> dict[str, Any]:
        """Delegate bank account retrieval to the bank account service."""
        self._log_entry("get_bank_account", user_id=user_id)
        return await self.bank_account_service.get_bank_account(user_id=user_id, bank_account_id=bank_account_id)

    async def set_default_bank_account(self, *, user_id: UUID, bank_account_id: UUID) -> dict[str, Any]:
        """Delegate default bank account changes to the bank account service."""
        self._log_entry("set_default_bank_account", user_id=user_id)
        return await self.bank_account_service.set_default_bank_account(user_id=user_id, bank_account_id=bank_account_id)

    async def remove_bank_account(self, *, user_id: UUID, bank_account_id: UUID) -> dict[str, Any]:
        """Delegate bank account removal to the bank account service."""
        self._log_entry("remove_bank_account", user_id=user_id)
        return await self.bank_account_service.remove_bank_account(user_id=user_id, bank_account_id=bank_account_id)

    async def restore_bank_account(self, *, user_id: UUID, bank_account_id: UUID) -> dict[str, Any]:
        """Delegate bank account restoration to the bank account service."""
        self._log_entry("restore_bank_account", user_id=user_id)
        return await self.bank_account_service.restore_bank_account(user_id=user_id, bank_account_id=bank_account_id)

    async def register_device(
        self,
        *,
        user_id: UUID,
        device_fingerprint: str,
        device_name: str | None = None,
        device_type: str = "unknown",
        operating_system: str | None = None,
        os_version: str | None = None,
        app_version: str | None = None,
        browser_name: str | None = None,
        browser_version: str | None = None,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        """Delegate device registration to the device service."""
        self._log_entry("register_device", user_id=user_id)
        return await self.device_service.register_device(
            user_id=user_id,
            device_fingerprint=device_fingerprint,
            device_name=device_name,
            device_type=device_type,
            operating_system=operating_system,
            os_version=os_version,
            app_version=app_version,
            browser_name=browser_name,
            browser_version=browser_version,
            ip_address=ip_address,
        )

    async def get_device(self, *, user_id: UUID, device_id: UUID) -> dict[str, Any]:
        """Delegate device retrieval to the device service."""
        self._log_entry("get_device", user_id=user_id)
        return await self.device_service.get_device(user_id=user_id, device_id=device_id)

    async def list_devices(self, *, user_id: UUID, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        """Delegate device listing to the device service."""
        self._log_entry("list_devices", user_id=user_id)
        return await self.device_service.list_devices(user_id=user_id, page=page, page_size=page_size)

    async def update_device(
        self,
        *,
        user_id: UUID,
        device_id: UUID,
        device_name: str | None = None,
        device_type: str | None = None,
        operating_system: str | None = None,
        os_version: str | None = None,
        app_version: str | None = None,
        browser_name: str | None = None,
        browser_version: str | None = None,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        """Delegate device updates to the device service."""
        self._log_entry("update_device", user_id=user_id)
        return await self.device_service.update_device(
            user_id=user_id,
            device_id=device_id,
            device_name=device_name,
            device_type=device_type,
            operating_system=operating_system,
            os_version=os_version,
            app_version=app_version,
            browser_name=browser_name,
            browser_version=browser_version,
            ip_address=ip_address,
        )

    async def trust_device(self, *, user_id: UUID, device_id: UUID) -> dict[str, Any]:
        """Delegate device trust changes to the device service."""
        self._log_entry("trust_device", user_id=user_id)
        return await self.device_service.trust_device(user_id=user_id, device_id=device_id)

    async def untrust_device(self, *, user_id: UUID, device_id: UUID) -> dict[str, Any]:
        """Delegate device untrust changes to the device service."""
        self._log_entry("untrust_device", user_id=user_id)
        return await self.device_service.untrust_device(user_id=user_id, device_id=device_id)

    async def revoke_device(self, *, user_id: UUID, device_id: UUID, reason: str | None = None) -> dict[str, Any]:
        """Delegate device revocation to the device service."""
        self._log_entry("revoke_device", user_id=user_id)
        return await self.device_service.revoke_device(user_id=user_id, device_id=device_id, reason=reason)

    async def delete_device(self, *, user_id: UUID, device_id: UUID) -> dict[str, Any]:
        """Delegate device deletion to the device service."""
        self._log_entry("delete_device", user_id=user_id)
        return await self.device_service.delete_device(user_id=user_id, device_id=device_id)

    async def is_trusted_device(self, *, user_id: UUID, device_id: UUID) -> bool:
        """Delegate trusted-device checks to the device service."""
        self._log_entry("is_trusted_device", user_id=user_id)
        return await self.device_service.is_trusted_device(user_id=user_id, device_id=device_id)

    def _log_entry(self, action: str, *, user_id: UUID | None = None) -> None:
        self.logger.debug("user_service_entry", extra={"action": action, "user_id": str(user_id) if user_id else None})
