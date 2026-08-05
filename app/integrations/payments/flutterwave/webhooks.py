from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any, Mapping
from uuid import uuid4

from app.integrations.payments.flutterwave.authentication import FlutterwaveAuthentication
from app.integrations.payments.flutterwave.exceptions import (
    FlutterwaveAuthenticationError,
    FlutterwaveValidationError,
    FlutterwaveWebhookVerificationError,
)
from app.integrations.payments.flutterwave.models import WebhookEvent, WebhookPayload
from app.integrations.payments.flutterwave.utils import normalize_provider_status, parse_json_payload


class FlutterwaveWebhookService:
    """Validate and normalize incoming Flutterwave webhook payloads."""

    def __init__(
        self,
        *,
        authentication: FlutterwaveAuthentication | None = None,
        logger: logging.Logger | None = None,
        webhook_secret: str | None = None,
    ) -> None:
        self.authentication = authentication or FlutterwaveAuthentication()
        self.logger = logger or logging.getLogger(__name__)
        self.webhook_secret = self._resolve_webhook_secret(webhook_secret)

    def verify_signature(self, *, payload: str | bytes | None, signature: str | None) -> bool:
        """Validate the webhook signature using constant-time comparison."""
        if not payload:
            raise FlutterwaveValidationError("Webhook payload is required.")
        if not signature:
            raise FlutterwaveWebhookVerificationError("Webhook signature header is missing.")

        expected_signature = self._compute_signature(payload)
        if not hmac.compare_digest(expected_signature, signature):
            raise FlutterwaveWebhookVerificationError("Webhook signature verification failed.")
        return True

    def parse_event(self, payload: Mapping[str, Any] | str | bytes | None) -> WebhookPayload:
        """Parse and validate a webhook payload into a strongly typed model."""
        if payload is None:
            raise FlutterwaveValidationError("Webhook payload is required.")

        parsed_payload = self._coerce_payload(payload)
        if not isinstance(parsed_payload, dict):
            raise FlutterwaveValidationError("Webhook payload must be an object.")

        event_payload = WebhookPayload.model_validate(parsed_payload)
        self.validate_event(event_payload)
        return event_payload

    def validate_event(self, payload: WebhookPayload | Mapping[str, Any]) -> WebhookPayload:
        """Validate the webhook payload structure and required fields."""
        if isinstance(payload, Mapping):
            event_payload = WebhookPayload.model_validate(dict(payload))
        else:
            event_payload = payload

        if not event_payload.event:
            raise FlutterwaveValidationError("Webhook event is missing.")
        if not event_payload.data:
            raise FlutterwaveValidationError("Webhook payload data is missing.")
        if not isinstance(event_payload.data, dict):
            raise FlutterwaveValidationError("Webhook payload data must be an object.")
        if not event_payload.data.get("id") and not event_payload.data.get("txRef") and not event_payload.data.get("tx_ref"):
            raise FlutterwaveValidationError("Webhook payload does not contain a transaction identifier.")
        return event_payload

    def extract_event_type(self, payload: WebhookPayload | Mapping[str, Any]) -> str:
        """Return the normalized event type."""
        if isinstance(payload, Mapping):
            event_payload = WebhookPayload.model_validate(dict(payload))
        else:
            event_payload = payload
        if not event_payload.event:
            raise FlutterwaveValidationError("Webhook event is missing.")
        return str(event_payload.event).strip().lower()

    def extract_transaction_reference(self, payload: WebhookPayload | Mapping[str, Any]) -> str | None:
        """Extract a transaction reference from a supported webhook payload."""
        if isinstance(payload, Mapping):
            event_payload = WebhookPayload.model_validate(dict(payload))
        else:
            event_payload = payload
        if not event_payload.data:
            return None
        data = event_payload.data
        for field_name in ("txRef", "tx_ref", "transactionReference", "transaction_reference"):
            value = data.get(field_name)
            if value:
                return str(value)
        return None

    def extract_payment_status(self, payload: WebhookPayload | Mapping[str, Any]) -> str:
        """Normalize the provider payment status from a webhook payload."""
        if isinstance(payload, Mapping):
            event_payload = WebhookPayload.model_validate(dict(payload))
        else:
            event_payload = payload
        if not event_payload.data:
            return "unknown"
        data = event_payload.data
        for field_name in ("status", "statusCode", "eventStatus"):
            value = data.get(field_name)
            if value is not None:
                return normalize_provider_status(str(value))
        return "unknown"

    def acknowledge(self, *, request_id: str | None = None) -> dict[str, Any]:
        """Return a standard acknowledgement payload for successful webhook processing."""
        resolved_request_id = request_id or f"webhook-ack-{uuid4().hex}"
        self.logger.info(
            "flutterwave_webhook_acknowledged",
            extra={
                "event": "flutterwave_webhook_acknowledged",
                "request_id": resolved_request_id,
            },
        )
        return {"status": "success", "message": "Webhook acknowledged"}

    def _compute_signature(self, payload: str | bytes) -> str:
        """Compute the expected Flutterwave webhook signature."""
        if not self.webhook_secret:
            raise FlutterwaveAuthenticationError("Webhook secret is not configured.")
        if isinstance(payload, bytes):
            payload_bytes = payload
        else:
            payload_bytes = payload.encode("utf-8")
        digest = hmac.new(self.webhook_secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
        return digest

    def _coerce_payload(self, payload: Mapping[str, Any] | str | bytes) -> Any:
        """Convert a payload into a plain mapping or return a parsed JSON object."""
        if isinstance(payload, (str, bytes)):
            return parse_json_payload(payload)
        return payload

    def _resolve_webhook_secret(self, webhook_secret: str | None) -> str:
        """Resolve the configured webhook secret from explicit input or settings."""
        if webhook_secret:
            return webhook_secret
        configured = getattr(self.authentication, "_secret_key", None)
        if configured:
            return str(configured)
        return ""
