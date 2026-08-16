from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from packages.quality import validate_source_trace


@dataclass(frozen=True)
class FunMetricSpec:
    metric_key: str
    label: str
    field: str
    unit: str
    statement: str
    formula: str


FUN_METRICS: tuple[FunMetricSpec, ...] = (
    FunMetricSpec(
        "revenue",
        "Revenue",
        "revenue",
        "reported",
        "income_statement",
        "revenue as reported in financial source",
    ),
    FunMetricSpec(
        "adjusted_eps",
        "Adjusted EPS",
        "eps",
        "per_share",
        "normalization_engine",
        "adjusted_eps from selected normalization policy",
    ),
    FunMetricSpec(
        "gaap_eps_diluted",
        "GAAP diluted EPS",
        "gaap_eps_diluted",
        "per_share",
        "income_statement",
        "gaap_eps_diluted as reported in filing fact",
    ),
    FunMetricSpec(
        "free_cash_flow",
        "Free cash flow",
        "fcf",
        "reported",
        "cash_flow_statement",
        "free_cash_flow from source metric; connector owns OCF-Capex derivation",
    ),
    FunMetricSpec(
        "gross_margin_pct",
        "Gross margin",
        "gross_margin",
        "percent",
        "income_statement",
        "gross_margin percentage as reported or deterministically derived upstream",
    ),
    FunMetricSpec(
        "operating_margin_pct",
        "Operating margin",
        "operating_margin",
        "percent",
        "income_statement",
        "operating_margin percentage as reported or deterministically derived upstream",
    ),
    FunMetricSpec(
        "net_margin_pct",
        "Net margin",
        "net_margin",
        "percent",
        "income_statement",
        "net_margin percentage as reported or deterministically derived upstream",
    ),
    FunMetricSpec(
        "roe_pct",
        "ROE",
        "roe",
        "percent",
        "ratio",
        "return_on_equity percentage from source metric",
    ),
    FunMetricSpec(
        "roic_pct",
        "ROIC",
        "roic",
        "percent",
        "ratio",
        "return_on_invested_capital percentage from source metric",
    ),
    FunMetricSpec(
        "debt_to_equity",
        "Debt / equity",
        "debt_to_equity",
        "ratio",
        "balance_sheet",
        "debt_to_equity ratio from source metric",
    ),
)


def build_fun_graphs(
    ticker: str,
    financial_rows: list[dict[str, Any]],
    *,
    currency: str,
    data_mode: str,
) -> dict[str, Any]:
    sorted_rows = sorted(financial_rows, key=lambda row: int(row["fiscal_year"]))
    series = [
        _metric_series(ticker, metric, sorted_rows, currency, data_mode)
        for metric in FUN_METRICS
    ]
    flags = sorted(
        {flag for metric in series for point in metric["points"] for flag in point["flags"]}
    )
    return {
        "ticker": ticker,
        "currency": currency,
        "metrics": series,
        "summary": {
            "latest_year": max((int(row["fiscal_year"]) for row in sorted_rows), default=None),
            "metric_count": len(series),
            "point_count": sum(len(metric["points"]) for metric in series),
            "quality_status": _summary_quality(series, data_mode),
            "flags": flags,
        },
        "source_trace": _summary_trace(ticker, sorted_rows, currency, data_mode, series, flags),
    }


def _metric_series(
    ticker: str,
    metric: FunMetricSpec,
    financial_rows: list[dict[str, Any]],
    currency: str,
    data_mode: str,
) -> dict[str, Any]:
    points = [
        _metric_point(ticker, metric, row, currency, data_mode)
        for row in financial_rows
    ]
    flags = sorted({flag for point in points for flag in point["flags"]})
    return {
        "metric_key": metric.metric_key,
        "label": metric.label,
        "unit": metric.unit,
        "statement": metric.statement,
        "formula": metric.formula,
        "points": points,
        "quality_status": _quality_status(data_mode, flags),
        "flags": flags,
    }


def _metric_point(
    ticker: str,
    metric: FunMetricSpec,
    financial: dict[str, Any],
    currency: str,
    data_mode: str,
) -> dict[str, Any]:
    year = int(financial["fiscal_year"])
    flags: list[str] = []
    value = _decimal_or_none(financial.get(metric.field), metric.field, flags)
    metric_trace = _metric_trace(financial, metric.field)
    flags.extend(validate_source_trace(metric_trace).flags)
    if value is None:
        flags.append(f"missing_{metric.metric_key}_source")
    quality_status = _quality_status(data_mode, flags)
    return {
        "fiscal_year": year,
        "value": value,
        "method": str(financial.get("method") or metric_trace.get("method") or "source_trace"),
        "confidence": financial.get("confidence"),
        "quality_status": quality_status,
        "flags": flags,
        "source_trace": _source_trace(
            ticker,
            year,
            metric,
            metric_trace,
            currency,
            quality_status,
        ),
    }


def _metric_trace(financial: dict[str, Any], field: str) -> dict[str, Any]:
    metric_traces = financial.get("metric_traces") or {}
    if field in metric_traces:
        return dict(metric_traces[field] or {})
    source_trace = financial.get("source_trace") or {}
    if field == "eps" and source_trace:
        return dict(source_trace)
    financial_fact_trace = source_trace.get("financial_fact_trace")
    if financial_fact_trace:
        return dict(financial_fact_trace)
    return dict(source_trace)


def _source_trace(
    ticker: str,
    year: int,
    metric: FunMetricSpec,
    input_trace: dict[str, Any],
    currency: str,
    quality_status: str,
) -> dict[str, Any]:
    return {
        "source_document_id": input_trace.get("source_document_id")
        or f"{ticker.lower()}-{year}-fun-graphs-{metric.metric_key}",
        "source_type": "fun_graphs_derived",
        "filing_id": input_trace.get("filing_id")
        or input_trace.get("accession_number")
        or f"{ticker}-{year}-fun-graphs-{metric.metric_key}",
        "period": input_trace.get("period") or f"FY{year}",
        "available_at": input_trace.get("available_at"),
        "unit": metric.unit,
        "currency": input_trace.get("currency") or currency,
        "method": "fun_graphs_derived",
        "formula": metric.formula,
        "quality_status": quality_status,
        "input_trace_summary": {
            "source_type": input_trace.get("source_type"),
            "source_document_id": input_trace.get("source_document_id"),
            "quality_status": input_trace.get("quality_status"),
        },
    }


def _summary_trace(
    ticker: str,
    financial_rows: list[dict[str, Any]],
    currency: str,
    data_mode: str,
    series: list[dict[str, Any]],
    flags: list[str],
) -> dict[str, Any]:
    latest_year = max((int(row["fiscal_year"]) for row in financial_rows), default=None)
    latest_trace = next(
        (
            row.get("source_trace")
            for row in reversed(financial_rows)
            if row.get("source_trace")
        ),
        {},
    )
    quality_status = _summary_quality(series, data_mode)
    return {
        "source_document_id": f"{ticker.lower()}-fun-graphs-summary",
        "source_type": "fun_graphs_derived",
        "filing_id": f"{ticker}-fun-graphs-summary",
        "period": f"FY{latest_year}" if latest_year else "historical",
        "available_at": latest_trace.get("available_at"),
        "unit": "mixed",
        "currency": currency,
        "method": "fun_graphs_derived",
        "formula": "FUN Graphs series are selected from source-traced financial rows by metric key",
        "quality_status": quality_status,
        "input_trace_summary": {
            "financial_row_count": len(financial_rows),
            "metric_count": len(series),
            "flags": flags,
        },
    }


def _decimal_or_none(value: Any, field: str, flags: list[str]) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        flags.append(f"invalid_decimal:{field}")
        return None
    if not parsed.is_finite():
        flags.append(f"invalid_decimal:{field}")
        return None
    return parsed


def _quality_status(data_mode: str, flags: list[str]) -> str:
    if data_mode != "source_backed":
        return "fixture_non_production_fun_graphs"
    if any(flag.startswith(("missing_source_trace", "invalid_decimal")) for flag in flags):
        return "source_backed_warning"
    if flags:
        return "source_backed_partial"
    return "source_backed_derived"


def _summary_quality(series: list[dict[str, Any]], data_mode: str) -> str:
    if data_mode != "source_backed":
        return "fixture_non_production_fun_graphs"
    statuses = {
        point["quality_status"]
        for metric in series
        for point in metric["points"]
    }
    if "source_backed_warning" in statuses:
        return "source_backed_warning"
    if "source_backed_partial" in statuses:
        return "source_backed_partial"
    return "source_backed_derived"
