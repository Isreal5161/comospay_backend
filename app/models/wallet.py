from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.ledger import Ledger
from app.models.transaction import Transaction
from app.models.virtual_account import VirtualAccount

if TYPE_CHECKING:
    from app.models.user import User


class Wallet(Base):
    """Persistent representation of a customer's financial wallet."""

    __tablename__ = "wallets"

    __table_args__ = (
        Index("ix_wallets_user_status", "user_id", "status"),
        Index("ix_wallets_currency_status", "currency", "status"),
        Index("ix_wallets_active", "is_active", "is_frozen"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4, index=True, doc="Unique wallet identifier.")
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        unique=True,
        index=True,
        doc="Owning user identifier.",
    )
    wallet_reference: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True, index=True, doc="External or internal wallet reference.")
    wallet_type: Mapped[str] = mapped_column(String(50), nullable=False, default="customer", index=True, doc="Wallet category.")
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="NGN", index=True, doc="ISO currency code.")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active", index=True, doc="Wallet lifecycle status.")
    available_balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0, doc="Available spendable balance.")
    ledger_balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0, doc="Ledger balance before reserves.")
    locked_balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0, doc="Locked balance for pending operations.")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True, doc="Whether the wallet is active.")
    is_frozen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True, doc="Whether the wallet is frozen.")
    is_suspended: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True, doc="Whether the wallet is suspended.")
    transaction_pin_reference: Mapped[str | None] = mapped_column(String(255), nullable=True, doc="Reference to a protected transaction PIN store.")
    metadata_payload: Mapped[str | None] = mapped_column(String(1000), nullable=True, doc="Optional non-sensitive wallet metadata.")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
        doc="Record creation timestamp.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
        doc="Record update timestamp.",
    )

    user: Mapped["User"] = relationship("User", foreign_keys=[user_id], lazy="selectin")
    virtual_accounts: Mapped[list[VirtualAccount]] = relationship(
        VirtualAccount,
        foreign_keys=VirtualAccount.wallet_id,
        back_populates="wallet",
        lazy="selectin",
    )
    ledgers: Mapped[list[Ledger]] = relationship(
        Ledger,
        foreign_keys=Ledger.wallet_id,
        back_populates="wallet",
        lazy="selectin",
    )
    transactions: Mapped[list[Transaction]] = relationship(
        Transaction,
        foreign_keys=Transaction.wallet_id,
        lazy="selectin",
    )
