from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import String, case, cast, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction
from app.models.user import User
from app.models.ledger import Ledger
from app.utils.exceptions import DuplicateProviderReferenceException


class TransactionRepository:
    """Repository for database access to transaction records."""

    _CASE_INSENSITIVE_SUCCESS_STATUSES = ("completed", "posted", "successful", "succeeded")

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @classmethod
    def _dashboard_success_status(cls):
        return or_(
            func.lower(Transaction.status).in_(cls._CASE_INSENSITIVE_SUCCESS_STATUSES),
            Transaction.status == "settled",
        )

    async def get_dashboard_counts(self) -> dict[str, int]:
        """Return persisted transaction counts by dashboard lifecycle status."""
        result = await self.session.execute(
            select(
                func.count(Transaction.id).label("total_transactions"),
                func.count(
                    case((Transaction.status == "pending", Transaction.id))
                ).label("pending_transactions"),
                func.count(
                    case((Transaction.status == "failed", Transaction.id))
                ).label("failed_transactions"),
            )
        )
        row = result.one()
        return {
            "total_transactions": int(row.total_transactions or 0),
            "pending_transactions": int(row.pending_transactions or 0),
            "failed_transactions": int(row.failed_transactions or 0),
        }

    async def get_successful_volume_by_currency(self) -> list[tuple[str, Decimal]]:
        """Sum successful transaction amounts by currency using reconciliation statuses."""
        successful_status = self._dashboard_success_status()
        result = await self.session.execute(
            select(
                Transaction.currency,
                func.coalesce(func.sum(Transaction.amount), Decimal("0.00")),
            )
            .where(successful_status, Transaction.amount >= Decimal("0.00"))
            .group_by(Transaction.currency)
            .order_by(Transaction.currency)
        )
        return [(currency, amount or Decimal("0.00")) for currency, amount in result.all()]

    async def get_dashboard_category_breakdown(self) -> list[tuple[str, str | None, str, int, Decimal]]:
        """Aggregate raw transaction category/provider volume by currency."""
        successful_status = self._dashboard_success_status()
        result = await self.session.execute(
            select(
                Transaction.category,
                Transaction.provider_name,
                Transaction.currency,
                func.count(Transaction.id),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                successful_status & (Transaction.amount >= Decimal("0.00")),
                                Transaction.amount,
                            ),
                            else_=Decimal("0.00"),
                        )
                    ),
                    Decimal("0.00"),
                ),
            )
            .group_by(
                Transaction.category,
                Transaction.provider_name,
                Transaction.currency,
            )
            .order_by(
                Transaction.category,
                Transaction.provider_name,
                Transaction.currency,
            )
        )
        return [
            (category, provider, currency, int(count or 0), amount or Decimal("0.00"))
            for category, provider, currency, count, amount in result.all()
        ]

    async def get_dashboard_analytics_by_bucket(
        self,
        buckets: list[tuple[str, datetime, datetime]],
    ) -> list[tuple[str, str, int, Decimal]]:
        """Aggregate transaction counts and successful volume by bucket and currency."""
        if not buckets:
            return []

        successful_status = self._dashboard_success_status()
        bucket_case = case(
            *[
                ((Transaction.created_at >= start) & (Transaction.created_at < end), label)
                for label, start, end in buckets
            ],
            else_=None,
        )
        result = await self.session.execute(
            select(
                bucket_case.label("bucket"),
                Transaction.currency,
                func.count(Transaction.id),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                successful_status & (Transaction.amount >= Decimal("0.00")),
                                Transaction.amount,
                            ),
                            else_=Decimal("0.00"),
                        )
                    ),
                    Decimal("0.00"),
                ),
            )
            .where(
                Transaction.created_at >= buckets[0][1],
                Transaction.created_at < buckets[-1][2],
            )
            .group_by(bucket_case, Transaction.currency)
        )
        return [
            (str(bucket), currency, int(count or 0), amount or Decimal("0.00"))
            for bucket, currency, count, amount in result.all()
            if bucket is not None
        ]

    @staticmethod
    def _is_provider_reference_unique_violation(exc: IntegrityError) -> bool:
        """Check whether an IntegrityError is specifically caused by the provider-scoped transaction constraint."""
        orig = getattr(exc, "orig", None)
        if orig is None:
            return False

        diag = getattr(orig, "diag", None)
        if diag is not None and getattr(diag, "constraint_name", None):
            return diag.constraint_name == "uq_transactions_provider_ref"

        text = str(orig).lower()
        if "uq_transactions_provider_ref" in text:
            return True
        if "transactions.provider_name, transactions.provider_reference" in text:
            return True
        if "unique constraint failed: transactions.provider_name, transactions.provider_reference" in text:
            return True
        return False

    async def create_transaction(self, transaction: Transaction) -> Transaction:
        """Create and persist a new transaction record."""
        try:
            self.session.add(transaction)
            await self.session.flush()
            await self.session.refresh(transaction)
            return transaction
        except IntegrityError as exc:
            await self.session.rollback()
            if self._is_provider_reference_unique_violation(exc):
                raise DuplicateProviderReferenceException(
                    detail=(
                        f"Duplicate provider reference '{transaction.provider_reference}' "
                        f"for provider '{transaction.provider_name}' detected."
                    )
                ) from exc
            raise

    async def get_by_id(self, transaction_id: UUID) -> Transaction | None:
        """Retrieve a transaction by primary key."""
        result = await self.session.execute(select(Transaction).where(Transaction.id == transaction_id))
        return result.scalar_one_or_none()

    async def get_admin_transaction_details(self, transaction_id: UUID) -> tuple[Transaction, User] | None:
        """Retrieve an admin transaction together with its safe display owner fields."""
        result = await self.session.execute(
            select(Transaction, User)
            .join(User, User.id == Transaction.user_id)
            .where(Transaction.id == transaction_id)
        )
        return result.one_or_none()

    async def get_by_id_for_update(self, transaction_id: UUID) -> Transaction | None:
        """Retrieve a transaction by primary key while acquiring a row lock."""
        result = await self.session.execute(select(Transaction).where(Transaction.id == transaction_id).with_for_update())
        return result.scalar_one_or_none()

    async def get_by_reference(self, reference: str) -> Transaction | None:
        """Retrieve a transaction by its unique reference."""
        result = await self.session.execute(select(Transaction).where(Transaction.reference == reference))
        return result.scalar_one_or_none()

    async def get_by_reference_for_update(self, reference: str) -> Transaction | None:
        """Retrieve a transaction by reference while acquiring a row lock."""
        result = await self.session.execute(select(Transaction).where(Transaction.reference == reference).with_for_update())
        return result.scalar_one_or_none()

    async def get_by_provider_reference(self, *, provider_name: str | None, provider_reference: str | None) -> Transaction | None:
        """Retrieve a transaction by the provider-scoped reference used in webhook callbacks."""
        if not provider_reference or not isinstance(provider_reference, str):
            return None
        query = select(Transaction).where(Transaction.provider_reference == provider_reference)
        if provider_name is not None:
            query = query.where(Transaction.provider_name == provider_name)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_user_transactions(
        self,
        *,
        user_id: UUID,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        category: str | None = None,
        order_by: str = "created_at",
        descending: bool = True,
    ) -> tuple[list[Transaction], int]:
        """Retrieve paginated transaction history for a user with optional filtering."""
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 20

        query = select(Transaction).where(Transaction.user_id == user_id)
        if status:
            query = query.where(Transaction.status == status)
        if category:
            query = query.where(Transaction.category == category)

        count_result = await self.session.execute(select(func.count(Transaction.id)).select_from(query.subquery()))
        total = int(count_result.scalar_one() or 0)

        order_column = getattr(Transaction, order_by, Transaction.created_at)
        if descending:
            order_column = order_column.desc()

        result = await self.session.execute(
            query.order_by(order_column).offset((page - 1) * page_size).limit(page_size)
        )
        transactions = list(result.scalars().all())
        return transactions, total

    async def get_transactions_by_status(
        self,
        *,
        status: str,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Transaction], int]:
        """Retrieve transactions filtered by lifecycle status."""
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 20

        query = select(Transaction).where(Transaction.status == status)
        count_result = await self.session.execute(select(func.count(Transaction.id)).where(Transaction.status == status))
        total = int(count_result.scalar_one() or 0)

        result = await self.session.execute(
            query.order_by(Transaction.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        )
        transactions = list(result.scalars().all())
        return transactions, total

    async def get_admin_transactions(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        category: str | None = None,
        categories: list[str] | tuple[str, ...] | None = None,
        transaction_type: str | None = None,
        reference: str | None = None,
        user_id: str | None = None,
        currency: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        search: str | None = None,
        provider: str | None = None,
        method: str | None = None,
        order_by: str = "created_at",
        descending: bool = True,
        include_user: bool = False,
    ) -> tuple[list[Any], int]:
        """Retrieve admin-facing transaction listing with filtering, sorting, and pagination."""
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 20
        if page_size > 100:
            page_size = 100

        filters = self._admin_transaction_filters(
            status=status,
            category=category,
            categories=categories,
            transaction_type=transaction_type,
            reference=reference,
            user_id=user_id,
            currency=currency,
            start_date=start_date,
            end_date=end_date,
            search=search,
            provider=provider,
            method=method,
        )
        query = select(Transaction).join(User, User.id == Transaction.user_id)
        if include_user:
            query = select(Transaction, User).join(User, User.id == Transaction.user_id)
        query = query.where(*filters)
        count_query = select(func.count(Transaction.id)).select_from(Transaction).join(
            User, User.id == Transaction.user_id
        ).where(*filters)

        order_mapping = {
            "created_at": Transaction.created_at,
            "updated_at": Transaction.updated_at,
            "status": Transaction.status,
            "amount": Transaction.amount,
            "category": Transaction.category,
            "transaction_type": Transaction.transaction_type,
            "currency": Transaction.currency,
            "reference": Transaction.reference,
        }
        order_column = order_mapping.get(order_by)
        if order_column is None:
            raise ValueError(f"Unsupported transaction sort field: {order_by}")
        if descending:
            order_column = order_column.desc()

        total_result = await self.session.execute(count_query)
        total = int(total_result.scalar_one() or 0)

        result = await self.session.execute(
            query.order_by(order_column, Transaction.id).offset((page - 1) * page_size).limit(page_size)
        )
        return (list(result.all()) if include_user else list(result.scalars().all())), total

    async def get_admin_transaction_summary(self, **filters: Any) -> dict[str, int]:
        """Return aggregate counts for the same filtered transaction set as the admin list."""
        query_filters = self._admin_transaction_filters(**filters)
        status = func.lower(Transaction.status)
        result = await self.session.execute(
            select(
                func.count(Transaction.id).label("total"),
                func.count(case((status.in_(self._CASE_INSENSITIVE_SUCCESS_STATUSES + ("settled",)), Transaction.id))).label("successful"),
                func.count(case((status.in_(("pending", "processing", "funds_reserved")), Transaction.id))).label("pending"),
                func.count(case((status == "failed", Transaction.id))).label("failed"),
            )
            .select_from(Transaction)
            .join(User, User.id == Transaction.user_id)
            .where(*query_filters)
        )
        row = result.one()
        return {
            "total": int(row.total or 0),
            "successful": int(row.successful or 0),
            "pending": int(row.pending or 0),
            "failed": int(row.failed or 0),
        }

    async def get_finance_aggregates(
        self,
        *,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        category: str | None = None,
        provider: str | None = None,
        currency: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Aggregate completed transaction principal and posted wallet adjustments."""
        transaction_filters: list[Any] = [
            func.lower(Transaction.status).in_(self._CASE_INSENSITIVE_SUCCESS_STATUSES + ("settled",)),
            ~func.lower(Transaction.category).like("%refund%"),
            ~func.lower(Transaction.category).like("%reversal%"),
            ~func.lower(Transaction.transaction_type).like("%refund%"),
            ~func.lower(Transaction.transaction_type).like("%reversal%"),
        ]
        if start_date is not None:
            transaction_filters.append(Transaction.created_at >= start_date)
        if end_date is not None:
            transaction_filters.append(Transaction.created_at <= end_date)
        if category:
            transaction_filters.append(func.lower(Transaction.category) == category.strip().lower())
        if provider:
            transaction_filters.append(func.lower(Transaction.provider_name) == provider.strip().lower())
        if currency:
            transaction_filters.append(func.upper(Transaction.currency) == currency.strip().upper())

        period_expression = func.date(Transaction.created_at)
        if self.session.bind is not None and self.session.bind.dialect.name == "postgresql":
            period_expression = func.date(func.timezone("UTC", Transaction.created_at))
        result = await self.session.execute(
            select(
                Transaction.currency,
                Transaction.category,
                Transaction.transaction_type,
                Transaction.provider_name,
                period_expression.label("period"),
                func.sum(Transaction.amount).label("amount"),
                func.sum(Transaction.charges).label("charges"),
                func.count(Transaction.id).label("count"),
            )
            .where(*transaction_filters)
            .group_by(
                Transaction.currency,
                Transaction.category,
                Transaction.transaction_type,
                Transaction.provider_name,
                period_expression,
            )
        )
        transaction_rows = [
            {
                "currency": row.currency,
                "category": row.category,
                "transaction_type": row.transaction_type,
                "provider_name": row.provider_name,
                "period": str(row.period),
                "amount": row.amount or Decimal("0"),
                "charges": row.charges or Decimal("0"),
                "count": int(row.count or 0),
            }
            for row in result.all()
        ]

        status_filters: list[Any] = []
        if start_date is not None:
            status_filters.append(Transaction.created_at >= start_date)
        if end_date is not None:
            status_filters.append(Transaction.created_at <= end_date)
        if category:
            status_filters.append(func.lower(Transaction.category) == category.strip().lower())
        if provider:
            status_filters.append(func.lower(Transaction.provider_name) == provider.strip().lower())
        if currency:
            status_filters.append(func.upper(Transaction.currency) == currency.strip().upper())
        status_result = await self.session.execute(
            select(
                Transaction.currency,
                func.lower(Transaction.status).label("status"),
                func.count(Transaction.id).label("count"),
            )
            .where(*status_filters)
            .group_by(Transaction.currency, func.lower(Transaction.status))
        )
        status_rows = [
            {"currency": row.currency, "status": row.status, "count": int(row.count or 0)}
            for row in status_result.all()
        ]

        adjustment_filters: list[Any] = [
            func.lower(Ledger.status).in_(self._CASE_INSENSITIVE_SUCCESS_STATUSES + ("settled",)),
            or_(
                func.lower(Ledger.entry_type).in_(("refund", "reversal")),
                func.lower(Ledger.transaction_type).like("%refund%"),
                func.lower(Ledger.transaction_type).like("%reversal%"),
            ),
        ]
        if start_date is not None:
            adjustment_filters.append(Ledger.created_at >= start_date)
        if end_date is not None:
            adjustment_filters.append(Ledger.created_at <= end_date)

        adjustment_result = await self.session.execute(
            select(
                Ledger.currency,
                Ledger.entry_type,
                Ledger.transaction_type,
                func.sum(Ledger.credit_amount).label("credits"),
                func.sum(Ledger.debit_amount).label("debits"),
            )
            .where(*adjustment_filters)
            .group_by(Ledger.currency, Ledger.entry_type, Ledger.transaction_type)
        )
        adjustment_rows = [
            {
                "currency": row.currency,
                "entry_type": row.entry_type,
                "transaction_type": row.transaction_type,
                "credits": row.credits or Decimal("0"),
                "debits": row.debits or Decimal("0"),
            }
            for row in adjustment_result.all()
        ]
        activity_filters: list[Any] = []
        if start_date is not None:
            activity_filters.append(Transaction.created_at >= start_date)
        if end_date is not None:
            activity_filters.append(Transaction.created_at <= end_date)
        category_result = await self.session.execute(
            select(func.distinct(Transaction.category)).where(*activity_filters).order_by(Transaction.category)
        )
        currency_result = await self.session.execute(
            select(func.distinct(Transaction.currency)).where(*activity_filters).order_by(Transaction.currency)
        )
        return {
            "transactions": transaction_rows,
            "transaction_status_counts": status_rows,
            "adjustments": adjustment_rows,
            "available_categories": [row[0] for row in category_result.all()],
            "available_currencies": [row[0] for row in currency_result.all()],
        }

    async def get_finance_activity(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        search: str | None = None,
        category: str | None = None,
        status: str | None = None,
        provider: str | None = None,
        currency: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> tuple[list[Transaction], int]:
        return await self.get_admin_transactions(
            page=page,
            page_size=page_size,
            search=search,
            category=category,
            status=status,
            provider=provider,
            currency=currency,
            start_date=start_date,
            end_date=end_date,
            include_user=False,
        )

    async def get_finance_transaction(self, transaction_id: UUID) -> tuple[Transaction | None, list[Ledger]]:
        transaction_result = await self.session.execute(
            select(Transaction).where(Transaction.id == transaction_id)
        )
        transaction = transaction_result.scalar_one_or_none()
        if transaction is None:
            return None, []
        ledger_result = await self.session.execute(
            select(Ledger)
            .where(
                or_(
                    Ledger.related_transaction_id == transaction.id,
                    Ledger.transaction_reference == transaction.reference,
                )
            )
            .order_by(Ledger.created_at, Ledger.ledger_id)
        )
        return transaction, list(ledger_result.scalars().all())

    @staticmethod
    def _admin_transaction_filters(
        *,
        status: str | None = None,
        category: str | None = None,
        categories: list[str] | tuple[str, ...] | None = None,
        transaction_type: str | None = None,
        reference: str | None = None,
        user_id: str | None = None,
        currency: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        search: str | None = None,
        provider: str | None = None,
        method: str | None = None,
    ) -> list[Any]:
        filters: list[Any] = []
        if status:
            filters.append(func.lower(Transaction.status) == status.lower())
        if category:
            filters.append(func.lower(Transaction.category) == category.lower())
        if categories:
            filters.append(func.lower(Transaction.category).in_([value.lower() for value in categories]))
        if transaction_type:
            filters.append(func.lower(Transaction.transaction_type) == transaction_type.lower())
        if reference:
            filters.append(Transaction.reference == reference)
        if user_id:
            filters.append(Transaction.user_id == UUID(user_id))
        if currency:
            filters.append(Transaction.currency == currency.upper())
        if start_date is not None:
            filters.append(Transaction.created_at >= start_date)
        if end_date is not None:
            filters.append(Transaction.created_at <= end_date)
        if provider:
            filters.append(func.lower(Transaction.provider_name) == provider.lower())
        if method:
            escaped_method = method.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            method_pattern = f'%{escaped_method}%'
            filters.append(
                or_(
                    func.lower(Transaction.provider_name) == method.lower(),
                    Transaction.metadata_payload.ilike(method_pattern, escape="\\"),
                )
            )
        if search and search.strip():
            search_term = search.strip()
            escaped_search = search_term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped_search}%"
            search_conditions = [
                Transaction.reference.ilike(pattern, escape="\\"),
                cast(Transaction.id, String).ilike(pattern, escape="\\"),
                Transaction.provider_reference.ilike(pattern, escape="\\"),
                Transaction.external_reference.ilike(pattern, escape="\\"),
                User.first_name.ilike(pattern, escape="\\"),
                User.last_name.ilike(pattern, escape="\\"),
                User.email.ilike(pattern, escape="\\"),
            ]
            try:
                search_conditions.append(Transaction.id == UUID(search_term))
            except ValueError:
                pass
            filters.append(
                or_(*search_conditions)
            )
        return filters

    async def update_transaction(self, transaction: Transaction, **fields: Any) -> Transaction:
        """Update editable transaction fields in the database."""
        try:
            for field, value in fields.items():
                if hasattr(transaction, field):
                    setattr(transaction, field, value)
            self.session.add(transaction)
            await self.session.flush()
            await self.session.refresh(transaction)
            return transaction
        except IntegrityError as exc:
            await self.session.rollback()
            if self._is_provider_reference_unique_violation(exc):
                raise DuplicateProviderReferenceException(
                    detail=(
                        f"Duplicate provider reference '{transaction.provider_reference}' "
                        f"for provider '{transaction.provider_name}' detected."
                    )
                ) from exc
            raise

    async def count_user_transactions(self, user_id: UUID) -> int:
        """Count transactions for a user."""
        result = await self.session.execute(select(func.count(Transaction.id)).where(Transaction.user_id == user_id))
        return int(result.scalar_one() or 0)

    async def delete_transaction(self, transaction_id: UUID) -> None:
        """Delete a transaction record by primary key."""
        await self.session.execute(delete(Transaction).where(Transaction.id == transaction_id))
        await self.session.flush()
