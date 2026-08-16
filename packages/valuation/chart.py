from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from io import BytesIO, StringIO

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.figure import Figure


def chart_source_summary(series: list[dict], visibility: dict | None = None) -> dict:
    visibility = visibility or {}
    traces = [
        row.get("source_trace")
        for row in series
        if isinstance(row.get("source_trace"), dict)
    ]
    source_docs = {
        value
        for value in (_trace_record_text(trace, "source_document_id") for trace in traces)
        if value
    }
    filings = {
        value
        for value in (
            _trace_record_text(trace, "filing_id")
            or _trace_record_text(trace, "accession_number")
            for trace in traces
        )
        if value
    }
    available_at = sorted(
        value
        for value in (_trace_record_text(trace, "available_at") for trace in traces)
        if value
    )
    return {
        "metric": visibility.get("metric") or "metric",
        "metric_label": visibility.get("metric_label") or visibility.get("metric") or "metric",
        "data_mode": visibility.get("data_mode") or "unknown",
        "data_backend": visibility.get("data_backend") or "unknown",
        "methods": _unique_compact_list(
            _trace_record_text(trace, "method")
            or _trace_record_text(trace, "source_type")
            or _trace_record_text(trace, "source")
            for trace in traces
        ),
        "sources": _unique_compact_list(
            _trace_record_text(trace, "source")
            or _trace_record_text(trace, "source_type")
            for trace in traces
        ),
        "quality_statuses": _unique_compact_list(
            _trace_record_text(trace, "quality_status")
            or _trace_record_text(trace, "consensus_quality_status")
            for trace in traces
        ),
        "source_document_count": len(source_docs),
        "filing_count": len(filings),
        "actual_periods": sum(1 for row in series if not row.get("forecast_flag")),
        "forecast_periods": sum(1 for row in series if row.get("forecast_flag")),
        "latest_available_at": available_at[-1] if available_at else None,
        "source_trace_rows": len(traces),
        "row_count": len(series),
    }


def render_valuation_svg(series: list[dict], visibility: dict | None = None) -> str:
    fig = _valuation_figure(series, visibility)
    output = StringIO()
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    fig.savefig(output, format="svg")
    plt.close(fig)
    return output.getvalue()


def render_valuation_png(series: list[dict], visibility: dict | None = None) -> bytes:
    fig = _valuation_figure(series, visibility)
    output = BytesIO()
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    fig.savefig(output, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return output.getvalue()


def _valuation_figure(series: list[dict], visibility: dict | None = None) -> Figure:
    visibility = visibility or {}
    show_price = visibility.get("price", True)
    show_metric = visibility.get("metric_area", True)
    show_fair = visibility.get("fair_value", True)
    show_normal = visibility.get("normal_multiple", True)
    show_current = visibility.get("current_valuation", True)
    show_custom = visibility.get("custom_valuation", False)
    custom_multiple = _optional_float(visibility.get("custom_valuation_multiple"))
    show_dividend = visibility.get("dividend_floor", True)
    show_payout = visibility.get("payout_ratio", True)
    show_yield = visibility.get("dividend_yield", False)
    show_recession = visibility.get("recession_bands", True)
    show_forecast = visibility.get("forecast", True)
    show_scenario = visibility.get("scenario_lines", True)
    calculation_lines = visibility.get("calculation_lines") or []
    recession_bands = visibility.get("recession_bands_data") or []
    price_point_years, price_point_prices = _price_point_series(
        series,
        visibility.get("price_points") or [],
    )
    hidden_scenario_lines = set(visibility.get("hidden_scenario_lines") or [])

    years = [row["fiscal_year"] for row in series]
    prices = [float(row["price"]) for row in series]
    fair = [float(row["fair_value_price"]) for row in series]
    normal = [
        float(Decimal(str(row["normal_multiple"])) * Decimal(str(row["metric"])))
        if row.get("normal_multiple") is not None
        else None
        for row in series
    ]
    current_multiple = _current_valuation_multiple(series)
    current_valuation = [
        float(Decimal(str(row["metric"])) * Decimal(str(current_multiple)))
        if current_multiple is not None
        else None
        for row in series
    ]
    custom_valuation = [
        float(Decimal(str(row["metric"])) * Decimal(str(custom_multiple)))
        if custom_multiple is not None
        else None
        for row in series
    ]
    metric_scaled = [float(Decimal(str(row["metric"])) * Decimal("15")) for row in series]
    dividend_floor = [float(Decimal(str(row.get("dividend", 0))) * Decimal("15")) for row in series]
    payout_ratio = _ratio_series(series, "metric", default_max=100)
    dividend_yield = _ratio_series(series, "price", default_max=8)

    fig, ax = plt.subplots(figsize=(11.5, 5.8), dpi=120)
    fig.patch.set_facecolor("#f7f8f7")
    ax.set_facecolor("#ffffff")
    if show_recession and recession_bands:
        has_recession_label = False
        for band in recession_bands:
            start = _date_to_year_fraction(band.get("start_date"))
            end = _date_to_year_fraction(band.get("end_date")) if band.get("end_date") else None
            if start is None:
                continue
            ax.axvspan(
                start,
                end if end is not None else max(years) + 0.5,
                color="#4b5563",
                alpha=0.12,
                label="Recession bands" if not has_recession_label else None,
            )
            has_recession_label = True
    forecast_years = [row["fiscal_year"] for row in series if row.get("forecast_flag")]
    if show_forecast and forecast_years:
        ax.axvspan(
            min(forecast_years) - 0.5,
            max(forecast_years) + 0.5,
            color="#dcfce7",
            alpha=0.55,
        )
    if show_metric:
        ax.fill_between(years, metric_scaled, color="#1b8f59", alpha=0.22, label="EPS metric area")
    if show_price:
        if price_point_years and price_point_prices:
            ax.plot(
                price_point_years,
                price_point_prices,
                color="#111111",
                linewidth=2.2,
                label=f"Price ({len(price_point_prices)} dated points)",
            )
        else:
            ax.plot(years, prices, color="#111111", linewidth=2.2, label="Price")
    if show_fair:
        ax.plot(years, fair, color="#d97706", linewidth=2, label="Fair value")
    if show_normal:
        ax.plot(years, normal, color="#2563eb", linewidth=1.8, label="Normal multiple")
    if show_current and current_multiple is not None:
        ax.plot(
            years,
            current_valuation,
            color="#111111",
            linewidth=1.15,
            alpha=0.72,
            label=f"Current valuation {current_multiple:.1f}x",
        )
    if show_custom and custom_multiple is not None:
        ax.plot(
            years,
            custom_valuation,
            color="#a21caf",
            linewidth=1.55,
            alpha=0.86,
            linestyle="--",
            label=f"Custom valuation {custom_multiple:.1f}x",
        )
    if show_dividend:
        ax.plot(years, dividend_floor, color="#facc15", linewidth=1.4, label="Dividend floor")
    if show_payout and payout_ratio:
        ax.plot(years, payout_ratio, color="#84cc16", linewidth=1.45, label="Payout ratio")
    if show_yield and dividend_yield:
        ax.plot(
            years,
            dividend_yield,
            color="#7f1d1d",
            linewidth=1.25,
            linestyle=":",
            label="Dividend yield",
        )
    if show_scenario and calculation_lines:
        has_scenario_label = False
        for line in calculation_lines:
            if line.get("label") in hidden_scenario_lines:
                continue
            points = line.get("points") or []
            scenario_years: list[int] = []
            scenario_prices: list[float] = []
            for point in points:
                fiscal_year = point.get("fiscal_year")
                target_price = point.get("target_price")
                if fiscal_year is None or target_price is None:
                    continue
                scenario_years.append(int(fiscal_year))
                scenario_prices.append(float(target_price))
            if scenario_years and scenario_prices:
                ax.plot(
                    scenario_years,
                    scenario_prices,
                    color="#6b7280",
                    linewidth=0.8,
                    alpha=0.42,
                    linestyle="--",
                    label="Scenario lines" if not has_scenario_label else None,
                )
                has_scenario_label = True
    ax.grid(True, color="#e5e7eb", linewidth=0.8)
    ax.tick_params(colors="#374151", labelsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#d1d5db")
    ax.legend(loc="upper left", fontsize=8, frameon=False)
    ax.set_title("Historical Valuation Map", loc="left", fontsize=12, color="#111827")
    audit_caption = _chart_audit_caption(series, visibility)
    if audit_caption:
        fig.text(
            0.01,
            0.018,
            audit_caption,
            ha="left",
            va="bottom",
            fontsize=7,
            color="#475569",
            linespacing=1.35,
        )
    return fig


def _current_valuation_multiple(series: list[dict]) -> float | None:
    for row in reversed(series):
        if row.get("forecast_flag"):
            continue
        try:
            price = Decimal(str(row["price"]))
            metric = Decimal(str(row["metric"]))
        except Exception:
            continue
        if price > 0 and metric > 0:
            return float(price / metric)
    return None


def _price_point_series(series: list[dict], price_points: list[dict]) -> tuple[list[float], list[float]]:
    if not price_points:
        return [], []

    historical_years = {
        int(row["fiscal_year"])
        for row in series
        if row.get("fiscal_year") is not None and not row.get("forecast_flag")
    }
    parsed_points: list[tuple[float, float]] = []
    for point in price_points:
        x_value = _date_to_year_fraction(point.get("date"))
        if x_value is None:
            continue
        try:
            fiscal_year = int(point.get("fiscal_year") or int(x_value))
            close_price = float(Decimal(str(point.get("close_price"))))
        except Exception:
            continue
        if fiscal_year not in historical_years or close_price <= 0:
            continue
        parsed_points.append((x_value, close_price))

    if not parsed_points:
        return [], []

    sorted_points = sorted(parsed_points, key=lambda item: item[0])
    return [point[0] for point in sorted_points], [point[1] for point in sorted_points]


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except Exception:
        return None
    return float(parsed) if parsed > 0 else None


def _date_to_year_fraction(raw: object) -> float | None:
    if not raw:
        return None
    try:
        year, month, day = [int(part) for part in str(raw).split("-")[:3]]
    except Exception:
        return None
    return year + (month - 1) / 12 + max(0, day - 1) / 365


def _ratio_series(series: list[dict], denominator_key: str, default_max: float) -> list[float]:
    raw_values: list[float] = []
    for row in series:
        try:
            numerator = Decimal(str(row.get("dividend", 0)))
            denominator = Decimal(str(row.get(denominator_key, 0)))
        except Exception:
            raw_values.append(0)
            continue
        raw_values.append(float((numerator / denominator) * Decimal("100")) if denominator > 0 else 0)
    max_value = max([default_max, *raw_values, 1])
    return [value / max_value * 100 for value in raw_values]


def _chart_audit_caption(series: list[dict], visibility: dict) -> str:
    summary = chart_source_summary(series, visibility)
    if not summary["source_trace_rows"]:
        return "Source trace: no row-level source_trace supplied to chart renderer"

    return "\n".join(
        [
            "Source trace: "
            f"metric={summary['metric_label']} | "
            f"data={summary['data_mode']}/{summary['data_backend']} | "
            f"methods={_summary_join(summary['methods'])} | "
            f"sources={_summary_join(summary['sources'])} | "
            f"docs={summary['source_document_count']} | filings={summary['filing_count']}",
            "Quality: "
            f"{_summary_join(summary['quality_statuses'])} | "
            f"periods={summary['actual_periods']} actual + {summary['forecast_periods']} forecast | "
            f"latest_available={summary['latest_available_at'] or 'n/a'}",
        ]
    )


def _trace_record_text(trace: dict, key: str) -> str | None:
    value = trace.get(key)
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return None
    text = str(value).strip()
    return text or None


def _unique_compact_list(values: Iterable[str | None], limit: int = 3) -> list[str]:
    unique: list[str] = []
    for value in values:
        if not value or value in unique:
            continue
        unique.append(value)
        if len(unique) >= limit:
            break
    return unique


def _summary_join(values: object) -> str:
    if not isinstance(values, list):
        return "unknown"
    return ", ".join(str(value) for value in values if value) or "unknown"
