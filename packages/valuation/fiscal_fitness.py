from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from packages.quality import validate_source_trace

DIRECT_METRICS: list[tuple[str, str, str, str, str, str]] = [
    (
        "gross_margin_pct",
        "Gross margin",
        "profitability",
        "gross_margin",
        "percent",
        "higher_is_better",
    ),
    (
        "operating_margin_pct",
        "Operating margin",
        "profitability",
        "operating_margin",
        "percent",
        "higher_is_better",
    ),
    ("net_margin_pct", "Net margin", "profitability", "net_margin", "percent", "higher_is_better"),
    ("roe_pct", "ROE", "profitability", "roe", "percent", "higher_is_better"),
    ("roic_pct", "ROIC", "profitability", "roic", "percent", "higher_is_better"),
    ("debt_to_equity", "Debt / equity", "solvency", "debt_to_equity", "ratio", "lower_is_better"),
]

MISSING_SOURCE_METRICS = [
    ("current_ratio", "Current ratio", "liquidity", "ratio", "higher_is_better"),
    ("quick_ratio", "Quick ratio", "liquidity", "ratio", "higher_is_better"),
    ("interest_coverage", "Interest coverage", "solvency", "ratio", "higher_is_better"),
]


def build_fiscal_fitness_rows(
    ticker: str,
    financial_rows: list[dict[str, Any]],
    *,
    currency: str,
    data_mode: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    sorted_financials = sorted(financial_rows, key=lambda row: int(row["fiscal_year"]))
    previous: dict[str, Any] | None = None
    for financial in sorted_financials:
        year = int(financial["fiscal_year"])
        financial_trace = financial.get("source_trace") or {}
        metric_traces = financial.get("metric_traces") or {}
        for metric_key, label, category, field, unit, direction in DIRECT_METRICS:
            flags: list[str] = []
            value = _decimal_or_none(financial.get(field), field, flags)
            metric_trace = metric_traces.get(field) or financial_trace
            if value is None:
                flags.append(f"missing_{metric_key}_source")
            rows.append(
                _row(
                    ticker,
                    year,
                    metric_key,
                    label,
                    category,
                    value,
                    unit,
                    direction,
                    financial.get("method"),
                    financial.get("confidence"),
                    data_mode,
                    flags,
                    metric_trace,
                    currency,
                    f"{metric_key} read from financial source field `{field}`",
                )
            )
        rows.extend(
            _derived_rows(
                ticker,
                year,
                financial,
                previous,
                financial_trace,
                metric_traces,
                currency,
                data_mode,
            )
        )
        for metric_key, label, category, unit, direction in MISSING_SOURCE_METRICS:
            rows.append(
                _row(
                    ticker,
                    year,
                    metric_key,
                    label,
                    category,
                    None,
                    unit,
                    direction,
                    financial.get("method"),
                    financial.get("confidence"),
                    data_mode,
                    [f"missing_{metric_key}_source"],
                    financial_trace,
                    currency,
                    (
                        f"{metric_key} requires balance sheet or financing "
                        "source facts not yet ingested"
                    ),
                )
            )
        previous = financial
    return rows


def _derived_rows(
    ticker: str,
    year: int,
    financial: dict[str, Any],
    previous: dict[str, Any] | None,
    financial_trace: dict[str, Any],
    metric_traces: dict[str, Any],
    currency: str,
    data_mode: str,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    revenue_flags: list[str] = []
    revenue = _decimal_or_none(financial.get("revenue"), "revenue", revenue_flags)
    fcf = _decimal_or_none(financial.get("fcf"), "fcf", revenue_flags)
    fcf_margin = _ratio_pct(fcf, revenue)
    fcf_margin_trace = _combined_trace(
        financial_trace,
        {
            "revenue": metric_traces.get("revenue"),
            "fcf": metric_traces.get("fcf"),
        },
    )
    if fcf_margin is None:
        revenue_flags.append("fcf_margin_not_computable")
    output.append(
        _row(
            ticker,
            year,
            "fcf_margin_pct",
            "FCF margin",
            "cash_generation",
            fcf_margin,
            "percent",
            "higher_is_better",
            financial.get("method"),
            financial.get("confidence"),
            data_mode,
            revenue_flags,
            fcf_margin_trace,
            currency,
            "free_cash_flow / revenue * 100",
        )
    )

    output.append(
        _growth_row(
            ticker,
            year,
            "revenue_growth_pct",
            "Revenue growth",
            "revenue",
            financial,
            previous,
            metric_traces.get("revenue") or financial_trace,
            currency,
            data_mode,
        )
    )
    output.append(
        _growth_row(
            ticker,
            year,
            "eps_growth_pct",
            "EPS growth",
            "eps",
            financial,
            previous,
            metric_traces.get("eps") or financial_trace,
            currency,
            data_mode,
        )
    )
    return output


def _growth_row(
    ticker: str,
    year: int,
    metric_key: str,
    label: str,
    field: str,
    financial: dict[str, Any],
    previous: dict[str, Any] | None,
    financial_trace: dict[str, Any],
    currency: str,
    data_mode: str,
) -> dict[str, Any]:
    flags: list[str] = []
    current = _decimal_or_none(financial.get(field), field, flags)
    prior = _decimal_or_none(previous.get(field), f"previous_{field}", flags) if previous else None
    value = _growth_pct(current, prior)
    if value is None:
        flags.append(
            "previous_period_not_available"
            if previous is None
            else f"{metric_key}_not_computable"
        )
    return _row(
        ticker,
        year,
        metric_key,
        label,
        "growth",
        value,
        "percent",
        "higher_is_better",
        financial.get("method"),
        financial.get("confidence"),
        data_mode,
        flags,
        financial_trace,
        currency,
        f"({field} - previous_{field}) / abs(previous_{field}) * 100",
    )


def _row(
    ticker: str,
    year: int,
    metric_key: str,
    label: str,
    category: str,
    value: Decimal | None,
    unit: str,
    direction: str,
    method: Any,
    confidence: Any,
    data_mode: str,
    flags: list[str],
    financial_trace: dict[str, Any],
    currency: str,
    formula: str,
) -> dict[str, Any]:
    _extend_trace_flags(flags, financial_trace)
    source_document_id = financial_trace.get("source_document_id")
    if not source_document_id:
        flags.append("missing_input_source_document_id")
        source_document_id = f"{ticker.lower()}-{year}-{metric_key}"
    filing_id = financial_trace.get("filing_id") or financial_trace.get("accession_number")
    if not filing_id:
        flags.append("missing_input_filing_id")
        filing_id = f"{ticker}-{year}-{metric_key}"
    quality_status = _quality_status(data_mode, flags, financial_trace)
    source_trace = {
        "source_document_id": source_document_id,
        "source_type": "fiscal_fitness_derived",
        "filing_id": filing_id,
        "period": f"FY{year}",
        "available_at": financial_trace.get("available_at"),
        "unit": unit,
        "currency": currency,
        "method": "fiscal_fitness_derived",
        "formula": formula,
        "quality_status": quality_status,
        "financial_fact_trace": financial_trace,
    }
    if financial_trace.get("metric_input_traces"):
        source_trace["metric_input_traces"] = financial_trace["metric_input_traces"]
    return {
        "fiscal_year": year,
        "metric_key": metric_key,
        "label": label,
        "category": category,
        "value": value,
        "unit": unit,
        "direction": direction,
        "method": str(method or "source_trace"),
        "confidence": confidence,
        "quality_status": quality_status,
        "flags": flags,
        "source_trace": source_trace,
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


def _ratio_pct(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator in {None, Decimal("0")}:
        return None
    try:
        result = ((numerator / denominator) * Decimal("100")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, ArithmeticError):
        return None
    return result if result.is_finite() else None


def _growth_pct(current: Decimal | None, previous: Decimal | None) -> Decimal | None:
    if current is None or previous in {None, Decimal("0")}:
        return None
    try:
        result = (((current - previous) / abs(previous)) * Decimal("100")).quantize(
            Decimal("0.01")
        )
    except (InvalidOperation, ValueError, ArithmeticError):
        return None
    return result if result.is_finite() else None


def _extend_trace_flags(flags: list[str], trace: dict[str, Any]) -> None:
    result = validate_source_trace(trace)
    flags.extend(result.flags)


def _quality_status(data_mode: str, flags: list[str], trace: dict[str, Any]) -> str:
    if data_mode != "source_backed":
        return "fixture_non_production_fiscal_fitness"
    trace_status = str(trace.get("quality_status", ""))
    if "fixture" in trace_status or "fallback" in trace_status:
        return "source_backed_warning"
    if any(flag.startswith(("missing_source_trace", "invalid_decimal")) for flag in flags):
        return "source_backed_warning"
    if flags:
        return "source_backed_partial"
    return "source_backed_derived"


def _combined_trace(base_trace: dict[str, Any], metric_traces: dict[str, Any]) -> dict[str, Any]:
    trace = dict(base_trace)
    trace["metric_input_traces"] = {
        key: value for key, value in metric_traces.items() if value
    }
    return trace
