from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.services.wallet import (
    WalletFundingService,
    WalletManager,
    WalletPinService,
    WalletStatementService,
    WalletTransferService,
)


class WalletService:
    """Public facade for wallet-domain workflows using injected internal services."""

    def __init__(
        self,
        *,
        wallet_manager: WalletManager,
        funding_service: WalletFundingService,
        transfer_service: WalletTransferService,
        pin_service: WalletPinService,
        statement_service: WalletStatementService,
        logger: logging.Logger | None = None,
    ) -> None:
        self.wallet_manager = wallet_manager
        self.funding_service = funding_service
        self.transfer_service = transfer_service
        self.pin_service = pin_service
        self.statement_service = statement_service
        self.logger = logger or logging.getLogger(__name__)

    async def create_wallet(
        self,
        *,
        user_id: UUID,
        wallet_type: str = "customer",
        currency: str = "NGN",
        wallet_reference: str | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Delegate wallet creation to the wallet manager."""
        self._log_entry("create_wallet", user_id=user_id)
        return await self.wallet_manager.create_wallet(
            user_id=user_id,
            wallet_type=wallet_type,
            currency=currency,
            wallet_reference=wallet_reference,
            metadata_payload=metadata_payload,
        )

    async def get_wallet(self, *, wallet_id: UUID, user_id: UUID | None = None) -> dict[str, Any]:
        """Delegate wallet lookup to the wallet manager."""
        self._log_entry("get_wallet", wallet_id=wallet_id, user_id=user_id)
        return await self.wallet_manager.get_wallet(wallet_id=wallet_id, user_id=user_id)

    async def get_wallet_by_user(self, *, user_id: UUID, wallet_type: str | None = None) -> dict[str, Any]:
        """Delegate wallet lookup by user to the wallet manager."""
        self._log_entry("get_wallet_by_user", user_id=user_id)
        return await self.wallet_manager.get_wallet_by_user(user_id=user_id, wallet_type=wallet_type)

    async def get_wallet_balance(self, *, wallet_id: UUID, user_id: UUID | None = None) -> dict[str, Any]:
        """Delegate wallet balance lookup to the wallet manager."""
        self._log_entry("get_wallet_balance", wallet_id=wallet_id, user_id=user_id)
        return await self.wallet_manager.get_wallet_balance(wallet_id=wallet_id, user_id=user_id)

    async def freeze_wallet(self, *, wallet_id: UUID, reason: str | None = None) -> dict[str, Any]:
        """Delegate wallet freeze to the wallet manager."""
        self._log_entry("freeze_wallet", wallet_id=wallet_id)
        return await self.wallet_manager.freeze_wallet(wallet_id=wallet_id, reason=reason)

    async def unfreeze_wallet(self, *, wallet_id: UUID) -> dict[str, Any]:
        """Delegate wallet unfreeze to the wallet manager."""
        self._log_entry("unfreeze_wallet", wallet_id=wallet_id)
        return await self.wallet_manager.unfreeze_wallet(wallet_id=wallet_id)

    async def suspend_wallet(self, *, wallet_id: UUID) -> dict[str, Any]:
        """Delegate wallet suspension to the wallet manager."""
        self._log_entry("suspend_wallet", wallet_id=wallet_id)
        return await self.wallet_manager.suspend_wallet(wallet_id=wallet_id)

    async def activate_wallet(self, *, wallet_id: UUID) -> dict[str, Any]:
        """Delegate wallet activation to the wallet manager."""
        self._log_entry("activate_wallet", wallet_id=wallet_id)
        return await self.wallet_manager.activate_wallet(wallet_id=wallet_id)

    async def close_wallet(self, *, wallet_id: UUID) -> dict[str, Any]:
        """Delegate wallet closure to the wallet manager."""
        self._log_entry("close_wallet", wallet_id=wallet_id)
        return await self.wallet_manager.close_wallet(wallet_id=wallet_id)

    async def reopen_wallet(self, *, wallet_id: UUID) -> dict[str, Any]:
        """Delegate wallet reopen to the wallet manager."""
        self._log_entry("reopen_wallet", wallet_id=wallet_id)
        return await self.wallet_manager.reopen_wallet(wallet_id=wallet_id)

    async def initialize_wallet_funding(
        self,
        *,
        user_id: UUID,
        wallet_id: UUID,
        amount: Decimal | float | int,
        provider_name: str = "flutterwave",
        provider_reference: str | None = None,
        currency: str = "NGN",
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Delegate funding initialization to the funding service."""
        self._log_entry("initialize_wallet_funding", user_id=user_id, wallet_id=wallet_id)
        return await self.funding_service.initialize_wallet_funding(
            user_id=user_id,
            wallet_id=wallet_id,
            amount=amount,
            provider_name=provider_name,
            provider_reference=provider_reference,
            currency=currency,
            metadata_payload=metadata_payload,
        )

    async def verify_wallet_funding(
        self,
        *,
        transaction_id: UUID | None = None,
        reference: str | None = None,
        provider_reference: str | None = None,
        provider_name: str = "flutterwave",
    ) -> dict[str, Any]:
        """Delegate funding verification to the funding service."""
        self._log_entry("verify_wallet_funding", transaction_id=transaction_id)
        return await self.funding_service.verify_wallet_funding(
            transaction_id=transaction_id,
            reference=reference,
            provider_reference=provider_reference,
            provider_name=provider_name,
        )

    async def credit_wallet(self, *, transaction_id: UUID | None = None, reference: str | None = None) -> dict[str, Any]:
        """Delegate wallet crediting to the funding service."""
        self._log_entry("credit_wallet", transaction_id=transaction_id)
        return await self.funding_service.credit_wallet(transaction_id=transaction_id, reference=reference)

    async def reverse_wallet_credit(self, *, transaction_id: UUID | None = None, reference: str | None = None, reason: str | None = None) -> dict[str, Any]:
        """Delegate wallet credit reversal to the funding service."""
        self._log_entry("reverse_wallet_credit", transaction_id=transaction_id)
        return await self.funding_service.reverse_wallet_credit(transaction_id=transaction_id, reference=reference, reason=reason)

    async def reconcile_wallet_funding(
        self,
        *,
        provider_name: str = "flutterwave",
        provider_reference: str | None = None,
        transaction_id: UUID | None = None,
        reference: str | None = None,
    ) -> dict[str, Any]:
        """Delegate funding reconciliation to the funding service."""
        self._log_entry("reconcile_wallet_funding", transaction_id=transaction_id)
        return await self.funding_service.reconcile_wallet_funding(
            provider_name=provider_name,
            provider_reference=provider_reference,
            transaction_id=transaction_id,
            reference=reference,
        )

    async def process_virtual_account_funding(
        self,
        *,
        user_id: UUID,
        wallet_id: UUID,
        amount: Decimal | float | int,
        provider_name: str = "flutterwave",
        currency: str = "NGN",
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Delegate virtual-account funding to the funding service."""
        self._log_entry("process_virtual_account_funding", user_id=user_id, wallet_id=wallet_id)
        return await self.funding_service.process_virtual_account_funding(
            user_id=user_id,
            wallet_id=wallet_id,
            amount=amount,
            provider_name=provider_name,
            currency=currency,
            metadata_payload=metadata_payload,
        )

    async def transfer_between_users(
        self,
        *,
        sender_user_id: UUID,
        sender_wallet_id: UUID,
        recipient_user_id: UUID,
        recipient_wallet_id: UUID | None = None,
        amount: Decimal | float | int,
        transaction_pin: str | None = None,
        description: str | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Delegate internal transfers to the transfer service."""
        self._log_entry("transfer_between_users", sender_user_id=sender_user_id, wallet_id=sender_wallet_id)
        return await self.transfer_service.transfer_between_users(
            sender_user_id=sender_user_id,
            sender_wallet_id=sender_wallet_id,
            recipient_user_id=recipient_user_id,
            recipient_wallet_id=recipient_wallet_id,
            amount=amount,
            transaction_pin=transaction_pin,
            description=description,
            metadata_payload=metadata_payload,
        )

    async def transfer_to_bank(
        self,
        *,
        user_id: UUID,
        wallet_id: UUID,
        amount: Decimal | float | int,
        bank_code: str,
        account_number: str,
        account_name: str | None = None,
        transaction_pin: str | None = None,
        description: str | None = None,
        metadata_payload: str | None = None,
    ) -> dict[str, Any]:
        """Delegate bank transfers to the transfer service."""
        self._log_entry("transfer_to_bank", user_id=user_id, wallet_id=wallet_id)
        return await self.transfer_service.transfer_to_bank(
            user_id=user_id,
            wallet_id=wallet_id,
            amount=amount,
            bank_code=bank_code,
            account_number=account_number,
            account_name=account_name,
            transaction_pin=transaction_pin,
            description=description,
            metadata_payload=metadata_payload,
        )

    async def validate_transfer(
        self,
        *,
        wallet_id: UUID,
        amount: Decimal | float | int,
        transaction_pin: str | None = None,
        transfer_type: str = "internal",
    ) -> dict[str, Any]:
        """Delegate transfer validation to the transfer service."""
        self._log_entry("validate_transfer", wallet_id=wallet_id)
        return await self.transfer_service.validate_transfer(
            wallet_id=wallet_id,
            amount=amount,
            transaction_pin=transaction_pin,
            transfer_type=transfer_type,
        )

    async def calculate_transfer_fee(self, *, amount: Decimal | float | int, transfer_type: str = "internal") -> Decimal:
        """Delegate transfer fee calculation to the transfer service."""
        self._log_entry("calculate_transfer_fee")
        return await self.transfer_service.calculate_transfer_fee(amount=amount, transfer_type=transfer_type)

    async def create_transaction_pin(
        self,
        *,
        user_id: UUID,
        pin: str,
        confirm_pin: str,
        require_complexity: bool = False,
    ) -> dict[str, Any]:
        """Delegate transaction PIN creation to the PIN service."""
        self._log_entry("create_transaction_pin", user_id=user_id)
        return await self.pin_service.create_transaction_pin(
            user_id=user_id,
            pin=pin,
            confirm_pin=confirm_pin,
            require_complexity=require_complexity,
        )

    async def change_transaction_pin(
        self,
        *,
        user_id: UUID,
        current_pin: str,
        new_pin: str,
        confirm_pin: str,
        require_complexity: bool = False,
    ) -> dict[str, Any]:
        """Delegate transaction PIN change to the PIN service."""
        self._log_entry("change_transaction_pin", user_id=user_id)
        return await self.pin_service.change_transaction_pin(
            user_id=user_id,
            current_pin=current_pin,
            new_pin=new_pin,
            confirm_pin=confirm_pin,
            require_complexity=require_complexity,
        )

    async def reset_transaction_pin(
        self,
        *,
        user_id: UUID,
        new_pin: str,
        confirm_pin: str,
        require_complexity: bool = False,
    ) -> dict[str, Any]:
        """Delegate transaction PIN reset to the PIN service."""
        self._log_entry("reset_transaction_pin", user_id=user_id)
        return await self.pin_service.reset_transaction_pin(
            user_id=user_id,
            new_pin=new_pin,
            confirm_pin=confirm_pin,
            require_complexity=require_complexity,
        )

    async def verify_transaction_pin(self, *, user_id: UUID, pin: str) -> bool:
        """Delegate transaction PIN verification to the PIN service."""
        self._log_entry("verify_transaction_pin", user_id=user_id)
        return await self.pin_service.verify_transaction_pin(user_id=user_id, pin=pin)

    async def get_wallet_statement(
        self,
        *,
        user_id: UUID,
        wallet_id: UUID,
        start_date: str | None = None,
        end_date: str | None = None,
        status: str | None = None,
        transaction_type: str | None = None,
        page: int = 1,
        page_size: int = 20,
        sort_by: str = "created_at",
        sort_desc: bool = True,
    ) -> dict[str, Any]:
        """Delegate statement generation to the statement service."""
        self._log_entry("get_wallet_statement", user_id=user_id, wallet_id=wallet_id)
        return await self.statement_service.get_wallet_statement(
            user_id=user_id,
            wallet_id=wallet_id,
            start_date=start_date,
            end_date=end_date,
            status=status,
            transaction_type=transaction_type,
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_desc=sort_desc,
        )

    async def get_transaction_history(
        self,
        *,
        user_id: UUID,
        wallet_id: UUID,
        page: int = 1,
        page_size: int = 20,
        sort_by: str = "created_at",
        sort_desc: bool = True,
    ) -> dict[str, Any]:
        """Delegate transaction history retrieval to the statement service."""
        self._log_entry("get_transaction_history", user_id=user_id, wallet_id=wallet_id)
        return await self.statement_service.get_transaction_history(
            user_id=user_id,
            wallet_id=wallet_id,
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_desc=sort_desc,
        )

    async def get_transaction_details(self, *, user_id: UUID, transaction_id: UUID) -> dict[str, Any]:
        """Delegate transaction detail lookup to the statement service."""
        self._log_entry("get_transaction_details", user_id=user_id, transaction_id=transaction_id)
        return await self.statement_service.get_transaction_details(user_id=user_id, transaction_id=transaction_id)

    async def export_statement(
        self,
        *,
        user_id: UUID,
        wallet_id: UUID,
        format: str = "csv",
        start_date: str | None = None,
        end_date: str | None = None,
        status: str | None = None,
        transaction_type: str | None = None,
    ) -> dict[str, Any]:
        """Delegate statement export to the statement service."""
        self._log_entry("export_statement", user_id=user_id, wallet_id=wallet_id)
        return await self.statement_service.export_statement(
            user_id=user_id,
            wallet_id=wallet_id,
            format=format,
            start_date=start_date,
            end_date=end_date,
            status=status,
            transaction_type=transaction_type,
        )

    def _log_entry(self, action: str, **context: Any) -> None:
        self.logger.debug("wallet_service_entry", extra={"action": action, **context})
