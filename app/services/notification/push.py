from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from string import Template
from typing import Any, Awaitable, Callable

from app.models.provider import Provider
from app.services.provider_service import ProviderService
from app.utils.exceptions import NotificationException, ValidationException
from app.utils.logger import log_audit_event


class PushNotificationService:
    """Manage asynchronous push notification delivery through provider orchestration."""

    def __init__(
        self,
        *,
        provider_service: ProviderService,
        logger: logging.Logger | None = None,
    ) -> None:
        self.provider_service = provider_service
        self.logger = logger or logging.getLogger(__name__)

    async def send_push(
        self,
        *,
        recipients: str | list[str] | None = None,
        title: str | None = None,
        message: str | None = None,
        template_name: str | None = None,
        template_content: str | None = None,
        template_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        push_type: str | None = None,
        priority: str | None = None,
        badge_count: int | None = None,
        deep_link: str | None = None,
        silent: bool = False,
        broadcast: bool = False,
    ) -> dict[str, Any]:
        """Send a generic push notification through the configured push provider stack."""
        recipients_normalized = self._normalize_recipients(recipients, broadcast=broadcast)
        if provider_operation is None:
            raise ValidationException("A provider operation callback is required for push delivery.")

        body = self._render_payload(template_content, template_data) if template_content is not None else message
        if not silent and (body is None or not isinstance(body, str) or not body.strip()):
            raise ValidationException("Push notifications require a title or message unless silent is enabled.")

        payload = {
            "notification_id": str(uuid.uuid4()),
            "to": recipients_normalized,
            "title": title,
            "message": body.strip() if isinstance(body, str) else None,
            "template_name": template_name,
            "template_data": template_data or {},
            "metadata": metadata or {},
            "provider_name": provider_name,
            "push_type": push_type,
            "priority": priority,
            "badge_count": badge_count,
            "deep_link": deep_link,
            "silent": silent,
            "broadcast": broadcast,
            "sent_at": self._now_iso(),
        }

        try:
            provider_response = await self.provider_service.execute_provider(
                category="Push",
                operation=provider_operation,
                service_type="push",
                validate=self._validate_push_payload,
                normalize=self._normalize_provider_response,
                payload=payload,
            )
        except Exception as exc:
            self.logger.warning(
                "push_send_failed",
                extra={
                    "recipients": recipients_normalized,
                    "push_type": push_type,
                    "priority": priority,
                    "silent": silent,
                    "broadcast": broadcast,
                    "provider_name": provider_name,
                    "error": str(exc),
                },
            )
            if isinstance(exc, ValidationException):
                raise
            raise NotificationException(detail=str(exc)) from exc

        response = self._build_response(
            recipients=recipients_normalized,
            push_type=push_type,
            priority=priority,
            badge_count=badge_count,
            deep_link=deep_link,
            broadcast=broadcast,
            silent=silent,
            provider_response=provider_response,
        )
        self.logger.info(
            "push_send_completed",
            extra={
                "recipients": recipients_normalized,
                "push_type": push_type,
                "provider": response.get("provider"),
                "broadcast": broadcast,
                "silent": silent,
            },
        )
        log_audit_event(
            self.logger,
            "push_notification_sent",
            notification_id=response.get("notification_id"),
            push_type=push_type,
            broadcast=broadcast,
            silent=silent,
            priority=priority,
            provider=response.get("provider"),
        )
        return response

    async def send_transaction_notification(
        self,
        *,
        recipients: str | list[str],
        amount: str,
        currency: str,
        reference: str,
        description: str | None = None,
        title: str | None = None,
        template_name: str | None = None,
        template_content: str | None = None,
        template_data: dict[str, Any] | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        priority: str | None = None,
        badge_count: int | None = None,
        deep_link: str | None = None,
    ) -> dict[str, Any]:
        """Send a transactional push notification."""
        return await self.send_push(
            recipients=recipients,
            title=title or "Transaction Alert",
            message=template_content if template_content is not None else None,
            template_name=template_name or "transaction_push",
            template_content=template_content,
            template_data={
                **(template_data or {}),
                "amount": amount,
                "currency": currency,
                "reference": reference,
                "description": description,
            },
            metadata={"event": "transaction_push", "reference": reference},
            provider_name=provider_name,
            provider_operation=provider_operation,
            push_type="transaction",
            priority=priority,
            badge_count=badge_count,
            deep_link=deep_link,
        )

    async def send_wallet_funding_notification(
        self,
        *,
        recipients: str | list[str],
        amount: str,
        currency: str,
        wallet_name: str | None = None,
        title: str | None = None,
        template_name: str | None = None,
        template_content: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        priority: str | None = None,
        badge_count: int | None = None,
        deep_link: str | None = None,
    ) -> dict[str, Any]:
        """Send a wallet funding push notification."""
        return await self.send_push(
            recipients=recipients,
            title=title or "Wallet Funding Received",
            template_name=template_name or "wallet_funding_push",
            template_content=template_content,
            template_data={
                "amount": amount,
                "currency": currency,
                "wallet_name": wallet_name,
            },
            metadata={"event": "wallet_funding_push"},
            provider_name=provider_name,
            provider_operation=provider_operation,
            push_type="wallet_funding",
            priority=priority,
            badge_count=badge_count,
            deep_link=deep_link,
        )

    async def send_wallet_debit_notification(
        self,
        *,
        recipients: str | list[str],
        amount: str,
        currency: str,
        wallet_name: str | None = None,
        title: str | None = None,
        template_name: str | None = None,
        template_content: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        priority: str | None = None,
        badge_count: int | None = None,
        deep_link: str | None = None,
    ) -> dict[str, Any]:
        """Send a wallet debit push notification."""
        return await self.send_push(
            recipients=recipients,
            title=title or "Wallet Debit Alert",
            template_name=template_name or "wallet_debit_push",
            template_content=template_content,
            template_data={
                "amount": amount,
                "currency": currency,
                "wallet_name": wallet_name,
            },
            metadata={"event": "wallet_debit_push"},
            provider_name=provider_name,
            provider_operation=provider_operation,
            push_type="wallet_debit",
            priority=priority,
            badge_count=badge_count,
            deep_link=deep_link,
        )

    async def send_bank_transfer_notification(
        self,
        *,
        recipients: str | list[str],
        amount: str,
        currency: str,
        source_account: str,
        destination_account: str,
        reference: str,
        title: str | None = None,
        template_name: str | None = None,
        template_content: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        priority: str | None = None,
        badge_count: int | None = None,
        deep_link: str | None = None,
    ) -> dict[str, Any]:
        """Send a bank transfer push notification."""
        return await self.send_push(
            recipients=recipients,
            title=title or "Bank Transfer Completed",
            template_name=template_name or "bank_transfer_push",
            template_content=template_content,
            template_data={
                "amount": amount,
                "currency": currency,
                "source_account": source_account,
                "destination_account": destination_account,
                "reference": reference,
            },
            metadata={"event": "bank_transfer_push", "reference": reference},
            provider_name=provider_name,
            provider_operation=provider_operation,
            push_type="bank_transfer",
            priority=priority,
            badge_count=badge_count,
            deep_link=deep_link,
        )

    async def send_airtime_purchase_notification(
        self,
        *,
        recipients: str | list[str],
        amount: str,
        currency: str,
        provider_name_text: str,
        destination: str,
        title: str | None = None,
        template_name: str | None = None,
        template_content: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        priority: str | None = None,
        badge_count: int | None = None,
        deep_link: str | None = None,
    ) -> dict[str, Any]:
        """Send an airtime purchase push notification."""
        return await self.send_push(
            recipients=recipients,
            title=title or "Airtime Purchase Successful",
            template_name=template_name or "airtime_purchase_push",
            template_content=template_content,
            template_data={
                "amount": amount,
                "currency": currency,
                "provider_name": provider_name_text,
                "destination": destination,
            },
            metadata={"event": "airtime_purchase_push"},
            provider_name=provider_name,
            provider_operation=provider_operation,
            push_type="airtime_purchase",
            priority=priority,
            badge_count=badge_count,
            deep_link=deep_link,
        )

    async def send_data_purchase_notification(
        self,
        *,
        recipients: str | list[str],
        amount: str,
        currency: str,
        data_bundle: str,
        destination: str,
        title: str | None = None,
        template_name: str | None = None,
        template_content: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        priority: str | None = None,
        badge_count: int | None = None,
        deep_link: str | None = None,
    ) -> dict[str, Any]:
        """Send a data purchase push notification."""
        return await self.send_push(
            recipients=recipients,
            title=title or "Data Purchase Completed",
            template_name=template_name or "data_purchase_push",
            template_content=template_content,
            template_data={
                "amount": amount,
                "currency": currency,
                "data_bundle": data_bundle,
                "destination": destination,
            },
            metadata={"event": "data_purchase_push"},
            provider_name=provider_name,
            provider_operation=provider_operation,
            push_type="data_purchase",
            priority=priority,
            badge_count=badge_count,
            deep_link=deep_link,
        )

    async def send_electricity_purchase_notification(
        self,
        *,
        recipients: str | list[str],
        amount: str,
        currency: str,
        meter_number: str,
        distributor: str,
        title: str | None = None,
        template_name: str | None = None,
        template_content: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        priority: str | None = None,
        badge_count: int | None = None,
        deep_link: str | None = None,
    ) -> dict[str, Any]:
        """Send an electricity purchase push notification."""
        return await self.send_push(
            recipients=recipients,
            title=title or "Electricity Purchase Successful",
            template_name=template_name or "electricity_purchase_push",
            template_content=template_content,
            template_data={
                "amount": amount,
                "currency": currency,
                "meter_number": meter_number,
                "distributor": distributor,
            },
            metadata={"event": "electricity_purchase_push"},
            provider_name=provider_name,
            provider_operation=provider_operation,
            push_type="electricity_purchase",
            priority=priority,
            badge_count=badge_count,
            deep_link=deep_link,
        )

    async def send_tv_subscription_notification(
        self,
        *,
        recipients: str | list[str],
        amount: str,
        currency: str,
        provider_name_text: str,
        subscription_period: str,
        title: str | None = None,
        template_name: str | None = None,
        template_content: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        priority: str | None = None,
        badge_count: int | None = None,
        deep_link: str | None = None,
    ) -> dict[str, Any]:
        """Send a TV subscription push notification."""
        return await self.send_push(
            recipients=recipients,
            title=title or "TV Subscription Activated",
            template_name=template_name or "tv_subscription_push",
            template_content=template_content,
            template_data={
                "amount": amount,
                "currency": currency,
                "provider_name": provider_name_text,
                "subscription_period": subscription_period,
            },
            metadata={"event": "tv_subscription_push"},
            provider_name=provider_name,
            provider_operation=provider_operation,
            push_type="tv_subscription",
            priority=priority,
            badge_count=badge_count,
            deep_link=deep_link,
        )

    async def send_education_purchase_notification(
        self,
        *,
        recipients: str | list[str],
        amount: str,
        currency: str,
        institution_name: str,
        course_name: str,
        title: str | None = None,
        template_name: str | None = None,
        template_content: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        priority: str | None = None,
        badge_count: int | None = None,
        deep_link: str | None = None,
    ) -> dict[str, Any]:
        """Send an education purchase push notification."""
        return await self.send_push(
            recipients=recipients,
            title=title or "Education Purchase Confirmed",
            template_name=template_name or "education_purchase_push",
            template_content=template_content,
            template_data={
                "amount": amount,
                "currency": currency,
                "institution_name": institution_name,
                "course_name": course_name,
            },
            metadata={"event": "education_purchase_push"},
            provider_name=provider_name,
            provider_operation=provider_operation,
            push_type="education_purchase",
            priority=priority,
            badge_count=badge_count,
            deep_link=deep_link,
        )

    async def send_giftcard_transaction_notification(
        self,
        *,
        recipients: str | list[str],
        amount: str,
        currency: str,
        giftcard_name: str,
        reference: str,
        title: str | None = None,
        template_name: str | None = None,
        template_content: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        priority: str | None = None,
        badge_count: int | None = None,
        deep_link: str | None = None,
    ) -> dict[str, Any]:
        """Send a gift card transaction push notification."""
        return await self.send_push(
            recipients=recipients,
            title=title or "Gift Card Transaction Completed",
            template_name=template_name or "giftcard_transaction_push",
            template_content=template_content,
            template_data={
                "amount": amount,
                "currency": currency,
                "giftcard_name": giftcard_name,
                "reference": reference,
            },
            metadata={"event": "giftcard_transaction_push", "reference": reference},
            provider_name=provider_name,
            provider_operation=provider_operation,
            push_type="giftcard_transaction",
            priority=priority,
            badge_count=badge_count,
            deep_link=deep_link,
        )

    async def send_kyc_status_notification(
        self,
        *,
        recipients: str | list[str],
        status: str,
        kyc_type: str | None = None,
        reason: str | None = None,
        title: str | None = None,
        template_name: str | None = None,
        template_content: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        priority: str | None = None,
        badge_count: int | None = None,
        deep_link: str | None = None,
    ) -> dict[str, Any]:
        """Send a KYC status push notification."""
        return await self.send_push(
            recipients=recipients,
            title=title or "KYC Status Updated",
            template_name=template_name or "kyc_status_push",
            template_content=template_content,
            template_data={"status": status, "kyc_type": kyc_type, "reason": reason},
            metadata={"event": "kyc_status_push", "status": status},
            provider_name=provider_name,
            provider_operation=provider_operation,
            push_type="kyc_status",
            priority=priority,
            badge_count=badge_count,
            deep_link=deep_link,
        )

    async def send_login_alert(
        self,
        *,
        recipients: str | list[str],
        ip_address: str | None = None,
        location: str | None = None,
        device_name: str | None = None,
        title: str | None = None,
        template_name: str | None = None,
        template_content: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        priority: str | None = None,
        badge_count: int | None = None,
        deep_link: str | None = None,
    ) -> dict[str, Any]:
        """Send a login alert push notification."""
        return await self.send_push(
            recipients=recipients,
            title=title or "New Login Detected",
            template_name=template_name or "login_alert_push",
            template_content=template_content,
            template_data={
                "ip_address": ip_address,
                "location": location,
                "device_name": device_name,
            },
            metadata={"event": "login_alert_push"},
            provider_name=provider_name,
            provider_operation=provider_operation,
            push_type="login_alert",
            priority=priority,
            badge_count=badge_count,
            deep_link=deep_link,
        )

    async def send_security_alert(
        self,
        *,
        recipients: str | list[str],
        issue: str,
        details: str | None = None,
        title: str | None = None,
        template_name: str | None = None,
        template_content: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        priority: str | None = None,
        badge_count: int | None = None,
        deep_link: str | None = None,
    ) -> dict[str, Any]:
        """Send a security alert push notification."""
        return await self.send_push(
            recipients=recipients,
            title=title or "Security Alert",
            template_name=template_name or "security_alert_push",
            template_content=template_content,
            template_data={"issue": issue, "details": details},
            metadata={"event": "security_alert_push", "issue": issue},
            provider_name=provider_name,
            provider_operation=provider_operation,
            push_type="security_alert",
            priority=priority,
            badge_count=badge_count,
            deep_link=deep_link,
        )

    async def send_device_verification_notification(
        self,
        *,
        recipients: str | list[str],
        device_name: str,
        verification_code: str,
        title: str | None = None,
        template_name: str | None = None,
        template_content: str | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        priority: str | None = None,
        badge_count: int | None = None,
        deep_link: str | None = None,
    ) -> dict[str, Any]:
        """Send a device verification push notification."""
        return await self.send_push(
            recipients=recipients,
            title=title or "Verify Your Device",
            template_name=template_name or "device_verification_push",
            template_content=template_content,
            template_data={"device_name": device_name, "verification_code": verification_code},
            metadata={"event": "device_verification_push"},
            provider_name=provider_name,
            provider_operation=provider_operation,
            push_type="device_verification",
            priority=priority,
            badge_count=badge_count,
            deep_link=deep_link,
        )

    async def send_broadcast_notification(
        self,
        *,
        title: str,
        message: str | None = None,
        template_name: str | None = None,
        template_content: str | None = None,
        template_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        priority: str | None = None,
        badge_count: int | None = None,
        deep_link: str | None = None,
    ) -> dict[str, Any]:
        """Send a broadcast push notification."""
        return await self.send_push(
            recipients=None,
            title=title,
            message=message,
            template_name=template_name,
            template_content=template_content,
            template_data=template_data,
            metadata={"event": "broadcast_push", **(metadata or {})},
            provider_name=provider_name,
            provider_operation=provider_operation,
            push_type="broadcast",
            priority=priority,
            badge_count=badge_count,
            deep_link=deep_link,
            broadcast=True,
        )

    async def send_silent_push(
        self,
        *,
        recipients: str | list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        provider_name: str | None = None,
        provider_operation: Callable[[Provider], Awaitable[Any]] | None = None,
        priority: str | None = None,
        badge_count: int | None = None,
        deep_link: str | None = None,
        broadcast: bool = False,
    ) -> dict[str, Any]:
        """Send a silent push notification."""
        return await self.send_push(
            recipients=recipients,
            title=None,
            message=None,
            template_name=None,
            template_content=None,
            template_data={},
            metadata={"event": "silent_push", **(metadata or {})},
            provider_name=provider_name,
            provider_operation=provider_operation,
            push_type="silent",
            priority=priority,
            badge_count=badge_count,
            deep_link=deep_link,
            silent=True,
            broadcast=broadcast,
        )

    def _normalize_recipients(self, recipients: str | list[str] | None, broadcast: bool = False) -> list[str]:
        if recipients is None:
            if broadcast:
                return []
            raise ValidationException("At least one valid push recipient is required.")

        if isinstance(recipients, str):
            recipients = [recipients]

        normalized = [recipient.strip() for recipient in recipients if isinstance(recipient, str) and recipient.strip()]
        if not normalized and not broadcast:
            raise ValidationException("At least one valid push recipient is required.")
        return normalized

    def _validate_provider_operation(self, provider_operation: Callable[[Provider], Awaitable[Any]] | None) -> None:
        if provider_operation is None:
            raise ValidationException("A provider operation callback is required for push delivery.")

    def _validate_push_payload(self, payload: dict[str, Any]) -> None:
        if not payload:
            raise ValidationException("Push payload cannot be empty.")
        if not payload.get("broadcast") and not payload.get("to"):
            raise ValidationException("Push payload requires recipients or broadcast mode.")
        if not payload.get("silent") and not payload.get("message") and not payload.get("title"):
            raise ValidationException("Push payload requires a title or message for non-silent notifications.")

    def _render_payload(self, template_content: str | None, template_data: dict[str, Any] | None) -> str | None:
        if template_content is None:
            return None
        if template_data is None:
            return template_content
        try:
            return Template(template_content).safe_substitute(template_data)
        except Exception as exc:
            raise NotificationException(detail=f"Push template rendering failed: {exc}") from exc

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
        push_type: str | None,
        priority: str | None,
        badge_count: int | None,
        deep_link: str | None,
        broadcast: bool,
        silent: bool,
        provider_response: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "status": provider_response.get("status", "sent"),
            "provider": provider_response.get("provider"),
            "provider_reference": provider_response.get("provider_reference"),
            "message_id": provider_response.get("message_id"),
            "recipients": recipients,
            "push_type": push_type,
            "priority": priority,
            "badge_count": badge_count,
            "deep_link": deep_link,
            "broadcast": broadcast,
            "silent": silent,
            "sent_at": self._now_iso(),
            "notification_id": self._coerce_string(provider_response.get("provider_reference") or provider_response.get("message_id")),
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


__all__ = ["PushNotificationService"]
