from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from app.models.ledger import Ledger
from app.repositories.ledger_repository import LedgerRepository
from app.repositories.transaction_repository import TransactionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.wallet_repository import WalletRepository
from app.utils.exceptions import DatabaseException, ValidationException


class LedgerService:
    """Orchestrates ledger entry creation, lookup, reporting, and archival."""

    def __init__(
        self,
        *,
        ledger_repository: LedgerRepository,
        wallet_repository: WalletRepository | None = None,
        user_repository: UserRepository | None = None,
        transaction_repository: TransactionRepository | None = None,
        notification_service: Any | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.ledger_repository = ledger_repository
        self.wallet_repository = wallet_repository
        self.user_repository = user_repository
        self.transaction_repository = transaction_repository
        self.notification_service = notification_service
        self.logger = logger or logging.getLogger(__name__)

    async def create_entry(
        self,
        *,
        wallet_id: UUID,
        user_id: UUID,
        transaction_reference: str,
        transaction_type: str,
        entry_type: str,
        opening_balance: Decimal | int | float | str,
        debit_amount: Decimal | int | float | str,
        credit_amount: Decimal | int | float | str,
        currency: str,
        description: str | None = None,
        related_transaction_id: UUID | None = None,
        created_by: str = "system",
        status: str = "PENDING",
        metadata: dict[str, Any] | None = None,
    ) -> Ledger:
        """Create a single immutable ledger entry with derived closing balance."""
        await self._validate_context(wallet_id=wallet_id, user_id=user_id, related_transaction_id=related_transaction_id)

        opening = self._coerce_decimal(opening_balance, "opening_balance")
        debit = self._coerce_decimal(debit_amount, "debit_amount")
        credit = self._coerce_decimal(credit_amount, "credit_amount")

        if debit < 0 or credit < 0:
            raise ValidationException(detail="Ledger amounts must be non-negative.")
        if debit == Decimal("0") and credit == Decimal("0"):
            raise ValidationException(detail="Ledger entry must include a debit or credit movement.")
        if opening < 0:
            raise ValidationException(detail="Opening balance cannot be negative.")

        currency_code = self._normalize_currency(currency)
        reference = self._normalize_reference(transaction_reference)
        entry_kind = self._normalize_entry_type(entry_type)
        transaction_kind = self._normalize_transaction_type(transaction_type)

        closing_balance = opening + credit - debit
        if closing_balance < 0:
            raise ValidationException(detail="Closing balance cannot be negative.")

        ledger = Ledger(
            ledger_id=uuid4(),
            transaction_reference=reference,
            transaction_type=transaction_kind,
            entry_type=entry_kind,
            description=description or f"{entry_kind} ledger entry",
            status=status,
            currency=currency_code,
            opening_balance=opening,
            debit_amount=debit,
            credit_amount=credit,
            closing_balance=closing_balance,
            wallet_id=wallet_id,
            user_id=user_id,
            related_transaction_id=related_transaction_id,
            created_by=created_by,
            metadata=metadata or {},
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        try:
            created_entry = await self.ledger_repository.create(ledger)
            self.logger.info(
                "ledger_entry_created",
                extra={"wallet_id": str(wallet_id), "user_id": str(user_id), "entry_type": entry_kind},
            )
            return created_entry
        except ValidationException:
            raise
        except DatabaseException:
            raise
        except Exception as exc:  # pragma: no cover - defensive guard
            self.logger.exception("Unexpected failure while creating ledger entry")
            raise DatabaseException(detail="Unable to create ledger entry.") from exc

    async def create_debit_entry(
        self,
        *,
        wallet_id: UUID,
        user_id: UUID,
        transaction_reference: str,
        transaction_type: str,
        opening_balance: Decimal | int | float | str,
        amount: Decimal | int | float | str,
        currency: str,
        description: str | None = None,
        related_transaction_id: UUID | None = None,
        created_by: str = "system",
        status: str = "PENDING",
        metadata: dict[str, Any] | None = None,
    ) -> Ledger:
        """Create a debit ledger entry."""
        return await self.create_entry(
            wallet_id=wallet_id,
            user_id=user_id,
            transaction_reference=transaction_reference,
            transaction_type=transaction_type,
            entry_type="DEBIT",
            opening_balance=opening_balance,
            debit_amount=amount,
            credit_amount=Decimal("0"),
            currency=currency,
            description=description,
            related_transaction_id=related_transaction_id,
            created_by=created_by,
            status=status,
            metadata=metadata,
        )

    async def create_credit_entry(
        self,
        *,
        wallet_id: UUID,
        user_id: UUID,
        transaction_reference: str,
        transaction_type: str,
        opening_balance: Decimal | int | float | str,
        amount: Decimal | int | float | str,
        currency: str,
        description: str | None = None,
        related_transaction_id: UUID | None = None,
        created_by: str = "system",
        status: str = "PENDING",
        metadata: dict[str, Any] | None = None,
    ) -> Ledger:
        """Create a credit ledger entry."""
        return await self.create_entry(
            wallet_id=wallet_id,
            user_id=user_id,
            transaction_reference=transaction_reference,
            transaction_type=transaction_type,
            entry_type="CREDIT",
            opening_balance=opening_balance,
            debit_amount=Decimal("0"),
            credit_amount=amount,
            currency=currency,
            description=description,
            related_transaction_id=related_transaction_id,
            created_by=created_by,
            status=status,
            metadata=metadata,
        )

    async def create_reversal_entry(
        self,
        *,
        wallet_id: UUID,
        user_id: UUID,
        transaction_reference: str,
        transaction_type: str,
        opening_balance: Decimal | int | float | str,
        amount: Decimal | int | float | str,
        currency: str,
        description: str | None = None,
        related_transaction_id: UUID | None = None,
        created_by: str = "system",
        status: str = "PENDING",
        metadata: dict[str, Any] | None = None,
    ) -> Ledger:
        """Create a reversal ledger entry for correcting a prior movement."""
        return await self.create_entry(
            wallet_id=wallet_id,
            user_id=user_id,
            transaction_reference=transaction_reference,
            transaction_type=transaction_type or "REVERSAL",
            entry_type="REVERSAL",
            opening_balance=opening_balance,
            debit_amount=amount,
            credit_amount=Decimal("0"),
            currency=currency,
            description=description or "Reversal entry",
            related_transaction_id=related_transaction_id,
            created_by=created_by,
            status=status,
            metadata=metadata,
        )

    async def create_refund_entry(
        self,
        *,
        wallet_id: UUID,
        user_id: UUID,
        transaction_reference: str,
        transaction_type: str,
        opening_balance: Decimal | int | float | str,
        amount: Decimal | int | float | str,
        currency: str,
        description: str | None = None,
        related_transaction_id: UUID | None = None,
        created_by: str = "system",
        status: str = "PENDING",
        metadata: dict[str, Any] | None = None,
    ) -> Ledger:
        """Create a refund ledger entry."""
        return await self.create_entry(
            wallet_id=wallet_id,
            user_id=user_id,
            transaction_reference=transaction_reference,
            transaction_type=transaction_type or "REFUND",
            entry_type="REFUND",
            opening_balance=opening_balance,
            debit_amount=Decimal("0"),
            credit_amount=amount,
            currency=currency,
            description=description or "Refund entry",
            related_transaction_id=related_transaction_id,
            created_by=created_by,
            status=status,
            metadata=metadata,
        )

    async def create_adjustment_entry(
        self,
        *,
        wallet_id: UUID,
        user_id: UUID,
        transaction_reference: str,
        transaction_type: str,
        opening_balance: Decimal | int | float | str,
        amount: Decimal | int | float | str,
        currency: str,
        description: str | None = None,
        related_transaction_id: UUID | None = None,
        created_by: str = "system",
        status: str = "PENDING",
        metadata: dict[str, Any] | None = None,
    ) -> Ledger:
        """Create an adjustment ledger entry."""
        return await self.create_entry(
            wallet_id=wallet_id,
            user_id=user_id,
            transaction_reference=transaction_reference,
            transaction_type=transaction_type or "ADJUSTMENT",
            entry_type="ADJUSTMENT",
            opening_balance=opening_balance,
            debit_amount=Decimal("0"),
            credit_amount=amount,
            currency=currency,
            description=description or "Adjustment entry",
            related_transaction_id=related_transaction_id,
            created_by=created_by,
            status=status,
            metadata=metadata,
        )

    async def create_fee_entry(
        self,
        *,
        wallet_id: UUID,
        user_id: UUID,
        transaction_reference: str,
        transaction_type: str,
        opening_balance: Decimal | int | float | str,
        amount: Decimal | int | float | str,
        currency: str,
        description: str | None = None,
        related_transaction_id: UUID | None = None,
        created_by: str = "system",
        status: str = "PENDING",
        metadata: dict[str, Any] | None = None,
    ) -> Ledger:
        """Create a fee ledger entry."""
        return await self.create_entry(
            wallet_id=wallet_id,
            user_id=user_id,
            transaction_reference=transaction_reference,
            transaction_type=transaction_type or "FEE",
            entry_type="FEE",
            opening_balance=opening_balance,
            debit_amount=amount,
            credit_amount=Decimal("0"),
            currency=currency,
            description=description or "Fee entry",
            related_transaction_id=related_transaction_id,
            created_by=created_by,
            status=status,
            metadata=metadata,
        )

    async def create_commission_entry(
        self,
        *,
        wallet_id: UUID,
        user_id: UUID,
        transaction_reference: str,
        transaction_type: str,
        opening_balance: Decimal | int | float | str,
        amount: Decimal | int | float | str,
        currency: str,
        description: str | None = None,
        related_transaction_id: UUID | None = None,
        created_by: str = "system",
        status: str = "PENDING",
        metadata: dict[str, Any] | None = None,
    ) -> Ledger:
        """Create a commission ledger entry."""
        return await self.create_entry(
            wallet_id=wallet_id,
            user_id=user_id,
            transaction_reference=transaction_reference,
            transaction_type=transaction_type or "COMMISSION",
            entry_type="COMMISSION",
            opening_balance=opening_balance,
            debit_amount=amount,
            credit_amount=Decimal("0"),
            currency=currency,
            description=description or "Commission entry",
            related_transaction_id=related_transaction_id,
            created_by=created_by,
            status=status,
            metadata=metadata,
        )

    async def get_entry(self, *, ledger_id: UUID) -> Ledger | None:
        """Retrieve a single ledger entry by identifier."""
        return await self.ledger_repository.get_by_id(ledger_id)

    async def get_wallet_ledger(self, *, wallet_id: UUID, page: int = 1, page_size: int = 20) -> tuple[list[Ledger], int]:
        """Retrieve paginated ledger entries for a wallet."""
        return await self.ledger_repository.get_wallet_ledger(wallet_id=wallet_id, page=page, page_size=page_size)

    async def get_user_ledger(self, *, user_id: UUID, page: int = 1, page_size: int = 20) -> tuple[list[Ledger], int]:
        """Retrieve paginated ledger entries for a user."""
        return await self.ledger_repository.get_user_ledger(user_id=user_id, page=page, page_size=page_size)

    async def get_transaction_ledger(self, *, related_transaction_id: UUID) -> list[Ledger]:
        """Retrieve all ledger entries related to a transaction."""
        return await self.ledger_repository.get_by_transaction_id(related_transaction_id)

    async def search_entries(
        self,
        *,
        query: str | None = None,
        wallet_id: UUID | None = None,
        user_id: UUID | None = None,
        transaction_reference: str | None = None,
        transaction_type: str | None = None,
        entry_type: str | None = None,
        currency: str | None = None,
        status: str | None = None,
        created_by: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Ledger], int]:
        """Search ledger entries using repository-backed filters."""
        return await self.ledger_repository.search(
            query=query,
            wallet_id=wallet_id,
            user_id=user_id,
            transaction_reference=transaction_reference,
            transaction_type=transaction_type,
            entry_type=entry_type,
            currency=currency,
            status=status,
            created_by=created_by,
            start_date=start_date,
            end_date=end_date,
            page=page,
            page_size=page_size,
        )

    async def filter_entries(
        self,
        *,
        wallet_id: UUID | None = None,
        user_id: UUID | None = None,
        related_transaction_id: UUID | None = None,
        transaction_reference: str | None = None,
        transaction_type: str | None = None,
        entry_type: str | None = None,
        currency: str | None = None,
        status: str | None = None,
        created_by: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Ledger], int]:
        """Filter ledger entries by the supported fields."""
        return await self.ledger_repository.filter(
            wallet_id=wallet_id,
            user_id=user_id,
            related_transaction_id=related_transaction_id,
            transaction_reference=transaction_reference,
            transaction_type=transaction_type,
            entry_type=entry_type,
            currency=currency,
            status=status,
            created_by=created_by,
            start_date=start_date,
            end_date=end_date,
            page=page,
            page_size=page_size,
        )

    async def paginate_entries(self, *, page: int = 1, page_size: int = 20) -> tuple[list[Ledger], int]:
        """Retrieve a paginated list of ledger entries."""
        return await self.ledger_repository.paginate(page=page, page_size=page_size)

    async def export_entries(
        self,
        *,
        wallet_id: UUID | None = None,
        user_id: UUID | None = None,
        format: str = "json",
        page: int = 1,
        page_size: int = 100,
    ) -> dict[str, Any]:
        """Prepare a serializable export payload for ledger entries."""
        entries, total = await self.ledger_repository.filter(
            wallet_id=wallet_id,
            user_id=user_id,
            page=page,
            page_size=page_size,
        )
        return {
            "format": format,
            "record_count": len(entries),
            "total_records": total,
            "entries": [self._serialize_entry(entry) for entry in entries],
        }

    async def summarize_wallet(self, *, wallet_id: UUID) -> dict[str, Any]:
        """Produce a summary for the provided wallet."""
        entries = await self.ledger_repository.get_by_wallet_id(wallet_id)
        return self._summarize_entries(entries, wallet_id=wallet_id)

    async def summarize_user(self, *, user_id: UUID) -> dict[str, Any]:
        """Produce a summary for the provided user."""
        entries = await self.ledger_repository.get_by_user_id(user_id)
        return self._summarize_entries(entries, user_id=user_id)

    async def calculate_running_balance(self, *, wallet_id: UUID) -> list[dict[str, Any]]:
        """Calculate the running balance for each ledger entry in chronological order."""
        entries = await self.ledger_repository.get_by_wallet_id(wallet_id)
        ordered_entries = sorted(entries, key=lambda item: item.created_at or datetime.utcnow())

        running_balance = Decimal("0")
        rows: list[dict[str, Any]] = []
        for entry in ordered_entries:
            running_balance = running_balance + entry.credit_amount - entry.debit_amount
            rows.append(
                {
                    "ledger_id": entry.ledger_id,
                    "transaction_reference": entry.transaction_reference,
                    "entry_type": entry.entry_type,
                    "opening_balance": entry.opening_balance,
                    "debit_amount": entry.debit_amount,
                    "credit_amount": entry.credit_amount,
                    "running_balance": running_balance,
                    "created_at": entry.created_at,
                }
            )

        return rows

    async def generate_statement(self, *, wallet_id: UUID) -> dict[str, Any]:
        """Generate a wallet statement payload with running balances."""
        entries = await self.ledger_repository.get_by_wallet_id(wallet_id)
        running_rows = await self.calculate_running_balance(wallet_id=wallet_id)
        return {
            "wallet_id": wallet_id,
            "entry_count": len(entries),
            "entries": running_rows,
        }

    async def archive_entry(self, *, ledger_id: UUID) -> Ledger | None:
        """Archive an existing ledger entry without changing the underlying balances."""
        return await self.ledger_repository.archive(ledger_id)

    async def delete_entry(self, *, ledger_id: UUID) -> None:
        """Delete an existing ledger entry by identifier."""
        await self.ledger_repository.delete(ledger_id)

    async def get_statistics(self) -> dict[str, Any]:
        """Retrieve aggregate ledger statistics from the repository."""
        return await self.ledger_repository.get_statistics()

    async def _validate_context(
        self,
        *,
        wallet_id: UUID,
        user_id: UUID,
        related_transaction_id: UUID | None,
    ) -> None:
        """Validate referenced wallet, user, and transaction identifiers."""
        if self.wallet_repository is not None:
            wallet = await self.wallet_repository.get_by_id(wallet_id)
            if wallet is None:
                raise ValidationException(detail="Wallet not found.")

        if self.user_repository is not None:
            user = await self.user_repository.get_by_id(user_id)
            if user is None:
                raise ValidationException(detail="User not found.")

        if related_transaction_id is not None and self.transaction_repository is not None:
            transaction = await self.transaction_repository.get_by_id(related_transaction_id)
            if transaction is None:
                raise ValidationException(detail="Transaction not found.")

    def _coerce_decimal(self, value: Decimal | int | float | str, field_name: str) -> Decimal:
        """Convert supported numeric inputs to Decimal without using float arithmetic."""
        if isinstance(value, Decimal):
            decimal_value = value
        else:
            try:
                decimal_value = Decimal(str(value))
            except Exception as exc:  # pragma: no cover - defensive guard
                raise ValidationException(detail=f"{field_name} must be numeric.") from exc

        if decimal_value.is_nan() or decimal_value.is_infinite():
            raise ValidationException(detail=f"{field_name} must be a finite decimal value.")
        return decimal_value

    def _normalize_currency(self, value: str) -> str:
        """Normalize the currency code into the supported upper-case form."""
        normalized = (value or "").strip().upper()
        if len(normalized) != 3 or not normalized.isalpha():
            raise ValidationException(detail="Currency must be a valid ISO code.")
        return normalized

    def _normalize_reference(self, value: str) -> str:
        """Ensure the ledger reference is present and trimmed."""
        reference = (value or "").strip()
        if not reference:
            raise ValidationException(detail="Transaction reference is required.")
        return reference

    def _normalize_entry_type(self, value: str) -> str:
        """Normalize the entry type to an upper-case string."""
        normalized = (value or "").strip().upper()
        if not normalized:
            raise ValidationException(detail="Entry type is required.")
        return normalized

    def _normalize_transaction_type(self, value: str) -> str:
        """Normalize the transaction type to an upper-case string."""
        normalized = (value or "").strip().upper()
        if not normalized:
            raise ValidationException(detail="Transaction type is required.")
        return normalized

    def _summarize_entries(self, entries: list[Ledger], *, wallet_id: UUID | None = None, user_id: UUID | None = None) -> dict[str, Any]:
        """Aggregate ledger entries into a compact summary payload."""
        total_debit = sum((entry.debit_amount for entry in entries), Decimal("0"))
        total_credit = sum((entry.credit_amount for entry in entries), Decimal("0"))
        total_opening = sum((entry.opening_balance for entry in entries), Decimal("0"))
        total_closing = sum((entry.closing_balance for entry in entries), Decimal("0"))

        return {
            "wallet_id": wallet_id,
            "user_id": user_id,
            "total_entries": len(entries),
            "total_debit_amount": total_debit,
            "total_credit_amount": total_credit,
            "total_opening_balance": total_opening,
            "total_closing_balance": total_closing,
        }

    def _serialize_entry(self, entry: Ledger) -> dict[str, Any]:
        """Convert a ledger ORM model into a serializable dictionary."""
        return {
            "ledger_id": entry.ledger_id,
            "wallet_id": entry.wallet_id,
            "user_id": entry.user_id,
            "related_transaction_id": entry.related_transaction_id,
            "transaction_reference": entry.transaction_reference,
            "transaction_type": entry.transaction_type,
            "entry_type": entry.entry_type,
            "description": entry.description,
            "currency": entry.currency,
            "status": entry.status,
            "opening_balance": entry.opening_balance,
            "debit_amount": entry.debit_amount,
            "credit_amount": entry.credit_amount,
            "closing_balance": entry.closing_balance,
            "created_by": entry.created_by,
            "metadata": entry.metadata,
            "created_at": entry.created_at,
            "updated_at": entry.updated_at,
        }
