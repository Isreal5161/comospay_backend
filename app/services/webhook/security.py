from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any

from app.utils.exceptions import ValidationException


class WebhookSecurityService:
    """Provide reusable security utilities for webhook verification."""

    def __init__(self, *, logger: logging.Logger | None = None, max_timestamp_skew_seconds: int = 300) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.max_timestamp_skew_seconds = max_timestamp_skew_seconds

    async def verify_signature(self, *, payload: bytes, signature: str | None, timestamp: str | None, secret: str | None) -> bool:
        if not payload:
            raise ValidationException("Webhook payload is required.")
        if not secret:
            raise ValidationException("Webhook secret is required.")
        if not signature:
            raise ValidationException("Webhook signature is required.")
        self._validate_timestamp(timestamp)
        expected = self._compute_signature(payload=payload, secret=secret)
        if not hmac.compare_digest(expected, signature):
            raise ValidationException("Webhook signature validation failed.")
        return True

    async def validate_request(self, *, payload: dict[str, Any], provider_name: str | None = None, timestamp: str | None = None, secret: str | None = None) -> dict[str, Any]:
        if not isinstance(payload, dict) or not payload:
            raise ValidationException("Webhook payload must be a non-empty object.")
        if not provider_name:
            raise ValidationException("Provider name is required.")
        self._validate_timestamp(timestamp)
        if not secret:
            raise ValidationException("Webhook secret is required.")
        return {"provider_name": provider_name, "payload": payload}

    def _validate_timestamp(self, timestamp: str | None) -> None:
        if timestamp is None:
            raise ValidationException("Webhook timestamp is required.")
        try:
            value = int(timestamp)
        except (TypeError, ValueError) as exc:
            raise ValidationException("Webhook timestamp is invalid.") from exc
        now = int(time.time())
        if abs(now - value) > self.max_timestamp_skew_seconds:
            raise ValidationException("Webhook timestamp is too old or from the future.")

    def _compute_signature(self, *, payload: bytes, secret: str) -> str:
        return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
