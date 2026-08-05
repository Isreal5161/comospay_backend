from __future__ import annotations

import secrets
import string
import time
from typing import Final


DEFAULT_PREFIXES: Final[dict[str, str]] = {
    "transaction": "TRX",
    "payment": "PAY",
    "wallet": "WLT",
    "virtual_account": "VA",
    "provider": "PRV",
    "webhook": "WHK",
    "refund": "RFD",
}


def _generate_suffix(length: int = 6) -> str:
    """Create a secure random alphanumeric suffix."""
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _build_reference(prefix: str, *, include_timestamp: bool = True, suffix_length: int = 6) -> str:
    """Build a human-readable reference using a prefix, timestamp, and random suffix."""
    timestamp = str(int(time.time() * 1000))
    suffix = _generate_suffix(suffix_length)

    if include_timestamp:
        return f"{prefix}-{timestamp}-{suffix}"
    return f"{prefix}-{suffix}"


def generate_transaction_reference(prefix: str | None = None) -> str:
    """Generate a unique transaction reference."""
    return _build_reference(prefix or DEFAULT_PREFIXES["transaction"])


def generate_payment_reference(prefix: str | None = None) -> str:
    """Generate a unique payment reference."""
    return _build_reference(prefix or DEFAULT_PREFIXES["payment"])


def generate_wallet_reference(prefix: str | None = None) -> str:
    """Generate a unique wallet reference."""
    return _build_reference(prefix or DEFAULT_PREFIXES["wallet"])


def generate_virtual_account_reference(prefix: str | None = None) -> str:
    """Generate a unique virtual account reference."""
    return _build_reference(prefix or DEFAULT_PREFIXES["virtual_account"])


def generate_provider_reference(prefix: str | None = None) -> str:
    """Generate a unique provider reference."""
    return _build_reference(prefix or DEFAULT_PREFIXES["provider"])


def generate_webhook_reference(prefix: str | None = None) -> str:
    """Generate a unique webhook reference."""
    return _build_reference(prefix or DEFAULT_PREFIXES["webhook"])


def generate_refund_reference(prefix: str | None = None) -> str:
    """Generate a unique refund reference."""
    return _build_reference(prefix or DEFAULT_PREFIXES["refund"])
