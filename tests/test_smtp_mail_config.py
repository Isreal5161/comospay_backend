from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.config.mail import SMTPEmailProvider, send_email_via_smtp


@pytest.mark.asyncio
async def test_smtp_provider_builds_message_and_sends_via_smtp() -> None:
    provider = SMTPEmailProvider(
        host="smtp.example.com",
        port=587,
        username="mailer",
        password="secret",
        from_email="noreply@example.com",
    )

    smtp_client = MagicMock()
    smtp_client.sendmail.return_value = {}

    with patch("app.config.mail.smtplib.SMTP") as smtp_factory:
        smtp_factory.return_value.__enter__.return_value = smtp_client
        result = await provider.send_email(
            to=["user@example.com"],
            subject="Welcome",
            body="Hello world",
            html_body="<p>Hello world</p>",
        )

    assert result["status"] == "sent"
    assert result["provider"] == "smtp"
    assert result["recipients"] == ["user@example.com"]
    smtp_client.sendmail.assert_called_once()


def test_send_email_via_smtp_requires_configuration() -> None:
    provider = SMTPEmailProvider(host="", port=587)
    with pytest.raises(ValueError):
        provider.validate_config()

    with pytest.raises(ValueError):
        send_email_via_smtp(to=["user@example.com"], subject="Hi", body="Hello", host="", port=587)
