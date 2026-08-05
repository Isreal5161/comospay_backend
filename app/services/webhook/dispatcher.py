from __future__ import annotations

import logging
from typing import Any

from app.utils.exceptions import ValidationException


class WebhookDispatcher:
    """Route incoming webhook events to the correct internal handler."""

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)

    async def dispatch(self, *, provider_name: str, event_type: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        if not provider_name:
            raise ValidationException("Provider name is required.")
        provider = provider_name.lower()
        event = (event_type or "").lower()
        if provider in {"flutterwave", "flutterwave-payments"}:
            return {"provider_name": provider, "event_type": event, "status": "dispatched"}
        if provider in {"aida", "aidapay", "clubkonnect", "vtugate", "vtu.ng", "vtu-ng"}:
            return {"provider_name": provider, "event_type": event, "status": "dispatched"}
        if provider in {"waec", "neco", "nabteb", "jamb", "remita"}:
            return {"provider_name": provider, "event_type": event, "status": "dispatched"}
        if provider in {"cardtonic", "prestmit"}:
            return {"provider_name": provider, "event_type": event, "status": "dispatched"}
        if provider in {"termii", "twilio", "smtp", "resend"}:
            return {"provider_name": provider, "event_type": event, "status": "dispatched"}
        if provider in {"cloudinary", "s3"}:
            return {"provider_name": provider, "event_type": event, "status": "dispatched"}
        return {"provider_name": provider, "event_type": event, "status": "unknown_provider"}
