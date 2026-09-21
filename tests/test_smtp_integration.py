from __future__ import annotations

import logging
import smtplib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import SecretStr

from app.config.mail import SMTPEmailProvider
from app.config.settings import settings
from app.services.auth_service import AuthService
from app.services.notification.email import EmailNotificationService
from app.services.notification.templates import render_cosmozpay_email
from app.utils.exceptions import DatabaseException, NotificationException


@pytest.mark.asyncio
async def test_smtp_configuration_uses_secret_and_configured_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_port", 465)
    monkeypatch.setattr(settings, "smtp_username", "mailer")
    monkeypatch.setattr(settings, "smtp_password", SecretStr("secret"))
    monkeypatch.setattr(settings, "smtp_from_email", "noreply@example.com")
    monkeypatch.setattr(settings, "smtp_timeout_seconds", 4.5)

    provider = SMTPEmailProvider()

    assert provider.host == "smtp.example.com"
    assert provider.port == 465
    assert provider.timeout == 4.5
    assert provider.password == "secret"
    assert "secret" not in repr(settings.smtp_password)


@pytest.mark.asyncio
async def test_smtp_provider_sends_successfully_with_tls_and_auth() -> None:
    provider = SMTPEmailProvider(
        host="smtp.example.com",
        port=587,
        username="mailer",
        password="secret",
        from_email="noreply@example.com",
        timeout=3,
    )
    smtp_client = MagicMock()

    with patch("app.config.mail.smtplib.SMTP") as smtp_factory:
        smtp_factory.return_value.__enter__.return_value = smtp_client
        result = await provider.send_email(to="user@example.com", subject="Reset", body="Use the link")

    smtp_client.starttls.assert_called_once_with()
    smtp_client.login.assert_called_once_with("mailer", "secret")
    smtp_client.sendmail.assert_called_once()
    assert result["status"] == "sent"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        smtplib.SMTPAuthenticationError(535, b"authentication failed"),
        ConnectionError("connection failed"),
        TimeoutError("timed out"),
    ],
)
async def test_smtp_provider_failures_are_not_reported_as_success(error: Exception) -> None:
    provider = SMTPEmailProvider(host="smtp.example.com", port=587, from_email="noreply@example.com")
    smtp_client = MagicMock()
    smtp_client.sendmail.side_effect = error

    with patch("app.config.mail.smtplib.SMTP") as smtp_factory:
        smtp_factory.return_value.__enter__.return_value = smtp_client
        with pytest.raises(RuntimeError, match="SMTP email delivery failed"):
            await provider.send_email(to="user@example.com", subject="Reset", body="Use the link")


@pytest.mark.asyncio
async def test_email_service_reaches_default_smtp_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mail_provider", "smtp")
    smtp_send = AsyncMock(return_value={"status": "sent", "message_id": "smtp-1"})
    provider_service = SimpleNamespace(execute_email=AsyncMock())

    async def execute_email(**kwargs):
        return {"status": "sent", "provider": "smtp", "message_id": "smtp-1"}

    provider_service.execute_email.side_effect = execute_email
    email_service = EmailNotificationService(provider_service=provider_service)

    with patch("app.services.notification.email.SMTPEmailProvider") as smtp_factory:
        smtp_factory.return_value.send_email = smtp_send
        result = await email_service.send_password_reset_email(
            recipients="user@example.com",
            reset_url="https://frontend.example/reset-password?token=opaque",
            token="opaque",
            user_name="Ada",
            expires_in_minutes=30,
        )
        assert result["status"] == "sent"
        smtp_send.assert_not_awaited()
        operation = provider_service.execute_email.call_args.kwargs["operation"]
        await operation(SimpleNamespace(name="smtp"))
        smtp_send.assert_awaited_once()
        assert "opaque" in smtp_send.call_args.kwargs["body"]


@pytest.mark.asyncio
async def test_forgot_password_wires_notification_and_configured_reset_url(monkeypatch: pytest.MonkeyPatch) -> None:
    user = SimpleNamespace(
        id="user-1",
        email="ada@example.com",
        first_name="Ada",
        username="ada",
        is_active=True,
        is_blocked=False,
        is_suspended=False,
        status="active",
    )
    password_service = SimpleNamespace(generate_password_reset_token=AsyncMock(return_value="opaque-token"))
    notification_service = SimpleNamespace(send_password_reset_email=AsyncMock())
    service = AuthService(
        user_repository=object(),
        session=object(),
        password_service=password_service,
        notification_service=notification_service,
    )
    service._get_user_by_identifier = AsyncMock(return_value=user)
    service._log_event = AsyncMock()
    monkeypatch.setattr(settings, "frontend_url", "https://app.example")

    result = await service.forgot_password(email="ada@example.com")

    assert result["message"].startswith("If an account exists")
    email_kwargs = notification_service.send_password_reset_email.call_args.kwargs
    reset_url = email_kwargs["reset_url"]
    assert urlparse(reset_url).scheme == "https"
    assert urlparse(reset_url).path == "/reset-password"
    assert parse_qs(urlparse(reset_url).query)["token"] == ["opaque-token"]
    assert email_kwargs["token"] == "opaque-token"
    assert "opaque-token" not in " ".join(str(call) for call in service._log_event.call_args_list)


@pytest.mark.asyncio
async def test_password_reset_notification_failure_is_wrapped() -> None:
    user = SimpleNamespace(
        id="user-1",
        email="ada@example.com",
        first_name="Ada",
        username=None,
        is_active=True,
        is_blocked=False,
        is_suspended=False,
        status="active",
    )
    service = AuthService(
        user_repository=object(),
        session=object(),
        password_service=SimpleNamespace(generate_password_reset_token=AsyncMock(return_value="opaque-token")),
        notification_service=SimpleNamespace(send_password_reset_email=AsyncMock(side_effect=NotificationException())),
    )
    service._get_user_by_identifier = AsyncMock(return_value=user)
    service._log_event = AsyncMock()

    with pytest.raises(DatabaseException) as raised:
        await service.forgot_password(email="ada@example.com")

    assert "SMTP" not in str(raised.value.detail)
    assert "opaque-token" not in str(raised.value)


def test_render_cosmozpay_welcome_email_has_branding_and_cta() -> None:
    html, text = render_cosmozpay_email(
        subject="Welcome to CosmozPay — Pay Bills with Ease",
        title="Welcome to CosmozPay!",
        greeting="Hello Ada,",
        body_lines=[
            "Pay bills with ease, securely and conveniently.",
            "Thank you for joining CosmozPay.",
        ],
        cta_text="Get Started",
        cta_url="https://app.example/get-started",
        footer_note="You are receiving this email because you created an account with CosmozPay.",
    )

    assert "CosmozPay" in html
    assert "Get Started" in html
    assert "https://app.example/get-started" in html
    assert "#5B21B6" in html
    assert "data:image/" in html
    assert "Welcome to CosmozPay!" in text
    assert "https://app.example/get-started" in text


def test_render_cosmozpay_email_escapes_untrusted_content() -> None:
    html, _ = render_cosmozpay_email(
        subject="Hello",
        title="<script>alert('x')</script>",
        greeting="<b>Hi</b>",
        body_lines=["<img src=x onerror=alert(1)>"],
        cta_text="<Click Me>",
        cta_url="https://app.example/?q=<tag>",
    )

    assert "&lt;script&gt;" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "&lt;Click Me&gt;" in html
    assert "&lt;tag&gt;" in html
    assert "<script>" not in html


def test_render_cosmozpay_email_handles_missing_optional_values() -> None:
    html, text = render_cosmozpay_email(
        subject="Password reset",
        title="Reset your password",
        body_lines=["Use the link below to continue."],
    )

    assert "CosmozPay" in html
    assert "Reset your password" in html
    assert "Use the link below to continue." in text
    assert "Get Started" not in html
    assert "Get Started" not in text
