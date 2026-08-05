from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Final


CENT: Final[Decimal] = Decimal("0.01")


class ChargeRule:
    """Represents a fee rule for a charge calculation."""

    def __init__(self, *, flat_fee: Decimal | None = None, percent_fee: Decimal | None = None, minimum_fee: Decimal | None = None, maximum_fee: Decimal | None = None, discount: Decimal | None = None) -> None:
        self.flat_fee = flat_fee or Decimal("0")
        self.percent_fee = percent_fee or Decimal("0")
        self.minimum_fee = minimum_fee or Decimal("0")
        self.maximum_fee = maximum_fee or Decimal("0")
        self.discount = discount or Decimal("0")

    def calculate(self, amount: Decimal) -> Decimal:
        """Calculate the fee for the provided amount based on the rule."""
        if amount < 0:
            raise ValueError("Amount cannot be negative")

        percentage_charge = (amount * self.percent_fee / Decimal("100")) if self.percent_fee else Decimal("0")
        fee = self.flat_fee + percentage_charge

        if self.minimum_fee and fee < self.minimum_fee:
            fee = self.minimum_fee

        if self.maximum_fee and fee > self.maximum_fee:
            fee = self.maximum_fee

        if self.discount:
            fee = fee - (fee * self.discount / Decimal("100"))

        return fee.quantize(CENT, rounding=ROUND_HALF_UP)


class ChargeCalculator:
    """Reusable calculator for applying fee rules to monetary values."""

    def __init__(self, rules: dict[str, ChargeRule] | None = None) -> None:
        self._rules = rules or {}

    def register_rule(self, name: str, rule: ChargeRule) -> None:
        """Register or update a fee rule by name."""
        self._rules[name] = rule

    def calculate(self, name: str, amount: Decimal) -> Decimal:
        """Calculate the fee for a registered rule and amount."""
        if name not in self._rules:
            raise KeyError(f"No charge rule registered for '{name}'")
        return self._rules[name].calculate(amount)


def calculate_wallet_funding_fee(amount: Decimal, *, rule: ChargeRule | None = None) -> Decimal:
    """Calculate wallet funding fee for a funding amount."""
    active_rule = rule or ChargeRule(flat_fee=Decimal("0"), percent_fee=Decimal("0"))
    return active_rule.calculate(amount)


def calculate_transfer_fee(amount: Decimal, *, rule: ChargeRule | None = None) -> Decimal:
    """Calculate internal transfer fee for a transfer amount."""
    active_rule = rule or ChargeRule(flat_fee=Decimal("0"), percent_fee=Decimal("0"))
    return active_rule.calculate(amount)


def calculate_airtime_charge(amount: Decimal, *, rule: ChargeRule | None = None) -> Decimal:
    """Calculate airtime purchase charge."""
    active_rule = rule or ChargeRule(flat_fee=Decimal("0"), percent_fee=Decimal("0"))
    return active_rule.calculate(amount)


def calculate_data_charge(amount: Decimal, *, rule: ChargeRule | None = None) -> Decimal:
    """Calculate data purchase charge."""
    active_rule = rule or ChargeRule(flat_fee=Decimal("0"), percent_fee=Decimal("0"))
    return active_rule.calculate(amount)


def calculate_electricity_charge(amount: Decimal, *, rule: ChargeRule | None = None) -> Decimal:
    """Calculate electricity bill charge."""
    active_rule = rule or ChargeRule(flat_fee=Decimal("0"), percent_fee=Decimal("0"))
    return active_rule.calculate(amount)


def calculate_tv_charge(amount: Decimal, *, rule: ChargeRule | None = None) -> Decimal:
    """Calculate TV subscription charge."""
    active_rule = rule or ChargeRule(flat_fee=Decimal("0"), percent_fee=Decimal("0"))
    return active_rule.calculate(amount)


def calculate_giftcard_charge(amount: Decimal, *, rule: ChargeRule | None = None) -> Decimal:
    """Calculate gift card purchase charge."""
    active_rule = rule or ChargeRule(flat_fee=Decimal("0"), percent_fee=Decimal("0"))
    return active_rule.calculate(amount)
