from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.virtual_account import VirtualAccount


class VirtualAccountRepository:
    """Repository for database access to virtual account records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _normalize_pagination(self, page: int, page_size: int) -> tuple[int, int]:
        """Normalize pagination values for repository queries."""
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 20
        return page, page_size

    async def create(self, virtual_account: VirtualAccount) -> VirtualAccount:
        """Create and persist a new virtual account record."""
        self.session.add(virtual_account)
        await self.session.flush()
        await self.session.refresh(virtual_account)
        return virtual_account

    async def get_by_id(self, virtual_account_id: UUID) -> VirtualAccount | None:
        """Retrieve a virtual account by primary key."""
        result = await self.session.execute(select(VirtualAccount).where(VirtualAccount.id == virtual_account_id))
        return result.scalar_one_or_none()

    async def get_by_wallet_id(self, wallet_id: UUID) -> list[VirtualAccount]:
        """Retrieve all virtual accounts for a wallet."""
        result = await self.session.execute(select(VirtualAccount).where(VirtualAccount.wallet_id == wallet_id))
        return list(result.scalars().all())

    async def get_by_user_id(self, user_id: UUID) -> list[VirtualAccount]:
        """Retrieve all virtual accounts for a user."""
        result = await self.session.execute(select(VirtualAccount).where(VirtualAccount.user_id == user_id))
        return list(result.scalars().all())

    async def get_primary_by_wallet(self, wallet_id: UUID) -> VirtualAccount | None:
        """Retrieve the primary virtual account for a wallet."""
        result = await self.session.execute(
            select(VirtualAccount).where(
                VirtualAccount.wallet_id == wallet_id,
                VirtualAccount.is_primary.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_provider_reference(self, provider_reference: str) -> VirtualAccount | None:
        """Retrieve a virtual account by provider reference."""
        result = await self.session.execute(
            select(VirtualAccount).where(VirtualAccount.provider_reference == provider_reference)
        )
        return result.scalar_one_or_none()

    async def get_by_provider_account_id(self, provider_account_id: str) -> VirtualAccount | None:
        """Retrieve a virtual account by provider account identifier."""
        result = await self.session.execute(
            select(VirtualAccount).where(VirtualAccount.provider_account_id == provider_account_id)
        )
        return result.scalar_one_or_none()

    async def get_by_account_number(self, account_number: str) -> VirtualAccount | None:
        """Retrieve a virtual account by account number."""
        result = await self.session.execute(select(VirtualAccount).where(VirtualAccount.account_number == account_number))
        return result.scalar_one_or_none()

    async def list_by_wallet(
        self,
        *,
        wallet_id: UUID,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        order_by: str = "created_at",
        descending: bool = True,
    ) -> tuple[list[VirtualAccount], int]:
        """Retrieve paginated virtual accounts for a wallet with optional filtering."""
        page, page_size = self._normalize_pagination(page, page_size)

        query = select(VirtualAccount).where(VirtualAccount.wallet_id == wallet_id)
        if status:
            query = query.where(VirtualAccount.status == status)

        count_query = select(func.count()).select_from(VirtualAccount).where(VirtualAccount.wallet_id == wallet_id)
        if status:
            count_query = count_query.where(VirtualAccount.status == status)

        count_result = await self.session.execute(count_query)
        total = int(count_result.scalar_one() or 0)

        order_column = getattr(VirtualAccount, order_by, VirtualAccount.created_at)
        if descending:
            order_column = order_column.desc()

        result = await self.session.execute(
            query.order_by(order_column).offset((page - 1) * page_size).limit(page_size)
        )
        accounts = list(result.scalars().all())
        return accounts, total

    async def list_by_user(
        self,
        *,
        user_id: UUID,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        order_by: str = "created_at",
        descending: bool = True,
    ) -> tuple[list[VirtualAccount], int]:
        """Retrieve paginated virtual accounts for a user with optional filtering."""
        page, page_size = self._normalize_pagination(page, page_size)

        query = select(VirtualAccount).where(VirtualAccount.user_id == user_id)
        if status:
            query = query.where(VirtualAccount.status == status)

        count_query = select(func.count()).select_from(VirtualAccount).where(VirtualAccount.user_id == user_id)
        if status:
            count_query = count_query.where(VirtualAccount.status == status)

        count_result = await self.session.execute(count_query)
        total = int(count_result.scalar_one() or 0)

        order_column = getattr(VirtualAccount, order_by, VirtualAccount.created_at)
        if descending:
            order_column = order_column.desc()

        result = await self.session.execute(
            query.order_by(order_column).offset((page - 1) * page_size).limit(page_size)
        )
        accounts = list(result.scalars().all())
        return accounts, total

    async def list_by_provider(
        self,
        *,
        provider: str,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        order_by: str = "created_at",
        descending: bool = True,
    ) -> tuple[list[VirtualAccount], int]:
        """Retrieve paginated virtual accounts for a provider with optional filtering."""
        page, page_size = self._normalize_pagination(page, page_size)

        query = select(VirtualAccount).where(VirtualAccount.provider == provider)
        if status:
            query = query.where(VirtualAccount.status == status)

        count_query = select(func.count()).select_from(VirtualAccount).where(VirtualAccount.provider == provider)
        if status:
            count_query = count_query.where(VirtualAccount.status == status)

        count_result = await self.session.execute(count_query)
        total = int(count_result.scalar_one() or 0)

        order_column = getattr(VirtualAccount, order_by, VirtualAccount.created_at)
        if descending:
            order_column = order_column.desc()

        result = await self.session.execute(
            query.order_by(order_column).offset((page - 1) * page_size).limit(page_size)
        )
        accounts = list(result.scalars().all())
        return accounts, total

    async def update(self, virtual_account: VirtualAccount, **fields: Any) -> VirtualAccount:
        """Update editable virtual account fields in the database."""
        for field, value in fields.items():
            if hasattr(virtual_account, field):
                setattr(virtual_account, field, value)
        self.session.add(virtual_account)
        await self.session.flush()
        await self.session.refresh(virtual_account)
        return virtual_account

    async def update_status(self, virtual_account: VirtualAccount, status: str) -> VirtualAccount:
        """Persist a new lifecycle status for a virtual account."""
        virtual_account.status = status
        self.session.add(virtual_account)
        await self.session.flush()
        await self.session.refresh(virtual_account)
        return virtual_account

    async def set_primary(self, virtual_account: VirtualAccount) -> VirtualAccount:
        """Persist a virtual account as the primary account for its wallet."""
        virtual_account.is_primary = True
        self.session.add(virtual_account)
        await self.session.flush()
        await self.session.refresh(virtual_account)
        return virtual_account

    async def unset_primary(self, virtual_account: VirtualAccount) -> VirtualAccount:
        """Remove the primary flag from a virtual account."""
        virtual_account.is_primary = False
        self.session.add(virtual_account)
        await self.session.flush()
        await self.session.refresh(virtual_account)
        return virtual_account

    async def activate(self, virtual_account: VirtualAccount) -> VirtualAccount:
        """Persist an active state for a virtual account."""
        virtual_account.is_active = True
        virtual_account.status = "ACTIVE"
        self.session.add(virtual_account)
        await self.session.flush()
        await self.session.refresh(virtual_account)
        return virtual_account

    async def suspend(self, virtual_account: VirtualAccount) -> VirtualAccount:
        """Persist a suspended state for a virtual account."""
        virtual_account.is_active = False
        virtual_account.status = "SUSPENDED"
        self.session.add(virtual_account)
        await self.session.flush()
        await self.session.refresh(virtual_account)
        return virtual_account

    async def close(self, virtual_account: VirtualAccount) -> VirtualAccount:
        """Persist a closed state for a virtual account."""
        virtual_account.is_active = False
        virtual_account.status = "CLOSED"
        self.session.add(virtual_account)
        await self.session.flush()
        await self.session.refresh(virtual_account)
        return virtual_account

    async def delete(self, virtual_account_id: UUID) -> None:
        """Delete a virtual account record by primary key."""
        await self.session.execute(delete(VirtualAccount).where(VirtualAccount.id == virtual_account_id))
        await self.session.flush()

    async def exists(self, *, wallet_id: UUID | None = None, account_number: str | None = None) -> bool:
        """Check whether a virtual account already exists for a wallet or account number."""
        query = select(VirtualAccount.id)
        if wallet_id is not None:
            query = query.where(VirtualAccount.wallet_id == wallet_id)
        if account_number is not None:
            query = query.where(VirtualAccount.account_number == account_number)

        result = await self.session.execute(query.limit(1))
        return result.scalar_one_or_none() is not None

    async def count_by_wallet(self, wallet_id: UUID) -> int:
        """Count virtual accounts for a wallet."""
        result = await self.session.execute(
            select(func.count(VirtualAccount.id)).where(VirtualAccount.wallet_id == wallet_id)
        )
        return int(result.scalar_one() or 0)

    async def count_by_provider(self, provider: str) -> int:
        """Count virtual accounts for a provider."""
        result = await self.session.execute(select(func.count(VirtualAccount.id)).where(VirtualAccount.provider == provider))
        return int(result.scalar_one() or 0)

    async def list_retryable_accounts(self, *, limit: int = 100) -> list[VirtualAccount]:
        """List virtual accounts that are scheduled for retry now or overdue.

        Criteria:
        - status in PENDING or PROVISIONING or FAILED
        - next_retry_at is null or <= now
        - retry_count is below a caller-managed max (caller filters later if needed)
        """
        from sqlalchemy import or_, and_
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        query = select(VirtualAccount).where(
            and_(
                or_(VirtualAccount.status == "PENDING", VirtualAccount.status == "PROVISIONING", VirtualAccount.status == "FAILED"),
                or_(VirtualAccount.next_retry_at.is_(None), VirtualAccount.next_retry_at <= now),
            )
        ).limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())
