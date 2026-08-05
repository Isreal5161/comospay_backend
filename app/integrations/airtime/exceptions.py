from __future__ import annotations


class ProviderUnavailableError(Exception):
    """Raised when a provider is currently unavailable for execution."""


class ProviderTemporaryFailure(Exception):
    """Raised when a provider fails temporarily and execution can fail over."""


class NoProviderAvailableError(Exception):
    """Raised when no enabled provider can successfully complete an operation."""
