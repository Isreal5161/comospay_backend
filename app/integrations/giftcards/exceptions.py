from __future__ import annotations


class SogoProviderUnavailableError(Exception):
    """Raised when Sogo provider is currently unavailable for execution."""


class SogoProviderTemporaryFailure(Exception):
    """Raised when Sogo provider fails temporarily and execution can fail over."""
