from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.wallet import Wallet


class WalletRepository:
    """Repository for database access to wallet records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_wallet(self, wallet: Wallet) -> Wallet:
        """Create and persist a new wallet record."""
        self.session.add(wallet)
        await self.session.flush()
        await self.session.refresh(wallet)
        return wallet

    async def get_by_id(self, wallet_id: UUID) -> Wallet | None:
        """Retrieve a wallet by primary key."""
        result = await self.session.execute(select(Wallet).where(Wallet.id == wallet_id))
        return result.scalar_one_or_none()

    async def get_by_id_for_update(self, wallet_id: UUID) -> Wallet | None:
        """Retrieve a wallet by primary key while acquiring a row lock."""
        result = await self.session.execute(select(Wallet).where(Wallet.id == wallet_id).with_for_update())
        return result.scalar_one_or_none()

    async def get_user_wallet(
        self,
        *,
        user_id: UUID,
        wallet_type: str | None = None,
    ) -> Wallet | None:
        """Retrieve a wallet by user and optional wallet type."""
        query = select(Wallet).where(Wallet.user_id == user_id)
        if wallet_type:
            query = query.where(Wallet.wallet_type == wallet_type)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_user_wallet_for_update(
        self,
        *,
        user_id: UUID,
        wallet_type: str | None = None,
    ) -> Wallet | None:
        """Retrieve a wallet by user while acquiring a row lock for transactional consistency."""
        query = select(Wallet).where(Wallet.user_id == user_id)
        if wallet_type:
            query = query.where(Wallet.wallet_type == wallet_type)
        result = await self.session.execute(query.with_for_update())
        return result.scalar_one_or_none()

    async def get_wallet_by_reference(self, wallet_reference: str) -> Wallet | None:
        """Retrieve a wallet by wallet reference."""
        result = await self.session.execute(select(Wallet).where(Wallet.wallet_reference == wallet_reference))
        return result.scalar_one_or_none()

    async def get_wallet_by_reference_for_update(self, wallet_reference: str) -> Wallet | None:
        """Retrieve a wallet by reference while acquiring a row lock for transactional consistency."""
        result = await self.session.execute(select(Wallet).where(Wallet.wallet_reference == wallet_reference).with_for_update())
        return result.scalar_one_or_none()

    async def update_wallet(self, wallet: Wallet, **fields: Any) -> Wallet:
        """Update editable wallet fields in the database."""
        for field, value in fields.items():
            if hasattr(wallet, field):
                setattr(wallet, field, value)
        self.session.add(wallet)
        await self.session.flush()
        await self.session.refresh(wallet)
        return wallet

    async def update_balance_fields(
        self,
        wallet: Wallet,
        *,
        available_balance: Decimal | None = None,
        ledger_balance: Decimal | None = None,
        locked_balance: Decimal | None = None,
    ) -> Wallet:
        """Persist balance-related wallet fields without applying financial rules."""
        if available_balance is not None:
            wallet.available_balance = available_balance
        if ledger_balance is not None:
            wallet.ledger_balance = ledger_balance
        if locked_balance is not None:
            wallet.locked_balance = locked_balance
        self.session.add(wallet)
        await self.session.flush()
        await self.session.refresh(wallet)
        return wallet

    async def freeze_wallet(self, wallet: Wallet, *, reason: str | None = None) -> Wallet:
        """Update the wallet record to a frozen state."""
        wallet.is_frozen = True
        wallet.status = "frozen"
        if reason is not None:
            wallet.metadata_payload = reason if wallet.metadata_payload is None else wallet.metadata_payload
        self.session.add(wallet)
        await self.session.flush()
        await self.session.refresh(wallet)
        return wallet

    async def delete_wallet(self, wallet_id: UUID) -> None:
        """Delete a wallet record by primary key."""
        await self.session.execute(delete(Wallet).where(Wallet.id == wallet_id))
        await self.session.flush()
