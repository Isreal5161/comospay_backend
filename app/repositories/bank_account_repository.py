from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bank_account import BankAccount


class BankAccountRepository:
    """Repository for database access to bank account records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_bank_account(self, bank_account: BankAccount) -> BankAccount:
        """Create and persist a new bank account record."""
        self.session.add(bank_account)
        await self.session.flush()
        await self.session.refresh(bank_account)
        return bank_account

    async def get_by_id(self, bank_account_id: UUID) -> BankAccount | None:
        """Retrieve a bank account by primary key."""
        result = await self.session.execute(select(BankAccount).where(BankAccount.id == bank_account_id))
        return result.scalar_one_or_none()

    async def get_user_accounts(
        self,
        *,
        user_id: UUID,
        page: int = 1,
        page_size: int = 20,
        order_by: str = "created_at",
        descending: bool = True,
    ) -> tuple[list[BankAccount], int]:
        """Retrieve paginated bank accounts belonging to a user."""
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 20

        query = select(BankAccount).where(BankAccount.user_id == user_id)
        count_result = await self.session.execute(query)
        total = len(count_result.scalars().all())

        order_column = getattr(BankAccount, order_by, BankAccount.created_at)
        if descending:
            order_column = order_column.desc()

        result = await self.session.execute(
            query.order_by(order_column).offset((page - 1) * page_size).limit(page_size)
        )
        accounts = list(result.scalars().all())
        return accounts, total

    async def get_by_account_number_reference(self, reference: str) -> BankAccount | None:
        """Retrieve an account by its stored provider/account reference."""
        result = await self.session.execute(
            select(BankAccount).where(BankAccount.provider_reference == reference)
        )
        return result.scalar_one_or_none()

    async def update_bank_account(self, bank_account: BankAccount, **fields: Any) -> BankAccount:
        """Update editable bank account fields in the database."""
        for field, value in fields.items():
            if hasattr(bank_account, field):
                setattr(bank_account, field, value)
        self.session.add(bank_account)
        await self.session.flush()
        await self.session.refresh(bank_account)
        return bank_account

    async def set_primary_account(self, bank_account: BankAccount) -> BankAccount:
        """Set an account as the primary/default record."""
        bank_account.is_default = True
        self.session.add(bank_account)
        await self.session.flush()
        await self.session.refresh(bank_account)
        return bank_account

    async def delete_bank_account(self, bank_account_id: UUID) -> None:
        """Delete a bank account record by primary key."""
        await self.session.execute(delete(BankAccount).where(BankAccount.id == bank_account_id))
        await self.session.flush()
