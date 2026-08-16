from decimal import Decimal

from packages.valuation.engine import ValuationPoint, build_valuation_map
from packages.valuation.forecast import ForecastAssumption, build_calculation_lines, build_forecast, build_manual_forecast


def test_valuation_map_calculates_yoy_and_fair_value():
    rows = build_valuation_map(
        [
            ValuationPoint(2023, Decimal("5"), Decimal("100"), Decimal("1")),
            ValuationPoint(2024, Decimal("6"), Decimal("120"), Decimal("1")),
        ]
    )
    assert rows[1].yoy == Decimal("20.00")
    assert rows[1].dividend == Decimal("1")
    assert rows[1].fair_value_price > 0
    assert rows[1].normal_multiple == Decimal("20.00")


def test_valuation_map_uses_selected_normal_multiple_window():
    rows = build_valuation_map(
        [
            ValuationPoint(2020, Decimal("5"), Decimal("50"), Decimal("1")),
            ValuationPoint(2021, Decimal("5"), Decimal("100"), Decimal("1")),
            ValuationPoint(2022, Decimal("5"), Decimal("150"), Decimal("1")),
        ],
        normal_multiple_years=2,
    )
    assert rows[-1].normal_multiple == Decimal("25.00")


def test_forecast_builds_one_to_five_year_projection():
    rows = build_forecast(
        ForecastAssumption(
            start_year=2024,
            start_metric=Decimal("6"),
            start_price=Decimal("120"),
            years=5,
            annual_growth_rate_pct=Decimal("10"),
            target_multiple=Decimal("20"),
            annual_dividend=Decimal("1"),
        )
    )
    assert len(rows) == 5
    assert rows[0].fiscal_year == 2025
    assert rows[-1].forecast_year == 5
    assert rows[-1].target_price > Decimal("120")


def test_manual_forecast_and_calculation_lines():
    assumption = ForecastAssumption(
        start_year=2024,
        start_metric=Decimal("6"),
        start_price=Decimal("120"),
        years=3,
        annual_growth_rate_pct=Decimal("8"),
        target_multiple=Decimal("20"),
        annual_dividend=Decimal("1"),
    )
    rows = build_manual_forecast(assumption, [Decimal("7.50"), None, Decimal("9.25")])
    assert rows[0].metric == Decimal("7.50")
    assert rows[1].metric == Decimal("8.10")
    assert rows[2].metric == Decimal("9.25")

    lines = build_calculation_lines(rows, Decimal("20"))
    assert len(lines) == 11
    assert lines[5].multiple == Decimal("20.00")
    assert lines[5].label == "20x"
    assert lines[5].points[-1].target_price == Decimal("185.00")
