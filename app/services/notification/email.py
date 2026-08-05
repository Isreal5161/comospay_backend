from __future__ import annotations

import logging
from datetime import datetime, timezone
from string import Template
from typing import Any, Awaitable, Callable

from app.models.provider import Provider
from app.services.provider_service import ProviderService
from app.utils.exceptions import NotificationException, ValidationException


EmailAttachment = dict[str, Any]


class EmailNotificationService:
    """Manage asynchronous transactional email delivery through provider orchestration."""

    def __init__(
        self,
        *,
        provider_service: ProviderService,
        logger: logging.Logger | None = None,
    ) -> None:
        self.provider_service = provider_service
        self.logger = logger or logging.getLogger(__name__)

    async def send_email(
        self,
        *,
        recipients: str | list[str],
        subject: str,
        template_name: str | None = None,
        template_content: str | None = None,
        template_data: dict[str, Any] | None = None,
        attachments: list[EmailAttachment] | None = None,
        metadata: dict[str, Any] | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        email_type: str | None = None,
    ) -> dict[str, Any]:
        """Send a generic transactional email through the configured email provider stack."""
        recipients_normalized = self._normalize_recipients(recipients)
        self._validate_subject(subject)
        self._validate_provider_operation(provider_operation)

        rendered_body = self._render_template(template_content, template_data) if template_content is not None else None
        payload = {
            "to": recipients_normalized,
            "subject": subject,
            "template_name": template_name,
            "template_data": template_data or {},
            "body": rendered_body,
            "attachments": attachments or [],
            "metadata": metadata or {},
            "provider_name": provider_name,
            "email_type": email_type,
            "sent_at": self._now_iso(),
        }

        try:
            provider_response = await self.provider_service.execute_email(
                operation=provider_operation,
                validate=self._validate_email_payload,
                normalize=self._normalize_provider_response,
                payload=payload,
            )
        except Exception as exc:
            self.logger.warning(
                "email_send_failed",
                extra={"subject": subject, "recipients": recipients_normalized, "error": str(exc), "provider_name": provider_name},
            )
            if isinstance(exc, ValidationException):
                raise
            raise NotificationException(detail=str(exc)) from exc

        response = self._build_response(
            recipients=recipients_normalized,
            subject=subject,
            email_type=email_type,
            provider_response=provider_response,
        )
        self.logger.info(
            "email_send_completed",
            extra={"subject": subject, "recipients": recipients_normalized, "provider": response.get("provider")},
        )
        return response

    async def send_transactional_email(
        self,
        *,
        recipients: str | list[str],
        subject: str,
        template_name: str | None = None,
        template_data: dict[str, Any] | None = None,
        attachments: list[EmailAttachment] | None = None,
        metadata: dict[str, Any] | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Send a transactional email notification."""
        return await self.send_email(
            recipients=recipients,
            subject=subject,
            template_name=template_name,
            template_data=template_data,
            attachments=attachments,
            metadata=metadata,
            provider_name=provider_name,
            provider_operation=provider_operation,
            email_type="transactional",
        )

    async def send_otp_email(
        self,
        *,
        recipients: str | list[str],
        otp_code: str,
        purpose: str,
        expires_at: datetime,
        subject: str | None = None,
        template_name: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Send an OTP email to a user."""
        return await self.send_email(
            recipients=recipients,
            subject=subject or "Your verification code",
            template_name=template_name or "otp_email",
            template_data={
                "otp_code": otp_code,
                "purpose": purpose,
                "expires_at": expires_at.isoformat(),
            },
            metadata={"event": "otp_email", "purpose": purpose},
            provider_name=provider_name,
            provider_operation=provider_operation,
            email_type="otp",
        )

    async def send_password_reset_email(
        self,
        *,
        recipients: str | list[str],
        reset_url: str,
        token: str,
        subject: str | None = None,
        template_name: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Send a password reset email."""
        return await self.send_email(
            recipients=recipients,
            subject=subject or "Reset your password",
            template_name=template_name or "password_reset_email",
            template_data={"reset_url": reset_url, "token": token},
            metadata={"event": "password_reset_email"},
            provider_name=provider_name,
            provider_operation=provider_operation,
            email_type="password_reset",
        )

    async def send_welcome_email(
        self,
        *,
        recipients: str | list[str],
        user_name: str | None = None,
        subject: str | None = None,
        template_name: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Send a welcome email to a new user."""
        return await self.send_email(
            recipients=recipients,
            subject=subject or "Welcome to CosmozPay",
            template_name=template_name or "welcome_email",
            template_data={"user_name": user_name},
            metadata={"event": "welcome_email"},
            provider_name=provider_name,
            provider_operation=provider_operation,
            email_type="welcome",
        )

    async def send_wallet_funding_notification(
        self,
        *,
        recipients: str | list[str],
        amount: str,
        currency: str,
        wallet_name: str | None = None,
        subject: str | None = None,
        template_name: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Send a wallet funding notification email."""
        return await self.send_email(
            recipients=recipients,
            subject=subject or "Your wallet has been funded",
            template_name=template_name or "wallet_funding_email",
            template_data={"amount": amount, "currency": currency, "wallet_name": wallet_name},
            metadata={"event": "wallet_funding_email"},
            provider_name=provider_name,
            provider_operation=provider_operation,
            email_type="wallet_funding",
        )

    async def send_transfer_notification(
        self,
        *,
        recipients: str | list[str],
        amount: str,
        currency: str,
        source_account: str,
        destination_account: str,
        subject: str | None = None,
        template_name: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Send a transfer notification email."""
        return await self.send_email(
            recipients=recipients,
            subject=subject or "Transfer completed successfully",
            template_name=template_name or "transfer_notification_email",
            template_data={
                "amount": amount,
                "currency": currency,
                "source_account": source_account,
                "destination_account": destination_account,
            },
            metadata={"event": "transfer_notification_email"},
            provider_name=provider_name,
            provider_operation=provider_operation,
            email_type="transfer_notification",
        )

    async def send_purchase_receipt(
        self,
        *,
        recipients: str | list[str],
        amount: str,
        currency: str,
        reference: str,
        description: str | None = None,
        subject: str | None = None,
        template_name: str | None = None,
        attachments: list[EmailAttachment] | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Send a purchase receipt email."""
        return await self.send_email(
            recipients=recipients,
            subject=subject or "Your purchase receipt",
            template_name=template_name or "purchase_receipt_email",
            template_data={"amount": amount, "currency": currency, "reference": reference, "description": description},
            attachments=attachments,
            metadata={"event": "purchase_receipt_email", "reference": reference},
            provider_name=provider_name,
            provider_operation=provider_operation,
            email_type="purchase_receipt",
        )

    async def send_kyc_notification(
        self,
        *,
        recipients: str | list[str],
        status: str,
        kyc_type: str | None = None,
        reason: str | None = None,
        subject: str | None = None,
        template_name: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Send a KYC notification email."""
        return await self.send_email(
            recipients=recipients,
            subject=subject or "Your KYC status has been updated",
            template_name=template_name or "kyc_notification_email",
            template_data={"status": status, "kyc_type": kyc_type, "reason": reason},
            metadata={"event": "kyc_notification_email", "status": status},
            provider_name=provider_name,
            provider_operation=provider_operation,
            email_type="kyc_notification",
        )

    async def send_security_alert(
        self,
        *,
        recipients: str | list[str],
        alert_type: str,
        details: str | None = None,
        subject: str | None = None,
        template_name: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Send a security alert email."""
        return await self.send_email(
            recipients=recipients,
            subject=subject or "Important security notification",
            template_name=template_name or "security_alert_email",
            template_data={"alert_type": alert_type, "details": details},
            metadata={"event": "security_alert_email", "alert_type": alert_type},
            provider_name=provider_name,
            provider_operation=provider_operation,
            email_type="security_alert",
        )

    async def send_admin_notification(
        self,
        *,
        recipients: str | list[str],
        subject: str,
        message: str,
        template_name: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
    ) -> dict[str, Any]:
        """Send an administrative notification email."""
        return await self.send_email(
            recipients=recipients,
            subject=subject,
            template_name=template_name or "admin_notification_email",
            template_data={"message": message},
            metadata={"event": "admin_notification_email"},
            provider_name=provider_name,
            provider_operation=provider_operation,
            email_type="admin_notification",
        )

    def _normalize_recipients(self, recipients: str | list[str]) -> list[str]:
        if isinstance(recipients, str):
            recipients = [recipients]
        normalized = [recipient.strip() for recipient in recipients if isinstance(recipient, str) and recipient.strip()]
        if not normalized:
            raise ValidationException("At least one valid email recipient is required.")
        return normalized

    def _validate_subject(self, subject: str) -> None:
        if not subject or not isinstance(subject, str) or not subject.strip():
            raise ValidationException("Email subject is required.")

    def _validate_provider_operation(self, provider_operation: Callable[[Provider], Awaitable[Any]] | None) -> None:
        if provider_operation is None:
            raise ValidationException("A provider operation callback is required for email delivery.")

    def _validate_email_payload(self, payload: dict[str, Any]) -> None:
        if not payload or not payload.get("to") or not payload.get("subject"):
            raise ValidationException("Email payload requires recipients and subject.")

    def _render_template(self, template_content: str, template_data: dict[str, Any] | None) -> str:
        if template_data is None:
            return template_content
        try:
            return Template(template_content).safe_substitute(template_data)
        except Exception as exc:
            raise NotificationException(detail=f"Email template rendering failed: {exc}") from exc

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
        subject: str,
        email_type: str | None,
        provider_response: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "status": provider_response.get("status", "sent"),
            "provider": provider_response.get("provider"),
            "provider_reference": provider_response.get("provider_reference"),
            "message_id": provider_response.get("message_id"),
            "recipients": recipients,
            "subject": subject,
            "email_type": email_type,
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


__all__ = ["EmailNotificationService"]
