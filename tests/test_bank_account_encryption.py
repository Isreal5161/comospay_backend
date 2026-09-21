from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.services.security.bank_account_encryption import BankAccountEncryption
from app.utils.exceptions import ValidationException


@pytest.fixture
def encryption() -> BankAccountEncryption:
    return BankAccountEncryption(keys={1: bytes(range(32))}, current_version=1)


def test_round_trip_uses_unique_authenticated_envelopes(encryption: BankAccountEncryption) -> None:
    first = encryption.encrypt("1234567890")
    second = encryption.encrypt("1234567890")

    assert first != second
    assert first.startswith("v1:")
    assert encryption.decrypt(first) == "1234567890"
    assert encryption.decrypt(second) == "1234567890"
    assert "1234567890" not in first


def test_tampered_ciphertext_fails_closed(encryption: BankAccountEncryption) -> None:
    envelope = encryption.encrypt("1234567890")
    tampered = envelope[:-1] + ("A" if envelope[-1] != "A" else "B")

    with pytest.raises(ValidationException, match="unavailable"):
        encryption.decrypt(tampered)


def test_wrong_key_and_unknown_version_fail_closed(encryption: BankAccountEncryption) -> None:
    envelope = encryption.encrypt("1234567890")
    wrong_key = BankAccountEncryption(keys={1: bytes([1] * 32)}, current_version=1)
    unknown_version = envelope.replace("v1:", "v2:", 1)

    with pytest.raises(ValidationException, match="unavailable"):
        wrong_key.decrypt(envelope)
    with pytest.raises(ValidationException, match="unavailable"):
        encryption.decrypt(unknown_version)


def test_fingerprint_is_stable_without_storing_plaintext(encryption: BankAccountEncryption) -> None:
    assert encryption.fingerprint("1234567890") == encryption.fingerprint("1234567890")
    assert encryption.fingerprint("1234567890") != encryption.fingerprint("0234567890")
