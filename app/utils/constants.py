from __future__ import annotations

from typing import Final


# User roles used across authentication and authorization flows.
USER_ROLES: Final[tuple[str, ...]] = (
    "super_admin",
    "admin",
    "agent",
    "user",
)


# Transaction lifecycle states used by payment and wallet services.
TRANSACTION_STATUS: Final[dict[str, str]] = {
    "PENDING": "PENDING",
    "SUCCESS": "SUCCESS",
    "FAILED": "FAILED",
    "REVERSED": "REVERSED",
}


# Provider identifiers used by integrations and routing logic.
PROVIDER_NAMES: Final[dict[str, str]] = {
    "FLUTTERWAVE": "flutterwave",
    "PAYSTACK": "paystack",
    "MONNIFY": "monnify",
    "KORAPAY": "korapay",
    "AIDAPAY": "aidapay",
    "VTUNG": "vtung",
    "CLUBKONNECT": "clubkonnect",
    "VTUGATE": "vtugate",
    "TERMII": "termii",
    "TWILIO": "twilio",
}


# Wallet lifecycle states used by wallet services.
WALLET_STATUS: Final[dict[str, str]] = {
    "ACTIVE": "ACTIVE",
    "INACTIVE": "INACTIVE",
    "SUSPENDED": "SUSPENDED",
}


# Notification categories used by the notification subsystem.
NOTIFICATION_TYPES: Final[dict[str, str]] = {
    "SMS": "sms",
    "EMAIL": "email",
    "PUSH": "push",
    "IN_APP": "in_app",
}


# Currency codes used by financial operations and provider integrations.
CURRENCY_CODES: Final[tuple[str, ...]] = (
    "NGN",
    "USD",
    "EUR",
    "GBP",
)


# OTP purpose values used by verification and authentication services.
OTP_TYPES: Final[dict[str, str]] = {
    "LOGIN": "login",
    "PASSWORD_RESET": "password_reset",
    "TRANSACTION": "transaction",
    "EMAIL_VERIFICATION": "email_verification",
    "PHONE_VERIFICATION": "phone_verification",
}
