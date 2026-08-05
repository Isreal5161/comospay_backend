from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.transaction import Transaction
    from app.models.user import User
    from app.models.wallet import Wallet


class Ledger(Base):
    """Immutable accounting ledger entry for wallet movements.

    Each ledger entry represents a single accounting movement affecting a wallet.
    It is the financial source of truth for reconciliation, balance history, and
    audit reporting.
    """

    __tablename__ = "ledgers"

    __table_args__ = (
        CheckConstraint("opening_balance >= 0", name="ck_ledgers_opening_balance_non_negative"),
        CheckConstraint("debit_amount >= 0", name="ck_ledgers_debit_amount_non_negative"),
        CheckConstraint("credit_amount >= 0", name="ck_ledgers_credit_amount_non_negative"),
        CheckConstraint("closing_balance >= 0", name="ck_ledgers_closing_balance_non_negative"),
        Index("ix_ledgers_wallet_created", "wallet_id", "created_at"),
        Index("ix_ledgers_user_created", "user_id", "created_at"),
        Index("ix_ledgers_reference_created", "transaction_reference", "created_at"),
        Index("ix_ledgers_related_transaction", "related_transaction_id", "created_at"),
        Index("ix_ledgers_type_status", "transaction_type", "status"),
        Index("ix_ledgers_status_created", "status", "created_at"),
        Index("ix_ledgers_currency_created", "currency", "created_at"),
    )

    ledger_id: Mapped[UUID] = mapped_column(
        primary_key=True,
        default=uuid4,
        index=True,
        doc="Unique ledger entry identifier.",
    )
    transaction_reference: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        doc="Business transaction reference associated with the accounting movement.",
    )
    transaction_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="ADMIN_ADJUSTMENT",
        index=True,
        doc="Business transaction category for the movement.",
    )
    entry_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="ADJUSTMENT",
        index=True,
        doc="Accounting entry classification such as credit, debit, refund, fee, or commission.",
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Human-readable explanation of the ledger movement.",
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="PENDING",
        index=True,
        doc="Ledger entry lifecycle status.",
    )
    currency: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="NGN",
        index=True,
        doc="ISO currency code for the ledger entry.",
    )
    opening_balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
        doc="Wallet balance before the movement.",
    )
    debit_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
        doc="Debit amount applied to the wallet.",
    )
    credit_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
        doc="Credit amount applied to the wallet.",
    )
    closing_balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
        doc="Wallet balance after the movement.",
    )
    wallet_id: Mapped[UUID] = mapped_column(
        ForeignKey("wallets.id"),
        nullable=False,
        index=True,
        doc="Wallet affected by the accounting movement.",
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True,
        doc="User associated with the ledger movement.",
    )
    related_transaction_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("transactions.id"),
        nullable=True,
        index=True,
        doc="Related business transaction identifier when available.",
    )
    created_by: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="system",
        index=True,
        doc="System, user, or admin actor that initiated the entry.",
    )
    metadata_payload: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",
        JSON,
        nullable=True,
        default=dict,
        doc="Additional accounting, provider, or audit metadata.",
    )
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
    wallet: Mapped["Wallet"] = relationship(
        "Wallet",
        foreign_keys=[wallet_id],
        back_populates="ledgers",
        lazy="selectin",
    )
    related_transaction: Mapped["Transaction | None"] = relationship(
        "Transaction",
        foreign_keys=[related_transaction_id],
        lazy="selectin",
    )

    def __getattr__(self, name: str) -> Any:
        if name == "metadata":
            return self.metadata_payload
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "metadata":
            object.__setattr__(self, "metadata_payload", value)
            return
        object.__setattr__(self, name, value)
