from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from app.integrations.payments.flutterwave.models import TransferResponse
from app.integrations.payments.flutterwave.client import FlutterwaveClient
from app.integrations.payments.flutterwave.transfers import FlutterwaveTransferService
from app.integrations.payments.flutterwave.utils import normalize_provider_status


class FlutterwaveWithdrawalProvider:
    """Adapt Flutterwave transfers to the provider-neutral withdrawal contract."""

    name = "flutterwave"

    def __init__(self, transfer_service: FlutterwaveTransferService) -> None:
        self.transfer_service = transfer_service

    async def transfer(
        self,
        *,
        account_details: Mapping[str, Any],
        amount: Decimal | float | int,
        reference: str | None = None,
    ) -> dict[str, Any]:
        """Submit a transfer using the supplied internal withdrawal reference."""
        response = await self.transfer_service.create_transfer(
            account_bank=str(account_details.get("account_bank") or account_details.get("bank_code") or ""),
            account_number=str(account_details.get("account_number") or ""),
            account_name=account_details.get("account_name") or account_details.get("beneficiary_name"),
            amount=float(amount),
            narration=account_details.get("narration"),
            reference=reference,
            currency=str(account_details.get("currency") or "NGN"),
        )
        return self._normalize_response(
            response,
            reference=reference,
            amount=amount,
            currency=account_details.get("currency"),
        )

    def _normalize_response(
        self,
        response: TransferResponse | Mapping[str, Any],
        *,
        reference: str | None,
        amount: Decimal | float | int,
        currency: Any,
    ) -> dict[str, Any]:
        payload = response.model_dump() if isinstance(response, TransferResponse) else dict(response)
        data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
        provider_transaction_id = data.get("id") or data.get("transaction_id")
        return {
            "provider": self.name,
            "provider_reference": data.get("reference") or data.get("tx_ref") or reference,
            "provider_transaction_id": (
                str(provider_transaction_id) if provider_transaction_id is not None else None
            ),
            "status": normalize_provider_status(data.get("status") or payload.get("status")),
            "amount": data.get("amount", amount),
            "currency": data.get("currency") or currency or "NGN",
            "beneficiary": {
                "account_bank": data.get("account_bank"),
                "account_number": data.get("account_number"),
                "account_name": data.get("account_name"),
            },
            "metadata": {"reference": reference},
        }


def build_flutterwave_withdrawal_provider() -> FlutterwaveWithdrawalProvider:
    """Build the configured Flutterwave withdrawal integration."""
    return FlutterwaveWithdrawalProvider(
        FlutterwaveTransferService(client=FlutterwaveClient())
    )