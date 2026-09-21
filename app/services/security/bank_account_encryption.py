from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from collections.abc import Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config.settings import settings
from app.utils.exceptions import ValidationException


class BankAccountEncryption:
    """Encrypt bank-account numbers with a versioned AES-GCM envelope."""

    _ENVELOPE_PATTERN = re.compile(r"^v([1-9][0-9]*):([A-Za-z0-9_-]+)$")

    def __init__(self, *, keys: Mapping[int, bytes] | None = None, current_version: int | None = None) -> None:
        configured_key = settings.bank_account_encryption_key.get_secret_value().strip() if settings.bank_account_encryption_key else ""
        version = current_version or settings.bank_account_encryption_key_version
        self._keys = dict(keys or ({version: self._decode_key(configured_key)} if configured_key else {}))
        self._current_version = version

    def encrypt(self, account_number: str) -> str:
        self._validate_account_number(account_number)
        key = self._keys.get(self._current_version)
        if key is None:
            raise ValidationException("Bank account encryption is not configured.")
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(key).encrypt(nonce, account_number.encode("utf-8"), None)
        encoded = base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii").rstrip("=")
        return f"v{self._current_version}:{encoded}"

    def decrypt(self, envelope: str) -> str:
        match = self._ENVELOPE_PATTERN.fullmatch(envelope or "")
        if match is None:
            raise ValidationException("Bank account details are unavailable.")
        version = int(match.group(1))
        key = self._keys.get(version)
        if key is None:
            raise ValidationException("Bank account details are unavailable.")
        try:
            padded = match.group(2) + "=" * (-len(match.group(2)) % 4)
            payload = base64.urlsafe_b64decode(padded.encode("ascii"))
            if len(payload) <= 12:
                raise ValueError
            plaintext = AESGCM(key).decrypt(payload[:12], payload[12:], None).decode("utf-8")
        except (InvalidTag, ValueError, UnicodeDecodeError, TypeError):
            raise ValidationException("Bank account details are unavailable.") from None
        self._validate_account_number(plaintext)
        return plaintext

    def fingerprint(self, account_number: str) -> str:
        self._validate_account_number(account_number)
        key = self._keys.get(self._current_version)
        if key is None:
            raise ValidationException("Bank account encryption is not configured.")
        fingerprint_key = hashlib.sha256(b"cosmozpay:bank-account-fingerprint:" + key).digest()
        return hmac.new(fingerprint_key, account_number.encode("utf-8"), hashlib.sha256).hexdigest()

    def is_encrypted(self, value: str | None) -> bool:
        return bool(value and self._ENVELOPE_PATTERN.fullmatch(value))

    @staticmethod
    def _decode_key(value: str) -> bytes:
        try:
            padded = value + "=" * (-len(value) % 4)
            key = base64.urlsafe_b64decode(padded.encode("ascii"))
        except (ValueError, UnicodeDecodeError):
            key = b""
        if len(key) != 32:
            raise ValueError("BANK_ACCOUNT_ENCRYPTION_KEY must be a URL-safe base64-encoded 32-byte key.")
        return key

    @staticmethod
    def _validate_account_number(account_number: str) -> None:
        if not isinstance(account_number, str) or not account_number.isdigit() or len(account_number) != 10:
            raise ValidationException("Bank account details are invalid.")
