from decimal import Decimal

from backend.normalize.parsers.numeric import parse_decimal
from backend.normalize.parsers.period import parse_period
from backend.normalize.tax import after_tax_impact, effective_tax_rate


def test_parse_decimal_handles_parentheses_and_symbols():
    assert parse_decimal("($1,234.50)") == Decimal("-1234.50")
    assert parse_decimal("—") is None
    assert parse_decimal("—", dash_as_zero=True) == Decimal("0")


def test_parse_period_detects_fy():
    period = parse_period("Year ended September 28, 2024")
    assert period is not None
    assert period.fiscal_year == 2024
    assert period.fiscal_period == "FY"


def test_tax_effect_uses_explicit_tax():
    impact, flags = after_tax_impact(
        pretax_amount=Decimal("100"),
        tax_effect=Decimal("21"),
        sign=1,
    )
    assert impact == Decimal("79")
    assert flags == ["explicit_tax_effect"]


def test_tax_effect_goodwill_no_tax_benefit():
    impact, flags = after_tax_impact(
        pretax_amount=Decimal("100"),
        sign=1,
        tax_rule="no_tax_benefit",
    )
    assert impact == Decimal("100")
    assert "no_tax_benefit_assumed" in flags


def test_effective_tax_rate_clamps_extreme_rates():
    rate, flags = effective_tax_rate(Decimal("100"), Decimal("80"))
    assert rate == Decimal("0.40")
    assert flags == ["abnormal_effective_tax_rate"]

