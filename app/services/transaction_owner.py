from __future__ import annotations

from app.models.transaction import Transaction


class TransactionOwnerResolver:
    """Resolve transaction ownership for orchestration delegation only."""

    def resolve(self, transaction: Transaction) -> str:
        transaction_type = (transaction.transaction_type or "").strip().lower()
        category = (transaction.category or "").strip().lower()

        if transaction_type.startswith("wallet_funding") or category == "funding":
            return "wallet_funding"
        if transaction_type.startswith("wallet_transfer") or category == "transfer":
            return "wallet_transfer"
        if transaction_type.startswith("wallet_withdrawal") or category == "withdrawal":
            return "wallet_withdrawal"
        if transaction_type == "airtime_purchase" or category == "airtime":
            return "airtime"
        if transaction_type == "data_purchase" or category == "data":
            return "data"
        if transaction_type == "electricity_purchase" or category == "electricity":
            return "electricity"
        if transaction_type == "tv_purchase" or category == "tv":
            return "tv"
        if transaction_type == "education_purchase" or category == "education":
            return "education"
        if transaction_type in {"payment", "payment_collection", "collection"} or category in {"payment", "collection"}:
            return "payment"
        return "unknown"