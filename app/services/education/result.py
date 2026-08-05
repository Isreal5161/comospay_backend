from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from app.utils.exceptions import ValidationException


class EducationResultService:
    """Process and normalize education provider responses into a standardized internal format."""

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)

    async def process_result(
        self,
        *,
        provider_response: Any,
        provider_name: str | None = None,
        examination_type: str | None = None,
        transaction_reference: str | None = None,
    ) -> dict[str, Any]:
        """Normalize a provider response from an education purchase or payment flow."""
        normalized = self._normalize_response(provider_response)
        self._assert_required_fields(normalized)

        result: dict[str, Any] = {
            "transaction_reference": self._coerce_string(transaction_reference) or self._extract_transaction_reference(normalized),
            "provider_name": provider_name or self._coerce_string(normalized.get("provider_name")) or self._coerce_string(normalized.get("provider")),
            "provider_reference": self._extract_provider_reference(normalized),
            "provider_transaction_id": self._extract_provider_transaction_id(normalized),
            "status": self._extract_status(normalized),
            "examination_type": examination_type or self._coerce_string(normalized.get("examination_type")),
            "pins": self._extract_pins(normalized),
            "serial_numbers": self._extract_serial_numbers(normalized),
            "confirmation_code": self._extract_confirmation_code(normalized),
            "receipt_number": self._extract_receipt_number(normalized),
            "receipt_url": self._extract_receipt_url(normalized),
            "amount": self._coerce_string(self._extract_amount(normalized)),
            "currency": self._coerce_string(self._extract_currency(normalized)) or "NGN",
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "raw_payload": normalized,
        }

        self.logger.info(
            "education_result_processed",
            extra={
                "transaction_reference": result["transaction_reference"],
                "provider_name": result["provider_name"],
                "provider_reference": result["provider_reference"],
                "status": result["status"],
                "pin_count": len(result["pins"]),
                "serial_count": len(result["serial_numbers"]),
            },
        )

        self.logger.info(
            "education_result_audit",
            extra={
                "event": "education_result_processed",
                "transaction_reference": result["transaction_reference"],
                "provider_name": result["provider_name"],
                "provider_reference": result["provider_reference"],
            },
        )
        return result

    def _normalize_response(self, provider_response: Any) -> dict[str, Any]:
        if provider_response is None:
            raise ValidationException("Provider response is required.")
        if isinstance(provider_response, str):
            try:
                parsed = json.loads(provider_response)
            except Exception as exc:
                raise ValidationException("Provider response string could not be parsed.") from exc
            provider_response = parsed
        if isinstance(provider_response, dict):
            return provider_response
        if hasattr(provider_response, "dict"):
            return provider_response.dict()
        raise ValidationException("Provider response must be a dictionary or JSON object.")

    def _assert_required_fields(self, payload: dict[str, Any]) -> None:
        if not payload:
            raise ValidationException("Provider response payload is empty.")
        status = self._extract_status(payload)
        if not status:
            raise ValidationException("Provider response status is missing.")

    def _extract_transaction_reference(self, payload: dict[str, Any]) -> str:
        reference = self._find_value(payload, ["transaction_reference", "reference", "trans_ref", "txn_ref", "transaction_id"])
        return self._coerce_string(reference) or f"education-{uuid4().hex[:12]}"

    def _extract_provider_reference(self, payload: dict[str, Any]) -> str:
        reference = self._find_value(payload, ["provider_reference", "reference", "provider_ref", "transaction_id", "trans_id"])
        return self._coerce_string(reference)

    def _extract_provider_transaction_id(self, payload: dict[str, Any]) -> str | None:
        return self._coerce_string(self._find_value(payload, ["provider_transaction_id", "transaction_id", "trans_id", "reference"]))

    def _extract_status(self, payload: dict[str, Any]) -> str:
        raw_status = self._find_value(payload, ["status", "transaction_status", "payment_status", "result", "response_status"])
        normalized = self._coerce_string(raw_status).lower()
        if normalized in {"success", "succeeded", "completed", "settled", "paid"}:
            return "succeeded"
        if normalized in {"failed", "failure", "cancelled", "cancelled", "reversed", "declined", "error"}:
            return "failed"
        if normalized in {"pending", "processing", "queued", "in_progress"}:
            return "pending"
        return self._coerce_string(raw_status) or "pending"

    def _extract_pins(self, payload: dict[str, Any]) -> list[str]:
        pins = self._find_value(payload, ["pins", "pin", "epins", "epin", "pin_list", "voucher_codes"])
        return self._coerce_string_list(pins)

    def _extract_serial_numbers(self, payload: dict[str, Any]) -> list[str]:
        serials = self._find_value(payload, ["serial_numbers", "serials", "voucher_serials", "serial"])
        return self._coerce_string_list(serials)

    def _extract_confirmation_code(self, payload: dict[str, Any]) -> str | None:
        return self._coerce_string(self._find_value(payload, ["confirmation_code", "confirmation", "payment_confirmation", "confirmation_number", "auth_code"]))

    def _extract_receipt_number(self, payload: dict[str, Any]) -> str | None:
        return self._coerce_string(self._find_value(payload, ["receipt_number", "receipt", "invoice_number", "payment_reference"]))

    def _extract_receipt_url(self, payload: dict[str, Any]) -> str | None:
        return self._coerce_string(self._find_value(payload, ["receipt_url", "receipt_link", "invoice_url", "payment_url"]))

    def _extract_amount(self, payload: dict[str, Any]) -> str | None:
        amount = self._find_value(payload, ["amount", "paid_amount", "price", "total_amount", "paid"])
        return self._coerce_string(amount)

    def _extract_currency(self, payload: dict[str, Any]) -> str | None:
        return self._coerce_string(self._find_value(payload, ["currency", "currency_code", "currency_symbol"]))

    def _find_value(self, payload: Any, keys: list[str]) -> Any:
        if isinstance(payload, dict):
            for key in keys:
                if key in payload:
                    return payload[key]
                lower_key = key.lower()
                for candidate in payload:
                    if str(candidate).lower() == lower_key:
                        return payload[candidate]
            for value in payload.values():
                if isinstance(value, dict):
                    nested = self._find_value(value, keys)
                    if nested is not None:
                        return nested
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            nested = self._find_value(item, keys)
                            if nested is not None:
                                return nested
        return None

    def _coerce_string(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (int, float, UUID)):
            return str(value)
        return ""

    def _coerce_string_list(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            cleaned = [item.strip() for item in re.split(r"[\s,;|]+", value) if item.strip()]
            return cleaned
        if isinstance(value, list):
            normalized: list[str] = []
            for item in value:
                if isinstance(item, str) and item.strip():
                    normalized.append(item.strip())
                elif isinstance(item, dict):
                    nested_pin = self._find_value(item, ["pin", "code", "epin", "serial"])
                    if nested_pin:
                        normalized.extend(self._coerce_string_list(nested_pin))
            return normalized
        if isinstance(value, dict):
            nested = self._find_value(value, ["pin", "code", "epin", "serial"])
            return self._coerce_string_list(nested)
        return []


__all__ = ["EducationResultService"]
