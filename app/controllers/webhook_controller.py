from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.config.settings import settings
from app.services.webhook_service import WebhookService
from app.utils.exceptions import AppException
from app.utils.logger import get_logger, log_api_event
from app.utils.response import success_response


class WebhookRequest(BaseModel):
    """Request schema for incoming webhook payloads."""

    provider_name: str = Field(..., max_length=100, description="Name of the webhook sender.")
    event_id: str | None = Field(default=None, max_length=255, description="Provider event identifier.")
    payload: dict[str, Any] = Field(..., description="Webhook payload body.")
    signature: str | None = Field(default=None, description="Webhook signature header value.")
    timestamp: str | None = Field(default=None, description="Webhook timestamp header value.")
    secret: str | None = Field(default=None, description="Webhook secret or signing token.")


class WebhookController:
    """Thin FastAPI controller for inbound provider webhooks."""

    def __init__(self, webhook_service: WebhookService, logger: logging.Logger | None = None) -> None:
        self.webhook_service = webhook_service
        self.logger = logger or get_logger(__name__)
        self.router = APIRouter(prefix="/webhooks", tags=["Webhooks"])
        self._register_routes()

    def _register_routes(self) -> None:
        self.router.post("/payment", status_code=status.HTTP_200_OK)(self.handle_payment_webhook)
        self.router.post("/virtual-account", status_code=status.HTTP_200_OK)(self.handle_virtual_account_webhook)
        self.router.post("/transfer", status_code=status.HTTP_200_OK)(self.handle_transfer_webhook)
        self.router.post("/provider", status_code=status.HTTP_200_OK)(self.handle_provider_webhook)
        self.router.post("/notification", status_code=status.HTTP_200_OK)(self.handle_notification_webhook)
        self.router.post("/giftcard", status_code=status.HTTP_200_OK)(self.handle_giftcard_webhook)

    async def handle_payment_webhook(self, request: Request) -> dict[str, Any]:
        """Handle payment provider webhook requests."""
        payload, raw_body = await self._read_payload(request)
        provider_name = self._extract_header(request, "x-provider-name") or payload.get("provider_name") or "flutterwave"
        signature = self._extract_header(request, "x-webhook-signature") or payload.get("signature")
        timestamp = self._extract_header(request, "x-webhook-timestamp") or payload.get("timestamp")
        configured_secret = self._resolve_configured_secret(provider_name)
        await self._verify_request_signature(raw_body=raw_body, signature=signature, timestamp=timestamp, secret=configured_secret)
        return await self._execute(
            action="handle_payment_webhook",
            handler=self.webhook_service.process_flutterwave_webhook,
            payload={
                "provider_name": provider_name,
                "event_id": payload.get("event_id"),
                "payload": payload.get("payload", {}),
                "signature": signature,
                "timestamp": timestamp,
                "secret": configured_secret,
            },
            success_message="Payment webhook processed successfully.",
        )

    async def handle_virtual_account_webhook(self, request: Request) -> dict[str, Any]:
        """Handle virtual-account webhook requests."""
        payload, raw_body = await self._read_payload(request)
        provider_name = self._extract_header(request, "x-provider-name") or payload.get("provider_name") or "flutterwave"
        signature = self._extract_header(request, "x-webhook-signature") or payload.get("signature")
        timestamp = self._extract_header(request, "x-webhook-timestamp") or payload.get("timestamp")
        configured_secret = self._resolve_configured_secret(provider_name)
        await self._verify_request_signature(raw_body=raw_body, signature=signature, timestamp=timestamp, secret=configured_secret)
        return await self._execute(
            action="handle_virtual_account_webhook",
            handler=self.webhook_service.process_flutterwave_webhook,
            payload={
                "provider_name": provider_name,
                "event_id": payload.get("event_id"),
                "payload": payload.get("payload", {}),
                "signature": signature,
                "timestamp": timestamp,
                "secret": configured_secret,
            },
            success_message="Virtual account webhook processed successfully.",
        )

    async def handle_transfer_webhook(self, request: Request) -> dict[str, Any]:
        """Handle transfer webhook requests."""
        payload, raw_body = await self._read_payload(request)
        provider_name = self._extract_header(request, "x-provider-name") or payload.get("provider_name") or "flutterwave"
        signature = self._extract_header(request, "x-webhook-signature") or payload.get("signature")
        timestamp = self._extract_header(request, "x-webhook-timestamp") or payload.get("timestamp")
        configured_secret = self._resolve_configured_secret(provider_name)
        await self._verify_request_signature(raw_body=raw_body, signature=signature, timestamp=timestamp, secret=configured_secret)
        return await self._execute(
            action="handle_transfer_webhook",
            handler=self.webhook_service.process_flutterwave_webhook,
            payload={
                "provider_name": provider_name,
                "event_id": payload.get("event_id"),
                "payload": payload.get("payload", {}),
                "signature": signature,
                "timestamp": timestamp,
                "secret": configured_secret,
            },
            success_message="Transfer webhook processed successfully.",
        )

    async def handle_provider_webhook(self, request: Request) -> dict[str, Any]:
        """Handle provider monitoring and health webhooks."""
        payload, raw_body = await self._read_payload(request)
        provider_name = self._extract_header(request, "x-provider-name") or payload.get("provider_name") or "provider"
        signature = self._extract_header(request, "x-webhook-signature") or payload.get("signature")
        timestamp = self._extract_header(request, "x-webhook-timestamp") or payload.get("timestamp")
        configured_secret = self._resolve_configured_secret(provider_name)
        await self._verify_request_signature(raw_body=raw_body, signature=signature, timestamp=timestamp, secret=configured_secret)
        return await self._execute(
            action="handle_provider_webhook",
            handler=self.webhook_service.process_vtu_webhook,
            payload={
                "provider_name": provider_name,
                "payload": payload.get("payload", payload),
            },
            success_message="Provider webhook processed successfully.",
        )

    async def handle_notification_webhook(self, request: Request) -> dict[str, Any]:
        """Handle notification provider webhook requests."""
        payload, raw_body = await self._read_payload(request)
        provider_name = self._extract_header(request, "x-provider-name") or payload.get("provider_name") or "flutterwave"
        signature = self._extract_header(request, "x-webhook-signature") or payload.get("signature")
        timestamp = self._extract_header(request, "x-webhook-timestamp") or payload.get("timestamp")
        configured_secret = self._resolve_configured_secret(provider_name)
        await self._verify_request_signature(raw_body=raw_body, signature=signature, timestamp=timestamp, secret=configured_secret)
        return await self._execute(
            action="handle_notification_webhook",
            handler=self.webhook_service.process_notification_webhook,
            payload={"payload": payload.get("payload", payload)},
            success_message="Notification webhook processed successfully.",
        )

    async def handle_giftcard_webhook(self, request: Request) -> dict[str, Any]:
        """Handle a generic Gift Card provider webhook request."""
        payload, raw_body = await self._read_payload(request)
        provider_name = self._extract_header(request, "x-provider-name") or payload.get("provider_name") or payload.get("provider")
        if not provider_name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Gift Card webhook provider is required.")
        signature = self._extract_header(request, "x-webhook-signature") or payload.get("signature")
        timestamp = self._extract_header(request, "x-webhook-timestamp") or payload.get("timestamp")
        configured_secret = self._resolve_configured_secret(provider_name)
        await self._verify_request_signature(raw_body=raw_body, signature=signature, timestamp=timestamp, secret=configured_secret)
        return await self._execute(
            action="handle_giftcard_webhook",
            handler=self.webhook_service.process_giftcard_webhook,
            payload={
                "provider_name": str(provider_name),
                "event_id": payload.get("event_id"),
                "payload": payload.get("payload", payload),
            },
            success_message="Gift Card webhook processed successfully.",
        )

    async def handle_sogo_giftcard_webhook(self, request: Request) -> dict[str, Any]:
        """Handle the Sogo gift-card webhook contract: provider-specific endpoint and signature header."""
        payload, raw_body = await self._read_payload(request)
        required_fields = {"event_type", "reference", "provider_reference", "status"}
        missing = sorted(required_fields - set(payload.keys()))
        if missing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Sogo webhook payload is missing required fields: {', '.join(missing)}",
            )

        signature = self._extract_header(request, "x-sogo-signature")
        configured_secret = self._resolve_configured_secret("sogo")
        if not configured_secret:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sogo webhook secret is not configured.")
        if not signature:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sogo webhook signature is required.")

        try:
            security_service = getattr(self.webhook_service, "security_service", None)
            if security_service is not None and hasattr(security_service, "verify_sogo_signature"):
                is_valid = await security_service.verify_sogo_signature(
                    payload=raw_body,
                    signature=signature,
                    secret=configured_secret,
                )
            else:
                normalized = signature.strip()
                if normalized.lower().startswith("hmac-sha256="):
                    normalized = normalized.split("=", 1)[1].strip()
                expected = __import__("hmac").new(configured_secret.encode("utf-8"), raw_body, __import__("hashlib").sha256).hexdigest()
                is_valid = __import__("hmac").compare_digest(expected, normalized)
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sogo webhook signature is invalid.") from exc

        if not is_valid:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sogo webhook signature is invalid.")

        return await self._execute(
            action="handle_sogo_giftcard_webhook",
            handler=self.webhook_service.process_giftcard_webhook,
            payload={
                "provider_name": "sogo",
                "event_id": payload.get("event_id") or payload.get("reference"),
                "payload": payload,
            },
            success_message="Sogo webhook processed successfully.",
        )

    async def _read_payload(self, request: Request) -> tuple[dict[str, Any], bytes]:
        """Read and normalize a webhook request into a dictionary payload and raw bytes."""
        try:
            body = await request.body()
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to read request body.") from exc

        if not body:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Webhook payload is required.")

        try:
            payload = body.decode("utf-8")
            if not payload.strip():
                raise ValueError("empty")
            parsed = self._parse_payload(payload)
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Webhook payload is invalid.") from exc

        if isinstance(parsed, dict):
            return parsed, body
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Webhook payload must be a JSON object.")

    def _parse_payload(self, payload: str) -> dict[str, Any]:
        import json

        return json.loads(payload)

    def _extract_header(self, request: Request, header_name: str) -> str | None:
        return request.headers.get(header_name)

    def _resolve_configured_secret(self, provider_name: str | None = None) -> str | None:
        """Resolve a webhook secret for a given provider from settings.

        Looks up a provider-specific setting like `'{provider}_webhook_secret'`.
        Falls back to `flutterwave_webhook_secret` for compatibility.
        """
        provider = (provider_name or "").strip().lower()
        if provider:
            candidate_attr = f"{provider}_webhook_secret"
            configured = getattr(settings, candidate_attr, None)
            if configured is not None:
                if hasattr(configured, "get_secret_value"):
                    return configured.get_secret_value()
                return str(configured)

        configured = getattr(settings, "flutterwave_webhook_secret", None)
        if configured is None:
            return None
        if hasattr(configured, "get_secret_value"):
            return configured.get_secret_value()
        return str(configured)

    async def _verify_request_signature(self, *, raw_body: bytes, signature: str | None, timestamp: str | None, secret: str | None) -> None:
        if not secret:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Webhook secret is not configured.")
        if not signature:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Webhook signature is required.")
        try:
            is_valid = await self.webhook_service.verify_signature(
                payload=raw_body,
                signature=signature,
                timestamp=timestamp,
                secret=secret,
            )
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Webhook signature is invalid.") from exc

        if not is_valid:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Webhook signature is invalid.")

    async def _execute(
        self,
        action: str,
        handler: Callable[..., Awaitable[Any]],
        payload: dict[str, Any],
        success_message: str,
    ) -> dict[str, Any]:
        try:
            result = await handler(**payload)
        except Exception as exc:
            raise self._handle_exception(exc, action)

        log_api_event(self.logger, "webhook_request_succeeded", action=action)
        return success_response(data=result, message=success_message)

    def _handle_exception(self, exc: Exception, action: str) -> HTTPException:
        log_api_event(self.logger, "webhook_request_failed", action=action, error=str(exc))
        if isinstance(exc, HTTPException):
            raise exc
        if isinstance(exc, AppException):
            raise exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while processing the webhook.",
        )
