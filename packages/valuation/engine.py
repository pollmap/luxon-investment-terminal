from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ValuationPoint:
    fiscal_year: int
    metric: Decimal
    price: Decimal
    dividend: Decimal
    forecast_flag: bool = False
    source_trace: dict | None = None


@dataclass(frozen=True)
class ValuationMapPoint:
    fiscal_year: int
    metric: Decimal
    price: Decimal
    dividend: Decimal
    yoy: Decimal | None
    normal_multiple: Decimal | None
    fair_multiple: Decimal
    fair_value_price: Decimal
    forecast_flag: bool
    source_trace: dict | None


def calculate_yoy(current: Decimal, previous: Decimal | None) -> Decimal | None:
    if previous in {None, Decimal("0")}:
        return None
    return ((current - previous) / abs(previous) * Decimal("100")).quantize(Decimal("0.01"))


def calculate_cagr(start: Decimal, end: Decimal, years: int) -> Decimal:
    if start <= 0 or end <= 0 or years <= 0:
        return Decimal("0")
    return ((end / start) ** (Decimal(1) / Decimal(years)) - 1) * Decimal("100")


def fair_multiple_for_growth(cagr_percent: Decimal) -> Decimal:
    candidate = max(Decimal("15"), cagr_percent)
    return min(Decimal("30"), max(Decimal("8"), candidate)).quantize(Decimal("0.01"))


def trimmed_mean(values: list[Decimal], trim_ratio: Decimal = Decimal("0.10")) -> Decimal | None:
    clean = sorted(value for value in values if value > 0)
    if not clean:
        return None
    trim = int(len(clean) * float(trim_ratio))
    trimmed = clean[trim : len(clean) - trim] if len(clean) - (2 * trim) > 0 else clean
    return (sum(trimmed) / Decimal(len(trimmed))).quantize(Decimal("0.01"))


def build_valuation_map(
    points: list[ValuationPoint],
    normal_multiple_years: int | None = None,
) -> list[ValuationMapPoint]:
    if not points:
        return []
    sorted_points = sorted(points, key=lambda item: item.fiscal_year)
    first = next((point for point in sorted_points if point.metric > 0), sorted_points[0])
    last = next((point for point in reversed(sorted_points) if point.metric > 0), sorted_points[-1])
    cagr = calculate_cagr(first.metric, last.metric, max(1, last.fiscal_year - first.fiscal_year))
    fair_multiple = fair_multiple_for_growth(cagr)
    multiple_points = [point for point in sorted_points if point.metric > 0]
    if normal_multiple_years is not None:
        window = max(1, min(20, int(normal_multiple_years)))
        multiple_points = multiple_points[-window:]
    multiples = [point.price / point.metric for point in multiple_points]
    normal_multiple = trimmed_mean(multiples)
    output: list[ValuationMapPoint] = []
    previous_metric: Decimal | None = None
    for point in sorted_points:
        yoy = calculate_yoy(point.metric, previous_metric)
        output.append(
            ValuationMapPoint(
                fiscal_year=point.fiscal_year,
                metric=point.metric,
                price=point.price,
                dividend=point.dividend,
                yoy=yoy,
                normal_multiple=normal_multiple,
                fair_multiple=fair_multiple,
                fair_value_price=(point.metric * fair_multiple).quantize(Decimal("0.01")),
                forecast_flag=point.forecast_flag,
                source_trace=point.source_trace,
            )
        )
        previous_metric = point.metric
    return output
