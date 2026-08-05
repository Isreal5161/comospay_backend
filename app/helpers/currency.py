from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Final


CENT: Final[Decimal] = Decimal("0.01")
DEFAULT_CURRENCY: Final[str] = "NGN"


class CurrencyFormatter:
    """Format monetary values using Decimal-based operations."""

    def __init__(self, code: str = DEFAULT_CURRENCY, symbol: str = "₦") -> None:
        self.code = code.upper()
        self.symbol = symbol

    def round_amount(self, amount: Decimal, places: int = 2) -> Decimal:
        """Round a monetary amount to the requested number of decimal places."""
        quantizer = Decimal("1").scaleb(-places)
        return amount.quantize(quantizer, rounding=ROUND_HALF_UP)

    def format(self, amount: Decimal, *, places: int = 2, include_symbol: bool = True) -> str:
        """Format a monetary amount as a string."""
        rounded = self.round_amount(amount, places)
        if include_symbol:
            return f"{self.symbol}{rounded:,.2f}"
        return f"{rounded:,.2f}"

    def format_ngn(self, amount: Decimal) -> str:
        """Format an amount as Nigerian Naira."""
        return self.format(amount, places=2, include_symbol=True)


def parse_currency(value: str | Decimal | int) -> Decimal:
    """Parse a value into a Decimal without using float."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace(" ", "")
        if cleaned.startswith("₦") or cleaned.startswith("NGN"):
            cleaned = cleaned[3:] if cleaned.startswith("NGN") else cleaned[1:]
        return Decimal(cleaned)
    raise TypeError("Unsupported currency value type")


def round_currency(amount: Decimal | str | int, places: int = 2) -> Decimal:
    """Round a monetary value using Decimal and half-up semantics."""
    parsed = parse_currency(amount)
    quantizer = Decimal("1").scaleb(-places)
    return parsed.quantize(quantizer, rounding=ROUND_HALF_UP)


def format_currency(amount: Decimal | str | int, *, code: str = DEFAULT_CURRENCY, symbol: str | None = None, places: int = 2) -> str:
    """Format a monetary value for the requested currency."""
    formatter = CurrencyFormatter(code=code, symbol=symbol or "₦")
    return formatter.format(parse_currency(amount), places=places, include_symbol=True)


def format_ngn(amount: Decimal | str | int) -> str:
    """Format a value as Nigerian Naira."""
    return format_currency(amount, code="NGN", symbol="₦")
