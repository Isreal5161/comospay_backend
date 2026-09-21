from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage
from typing import Any

from app.config.settings import settings

logger = logging.getLogger(__name__)


class SMTPEmailProvider:
    """SMTP-backed email provider that stays isolated to the mail configuration layer."""

    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        username: str | None = None,
        password: str | None = None,
        from_email: str | None = None,
        use_tls: bool = True,
        timeout: float | None = None,
    ) -> None:
        self.host = (settings.smtp_host if host is None else host or "").strip()
        self.port = int(settings.smtp_port if port is None else port)
        self.username = (settings.smtp_username if username is None else username or "").strip() or None
        configured_password = settings.smtp_password.get_secret_value() if settings.smtp_password else None
        self.password = (configured_password if password is None else password or "").strip() or None
        configured_from_email = settings.smtp_from_email or self.username
        self.from_email = (configured_from_email if from_email is None else from_email or "").strip() or None
        self.use_tls = use_tls
        self.timeout = float(timeout or settings.smtp_timeout_seconds)

    def validate_config(self) -> None:
        if not self.host:
            raise ValueError("SMTP host is not configured.")
        if not self.port:
            raise ValueError("SMTP port is not configured.")
        if not self.from_email:
            raise ValueError("SMTP sender address is not configured.")

    def _build_message(self, *, to: list[str], subject: str, body: str, html_body: str | None = None) -> EmailMessage:
        message = EmailMessage()
        message["From"] = self.from_email
        message["To"] = ", ".join(to)
        message["Subject"] = subject
        if html_body:
            message.set_content(body)
            message.add_alternative(html_body, subtype="html")
        else:
            message.set_content(body)
        return message

    def _send_message_sync(
        self,
        *,
        recipients: list[str],
        message: EmailMessage,
    ) -> None:
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp_client:
            if self.use_tls:
                smtp_client.starttls()
            if self.username and self.password:
                smtp_client.login(self.username, self.password)
            smtp_client.sendmail(self.from_email, recipients, message.as_string())

    async def send_email(
        self,
        *,
        to: str | list[str],
        subject: str,
        body: str,
        html_body: str | None = None,
        cc: str | list[str] | None = None,
        bcc: str | list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        recipients = [item.strip() for item in ([to] if isinstance(to, str) else to) if item and item.strip()]
        if not recipients:
            raise ValueError("At least one recipient is required.")

        self.validate_config()
        message = self._build_message(to=recipients, subject=subject, body=body, html_body=html_body)
        if cc:
            cc_list = [item.strip() for item in ([cc] if isinstance(cc, str) else cc) if item and item.strip()]
            if cc_list:
                message["Cc"] = ", ".join(cc_list)
                recipients.extend(cc_list)
        if bcc:
            bcc_list = [item.strip() for item in ([bcc] if isinstance(bcc, str) else bcc) if item and item.strip()]
            if bcc_list:
                recipients.extend(bcc_list)

        try:
            await asyncio.to_thread(self._send_message_sync, recipients=recipients, message=message)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("SMTP email delivery failed")
            raise RuntimeError(f"SMTP email delivery failed: {exc}") from exc

        return {
            "status": "sent",
            "provider": "smtp",
            "recipients": recipients,
            "subject": subject,
            "message_id": None,
            "metadata": metadata or {},
        }


def send_email_via_smtp(
    *,
    to: str | list[str],
    subject: str,
    body: str,
    html_body: str | None = None,
    host: str | None = None,
    port: int | None = None,
    username: str | None = None,
    password: str | None = None,
    from_email: str | None = None,
    cc: str | list[str] | None = None,
    bcc: str | list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convenience wrapper for a direct SMTP send while preserving the existing mail config surface."""
    provider = SMTPEmailProvider(
        host=host,
        port=port,
        username=username,
        password=password,
        from_email=from_email,
    )
    provider.validate_config()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            provider.send_email(
                to=to,
                subject=subject,
                body=body,
                html_body=html_body,
                cc=cc,
                bcc=bcc,
                metadata=metadata,
            )
        )

    result_container: dict[str, Any] = {}

    def runner() -> None:
        result_container.update(asyncio.run(provider.send_email(
            to=to,
            subject=subject,
            body=body,
            html_body=html_body,
            cc=cc,
            bcc=bcc,
            metadata=metadata,
        )))

    import threading

    thread = threading.Thread(target=runner)
    thread.start()
    thread.join()
    return result_container


__all__ = ["SMTPEmailProvider", "send_email_via_smtp"]
