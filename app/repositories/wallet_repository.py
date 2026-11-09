from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import String, cast, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
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

    async def get_dashboard_balances_by_currency(self) -> list[tuple[str, Decimal, Decimal, Decimal]]:
        """Sum wallet balance fields by currency, including frozen or inactive wallets."""
        result = await self.session.execute(
            select(
                Wallet.currency,
                func.coalesce(func.sum(Wallet.ledger_balance), Decimal("0.00")),
                func.coalesce(func.sum(Wallet.available_balance), Decimal("0.00")),
                func.coalesce(func.sum(Wallet.locked_balance), Decimal("0.00")),
            )
            .group_by(Wallet.currency)
            .order_by(Wallet.currency)
        )
        return [
            (
                currency,
                ledger_balance or Decimal("0.00"),
                available_balance or Decimal("0.00"),
                locked_balance or Decimal("0.00"),
            )
            for currency, ledger_balance, available_balance, locked_balance in result.all()
        ]

    async def list_wallets(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        wallet_type: str | None = None,
        currency: str | None = None,
        query_text: str | None = None,
        order_by: str = "created_at",
        descending: bool = True,
    ) -> tuple[list[Wallet], int]:
        """Retrieve paginated wallets with optional filters and search text."""
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 20

        query = select(Wallet)
        count_query = select(func.count(Wallet.id))

        query, count_query = self._apply_wallet_filters(
            query,
            count_query,
            status=status,
            wallet_type=wallet_type,
            currency=currency,
            query_text=query_text,
        )

        total_result = await self.session.execute(count_query)
        total = int(total_result.scalar_one() or 0)

        order_column = getattr(Wallet, order_by, Wallet.created_at)
        if descending:
            order_column = order_column.desc()

        result = await self.session.execute(
            query.order_by(order_column).offset((page - 1) * page_size).limit(page_size)
        )
        return list(result.scalars().all()), total

    async def get_wallet_directory_metadata(self) -> tuple[int, list[str]]:
        """Return global wallet count and distinct currencies for directory controls."""
        count_result = await self.session.execute(select(func.count(Wallet.id)))
        currencies_result = await self.session.execute(
            select(Wallet.currency).distinct().order_by(Wallet.currency)
        )
        return int(count_result.scalar_one() or 0), list(currencies_result.scalars().all())

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

    def _apply_wallet_filters(
        self,
        query,
        count_query,
        *,
        status: str | None,
        wallet_type: str | None,
        currency: str | None,
        query_text: str | None,
    ):
        if status:
            query = query.where(Wallet.status == status)
            count_query = count_query.where(Wallet.status == status)
        if wallet_type:
            query = query.where(Wallet.wallet_type == wallet_type)
            count_query = count_query.where(Wallet.wallet_type == wallet_type)
        if currency:
            normalized_currency = currency.upper()
            query = query.where(Wallet.currency == normalized_currency)
            count_query = count_query.where(Wallet.currency == normalized_currency)
        if query_text:
            pattern = f"%{query_text}%"
            wallet_id_filter = cast(Wallet.id, String).ilike(pattern)
            compact_query = query_text.replace("-", "").strip()
            if compact_query:
                wallet_id_filter = or_(
                    wallet_id_filter,
                    func.replace(cast(Wallet.id, String), "-", "").ilike(f"%{compact_query}%"),
                )
            user_id_filter = cast(User.id, String).ilike(pattern)
            if compact_query:
                user_id_filter = or_(
                    user_id_filter,
                    func.replace(cast(User.id, String), "-", "").ilike(f"%{compact_query}%"),
                )
            user_search_filter = Wallet.user.has(
                or_(
                    user_id_filter,
                    User.first_name.ilike(pattern),
                    User.last_name.ilike(pattern),
                    User.username.ilike(pattern),
                    User.email.ilike(pattern),
                    User.phone.ilike(pattern),
                    (
                        func.coalesce(User.first_name, "")
                        + " "
                        + func.coalesce(User.last_name, "")
                    ).ilike(pattern),
                )
            )
            search_filter = or_(
                Wallet.wallet_reference.ilike(pattern),
                wallet_id_filter,
                Wallet.status.ilike(pattern),
                Wallet.wallet_type.ilike(pattern),
                user_search_filter,
            )
            query = query.where(search_filter)
            count_query = count_query.where(search_filter)
        return query, count_query
