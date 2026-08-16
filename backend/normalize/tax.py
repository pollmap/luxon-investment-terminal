from __future__ import annotations

from decimal import Decimal


DEFAULT_US_STATUTORY_RATE = Decimal("0.21")
MIN_REASONABLE_RATE = Decimal("0.00")
MAX_REASONABLE_RATE = Decimal("0.40")


def effective_tax_rate(
    pretax_income: Decimal | None,
    tax_expense: Decimal | None,
    fallback_rate: Decimal = DEFAULT_US_STATUTORY_RATE,
) -> tuple[Decimal, list[str]]:
    flags: list[str] = []
    if pretax_income is None or pretax_income <= 0:
        return fallback_rate, ["missing_pretax_income"]
    if tax_expense is None:
        return fallback_rate, ["missing_tax_expense"]
    rate = tax_expense / pretax_income
    if rate < MIN_REASONABLE_RATE or rate > MAX_REASONABLE_RATE:
        flags.append("abnormal_effective_tax_rate")
        rate = max(MIN_REASONABLE_RATE, min(MAX_REASONABLE_RATE, rate))
    return rate, flags


def after_tax_impact(
    pretax_amount: Decimal | None,
    sign: int,
    tax_effect: Decimal | None = None,
    net_of_tax_amount: Decimal | None = None,
    tax_rule: str = "after_tax",
    effective_rate: Decimal = DEFAULT_US_STATUTORY_RATE,
) -> tuple[Decimal | None, list[str]]:
    flags: list[str] = []
    if sign == 0:
        return Decimal("0"), flags

    if tax_rule == "direct":
        if net_of_tax_amount is not None:
            return Decimal(sign) * abs(net_of_tax_amount), ["net_of_tax_amount"]
        if pretax_amount is not None:
            return Decimal(sign) * abs(pretax_amount), ["direct_tax_item"]
        return None, ["missing_direct_tax_amount"]

    if tax_rule in {"net_of_tax", "no_tax_benefit"}:
        if net_of_tax_amount is not None:
            return Decimal(sign) * abs(net_of_tax_amount), ["net_of_tax_amount"]
        if pretax_amount is None:
            return None, ["missing_pretax_amount"]
        if tax_rule == "no_tax_benefit":
            return Decimal(sign) * abs(pretax_amount), ["no_tax_benefit_assumed"]

    if net_of_tax_amount is not None:
        return Decimal(sign) * abs(net_of_tax_amount), ["net_of_tax_amount"]

    if pretax_amount is None:
        return None, ["missing_pretax_amount"]

    if tax_effect is not None:
        return Decimal(sign) * (abs(pretax_amount) - abs(tax_effect)), ["explicit_tax_effect"]

    inferred_tax = abs(pretax_amount) * effective_rate
    flags.append("inferred_tax_effect")
    return Decimal(sign) * (abs(pretax_amount) - inferred_tax), flags

