from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class ForecastSource(StrEnum):
    CONSENSUS_SNAPSHOT = "consensus_snapshot"
    DETERMINISTIC_TREND = "deterministic_trend"
    USER_INPUT = "user_input"
    AI_ASSISTED_REVIEW = "ai_assisted_review"


@dataclass(frozen=True)
class ForecastAssumption:
    start_year: int
    start_metric: Decimal
    start_price: Decimal
    years: int = 5
    annual_growth_rate_pct: Decimal = Decimal("8")
    target_multiple: Decimal = Decimal("15")
    annual_dividend: Decimal = Decimal("0")
    source: ForecastSource = ForecastSource.USER_INPUT
    source_trace: dict | None = None


@dataclass(frozen=True)
class ForecastPoint:
    fiscal_year: int
    forecast_year: int
    metric: Decimal
    target_price: Decimal
    dividend: Decimal
    price_cagr_pct: Decimal
    total_return_cagr_pct: Decimal
    source: ForecastSource
    source_trace: dict | None


@dataclass(frozen=True)
class ForecastCalculationPoint:
    fiscal_year: int
    target_price: Decimal


@dataclass(frozen=True)
class ForecastCalculationLine:
    multiple: Decimal
    label: str
    points: list[ForecastCalculationPoint]


def build_forecast(assumption: ForecastAssumption) -> list[ForecastPoint]:
    if assumption.years < 1 or assumption.years > 5:
        raise ValueError("Forecast horizon must be between 1 and 5 years for MVP")
    growth = assumption.annual_growth_rate_pct / Decimal("100")
    points: list[ForecastPoint] = []
    for offset in range(1, assumption.years + 1):
        metric = (assumption.start_metric * ((Decimal("1") + growth) ** offset)).quantize(
            Decimal("0.01")
        )
        target_price = (metric * assumption.target_multiple).quantize(Decimal("0.01"))
        cumulative_dividend = assumption.annual_dividend * Decimal(offset)
        price_cagr = _cagr(assumption.start_price, target_price, offset)
        total_cagr = _cagr(assumption.start_price, target_price + cumulative_dividend, offset)
        points.append(
            ForecastPoint(
                fiscal_year=assumption.start_year + offset,
                forecast_year=offset,
                metric=metric,
                target_price=target_price,
                dividend=assumption.annual_dividend,
                price_cagr_pct=price_cagr,
                total_return_cagr_pct=total_cagr,
                source=assumption.source,
                source_trace=assumption.source_trace,
            )
        )
    return points


def build_manual_forecast(
    assumption: ForecastAssumption,
    manual_metric_values: list[Decimal | None],
    source: ForecastSource | None = None,
) -> list[ForecastPoint]:
    if assumption.years < 1 or assumption.years > 5:
        raise ValueError("Forecast horizon must be between 1 and 5 years for MVP")
    growth = assumption.annual_growth_rate_pct / Decimal("100")
    points: list[ForecastPoint] = []
    previous_metric = assumption.start_metric
    for offset in range(1, assumption.years + 1):
        manual_metric = (
            manual_metric_values[offset - 1]
            if offset - 1 < len(manual_metric_values)
            else None
        )
        if manual_metric is not None and manual_metric > 0:
            metric = manual_metric.quantize(Decimal("0.01"))
        else:
            metric = (previous_metric * (Decimal("1") + growth)).quantize(Decimal("0.01"))
        target_price = (metric * assumption.target_multiple).quantize(Decimal("0.01"))
        cumulative_dividend = assumption.annual_dividend * Decimal(offset)
        points.append(
            ForecastPoint(
                fiscal_year=assumption.start_year + offset,
                forecast_year=offset,
                metric=metric,
                target_price=target_price,
                dividend=assumption.annual_dividend,
                price_cagr_pct=_cagr(assumption.start_price, target_price, offset),
                total_return_cagr_pct=_cagr(
                    assumption.start_price,
                    target_price + cumulative_dividend,
                    offset,
                ),
                source=source or ForecastSource.USER_INPUT,
                source_trace=assumption.source_trace,
            )
        )
        previous_metric = metric
    return points


def build_calculation_lines(
    points: list[ForecastPoint],
    center_multiple: Decimal,
    line_count: int = 11,
    step: Decimal = Decimal("1"),
) -> list[ForecastCalculationLine]:
    if line_count <= 0:
        return []
    midpoint = line_count // 2
    lines: list[ForecastCalculationLine] = []
    for index in range(line_count):
        offset = Decimal(index - midpoint) * step
        multiple = max(Decimal("1"), center_multiple + offset).quantize(Decimal("0.01"))
        lines.append(
            ForecastCalculationLine(
                multiple=multiple,
                label=f"{_multiple_label(multiple)}x",
                points=[
                    ForecastCalculationPoint(
                        fiscal_year=point.fiscal_year,
                        target_price=(point.metric * multiple).quantize(Decimal("0.01")),
                    )
                    for point in points
                ],
            )
        )
    return lines


def _cagr(start: Decimal, end: Decimal, years: int) -> Decimal:
    if start <= 0 or end <= 0 or years <= 0:
        return Decimal("0")
    return (((end / start) ** (Decimal("1") / Decimal(years))) - Decimal("1")) * Decimal("100")


def _multiple_label(multiple: Decimal) -> str:
    return format(multiple, "f").rstrip("0").rstrip(".")
