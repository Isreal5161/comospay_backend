from __future__ import annotations

import logging
from datetime import datetime, timezone
from string import Template
from typing import Any, Awaitable, Callable

from app.models.provider import Provider
from app.services.provider_service import ProviderService
from app.utils.exceptions import NotificationException, ValidationException


class SMSNotificationService:
    """Manage asynchronous SMS delivery through provider orchestration."""

    def __init__(
        self,
        *,
        provider_service: ProviderService,
        logger: logging.Logger | None = None,
    ) -> None:
        self.provider_service = provider_service
        self.logger = logger or logging.getLogger(__name__)

    async def send_sms(
        self,
        *,
        recipients: str | list[str],
        message: str | None = None,
        template_name: str | None = None,
        template_content: str | None = None,
        template_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        sms_type: str | None = None,
    ) -> dict[str, Any]:
        """Send a generic SMS through the configured SMS provider stack."""
        recipients_normalized = self._normalize_recipients(recipients)
        if provider_operation is None:
            raise ValidationException("A provider operation callback is required for SMS delivery.")

        body = self._render_sms(template_content, template_data) if template_content is not None else message
        if not body or not isinstance(body, str) or not body.strip():
            raise ValidationException("SMS message content is required.")

        payload = {
            "to": recipients_normalized,
            "message": body.strip(),
            "template_name": template_name,
            "template_data": template_data or {},
            "metadata": metadata or {},
            "provider_name": provider_name,
            "sms_type": sms_type,
            "sent_at": self._now_iso(),
        }

        try:
            provider_response = await self.provider_service.execute_sms(
                operation=provider_operation,
                validate=self._validate_sms_payload,
                normalize=self._normalize_provider_response,
                payload=payload,
            )
        except Exception as exc:
            self.logger.warning(
                "sms_send_failed",
                extra={
                    "recipients": recipients_normalized,
                    "sms_type": sms_type,
                    "provider_name": provider_name,
                    "error": str(exc),
                },
            )
            if isinstance(exc, ValidationException):
                raise
            raise NotificationException(detail=str(exc)) from exc

        response = self._build_response(
            recipients=recipients_normalized,
            sms_type=sms_type,
            provider_response=provider_response,
        )
        self.logger.info(
            "sms_send_completed",
            extra={
                "recipients": recipients_normalized,
                "sms_type": sms_type,
                "provider": response.get("provider"),
            },
        )
        return response

    async def send_otp_sms(
        self,
        *,
        recipients: str | list[str],
        otp_code: str,
        purpose: str,
        expires_at: datetime,
        template_name: str | None = None,
        template_content: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Send an OTP SMS to a user."""
        return await self.send_sms(
            recipients=recipients,
            template_name=template_name or "otp_sms",
            template_content=template_content,
            template_data={
                "otp_code": otp_code,
                "purpose": purpose,
                "expires_at": expires_at.isoformat(),
            },
            metadata={"event": "otp_sms", "purpose": purpose},
            provider_name=provider_name,
            provider_operation=provider_operation,
            sms_type="otp",
        )

    async def send_transaction_alert(
        self,
        *,
        recipients: str | list[str],
        amount: str,
        currency: str,
        reference: str,
        description: str | None = None,
        template_name: str | None = None,
        template_content: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Send a transaction alert SMS."""
        return await self.send_sms(
            recipients=recipients,
            template_name=template_name or "transaction_alert_sms",
            template_content=template_content,
            template_data={
                "amount": amount,
                "currency": currency,
                "reference": reference,
                "description": description,
            },
            metadata={"event": "transaction_alert_sms", "reference": reference},
            provider_name=provider_name,
            provider_operation=provider_operation,
            sms_type="transaction_alert",
        )

    async def send_wallet_funding_notification(
        self,
        *,
        recipients: str | list[str],
        amount: str,
        currency: str,
        wallet_name: str | None = None,
        template_name: str | None = None,
        template_content: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Send a wallet funding notification SMS."""
        return await self.send_sms(
            recipients=recipients,
            template_name=template_name or "wallet_funding_sms",
            template_content=template_content,
            template_data={"amount": amount, "currency": currency, "wallet_name": wallet_name},
            metadata={"event": "wallet_funding_sms"},
            provider_name=provider_name,
            provider_operation=provider_operation,
            sms_type="wallet_funding",
        )

    async def send_transfer_notification(
        self,
        *,
        recipients: str | list[str],
        amount: str,
        currency: str,
        source_account: str,
        destination_account: str,
        template_name: str | None = None,
        template_content: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Send a transfer notification SMS."""
        return await self.send_sms(
            recipients=recipients,
            template_name=template_name or "transfer_notification_sms",
            template_content=template_content,
            template_data={
                "amount": amount,
                "currency": currency,
                "source_account": source_account,
                "destination_account": destination_account,
            },
            metadata={"event": "transfer_notification_sms"},
            provider_name=provider_name,
            provider_operation=provider_operation,
            sms_type="transfer_notification",
        )

    async def send_kyc_notification(
        self,
        *,
        recipients: str | list[str],
        status: str,
        kyc_type: str | None = None,
        reason: str | None = None,
        template_name: str | None = None,
        template_content: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Send a KYC status notification SMS."""
        return await self.send_sms(
            recipients=recipients,
            template_name=template_name or "kyc_notification_sms",
            template_content=template_content,
            template_data={"status": status, "kyc_type": kyc_type, "reason": reason},
            metadata={"event": "kyc_notification_sms", "status": status},
            provider_name=provider_name,
            provider_operation=provider_operation,
            sms_type="kyc_notification",
        )

    async def send_security_alert(
        self,
        *,
        recipients: str | list[str],
        alert_type: str,
        details: str | None = None,
        template_name: str | None = None,
        template_content: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Send a security alert SMS."""
        return await self.send_sms(
            recipients=recipients,
            template_name=template_name or "security_alert_sms",
            template_content=template_content,
            template_data={"alert_type": alert_type, "details": details},
            metadata={"event": "security_alert_sms", "alert_type": alert_type},
            provider_name=provider_name,
            provider_operation=provider_operation,
            sms_type="security_alert",
        )

    async def send_low_balance_notification(
        self,
        *,
        recipients: str | list[str],
        balance: str,
        currency: str,
        threshold: str | None = None,
        template_name: str | None = None,
        template_content: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Send a low balance alert SMS."""
        return await self.send_sms(
            recipients=recipients,
            template_name=template_name or "low_balance_sms",
            template_content=template_content,
            template_data={"balance": balance, "currency": currency, "threshold": threshold},
            metadata={"event": "low_balance_sms", "threshold": threshold},
            provider_name=provider_name,
            provider_operation=provider_operation,
            sms_type="low_balance",
        )

    async def send_admin_notification(
        self,
        *,
        recipients: str | list[str],
        message: str,
        template_name: str | None = None,
        template_content: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Send an administrative SMS notification."""
        return await self.send_sms(
            recipients=recipients,
            message=message,
            template_name=template_name or "admin_notification_sms",
            template_content=template_content,
            template_data={"message": message},
            metadata={"event": "admin_notification_sms"},
            provider_name=provider_name,
            provider_operation=provider_operation,
            sms_type="admin_notification",
        )

    def _normalize_recipients(self, recipients: str | list[str]) -> list[str]:
        if isinstance(recipients, str):
            recipients = [recipients]
        normalized = [recipient.strip() for recipient in recipients if isinstance(recipient, str) and recipient.strip()]
        if not normalized:
            raise ValidationException("At least one valid SMS recipient is required.")
        return normalized

    def _validate_provider_operation(self, provider_operation: Callable[[Provider], Awaitable[Any]] | None) -> None:
        if provider_operation is None:
            raise ValidationException("A provider operation callback is required for SMS delivery.")

    def _validate_sms_payload(self, payload: dict[str, Any]) -> None:
        if not payload or not payload.get("to") or not payload.get("message"):
            raise ValidationException("SMS payload requires recipients and message content.")

    def _render_sms(self, template_content: str | None, template_data: dict[str, Any] | None) -> str | None:
        if template_content is None:
            return None
        if template_data is None:
            return template_content
        try:
            return Template(template_content).safe_substitute(template_data)
        except Exception as exc:
            raise NotificationException(detail=f"SMS template rendering failed: {exc}") from exc

    def _normalize_provider_response(self, result: Any, provider: Provider) -> dict[str, Any]:
        if isinstance(result, dict):
            payload = result
        else:
            payload = {"value": result}

        return {
            "status": str(payload.get("status") or "sent").lower(),
            "provider": getattr(provider, "name", None) or self._coerce_string(payload.get("provider")),
            "provider_reference": self._coerce_string(payload.get("provider_reference") or payload.get("message_id") or payload.get("id")),
            "message_id": self._coerce_string(payload.get("message_id") or payload.get("id") or payload.get("reference")),
            "raw": payload,
        }

    def _build_response(
        self,
        *,
        recipients: list[str],
        sms_type: str | None,
        provider_response: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "status": provider_response.get("status", "sent"),
            "provider": provider_response.get("provider"),
            "provider_reference": provider_response.get("provider_reference"),
            "message_id": provider_response.get("message_id"),
            "recipients": recipients,
            "sms_type": sms_type,
            "sent_at": self._now_iso(),
            "provider_response": provider_response,
        }

    def _coerce_string(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return str(value).strip()

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()


__all__ = ["SMSNotificationService"]
