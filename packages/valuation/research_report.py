from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from packages.quality import validate_source_trace


def build_research_report(
    ticker: str,
    *,
    snapshot: dict[str, Any] | None,
    valuation_rows: list[dict[str, Any]],
    financial_rows: list[dict[str, Any]],
    fiscal_fitness_rows: list[dict[str, Any]],
    health_check: dict[str, Any] | None,
    forecast_evidence: dict[str, Any] | None,
    use_of_cash_rows: list[dict[str, Any]] | None,
    currency: str,
    data_mode: str,
) -> dict[str, Any]:
    latest_historical = _latest_row(valuation_rows, forecast=False)
    latest_forecast = _latest_row(valuation_rows, forecast=True)
    latest_financial = _latest_financial(financial_rows)
    latest_fitness = _latest_fitness_map(fiscal_fitness_rows)
    latest_use_of_cash = _latest_financial(use_of_cash_rows or [])
    fiscal_year = _latest_year(
        latest_historical,
        latest_financial,
        health_check,
        latest_use_of_cash,
    )
    valuation_gap_pct = _valuation_gap_pct(latest_historical)
    sections = [
        _valuation_section(ticker, latest_historical, valuation_gap_pct, currency, data_mode),
        _quality_section(ticker, health_check, latest_fitness, currency, data_mode),
        _forecast_section(ticker, latest_forecast, forecast_evidence, currency, data_mode),
        _capital_allocation_section(ticker, latest_use_of_cash, currency, data_mode),
        _data_quality_section(
            ticker,
            snapshot,
            health_check,
            valuation_rows,
            fiscal_fitness_rows,
            forecast_evidence,
            currency,
            data_mode,
        ),
    ]
    flags = sorted({flag for section in sections for flag in section["flags"]})
    quality_status = _quality_status(data_mode, flags)
    audit_facts = _audit_facts(
        ticker,
        fiscal_year,
        latest_historical,
        valuation_gap_pct,
        health_check,
        latest_forecast,
        sections,
    )
    source_trace = {
        "source_document_id": f"{ticker.lower()}-{fiscal_year or 'latest'}-research-report",
        "source_type": "research_report_derived",
        "filing_id": f"{ticker}-{fiscal_year or 'latest'}-research-report",
        "period": f"FY{fiscal_year}" if fiscal_year is not None else "latest",
        "available_at": _first_available_at(
            [section["source_trace"] for section in sections]
        ),
        "unit": "narrative",
        "currency": currency,
        "method": "research_report_derived",
        "formula": (
            "deterministic template assembled from source-traced valuation, "
            "financial, forecast, and quality facts"
        ),
        "quality_status": quality_status,
        "input_trace_summary": _trace_collection_summary(
            [section["source_trace"] for section in sections]
        ),
    }
    return {
        "ticker": ticker,
        "title": f"{ticker} Source-Audited Research Report",
        "fiscal_year": fiscal_year,
        "data_mode": data_mode,
        "executive_summary": _executive_summary(
            ticker,
            latest_historical,
            valuation_gap_pct,
            health_check,
            latest_forecast,
        ),
        "sections": sections,
        "audit_facts": audit_facts,
        "flags": flags,
        "quality_status": quality_status,
        "source_trace": source_trace,
    }


def _valuation_section(
    ticker: str,
    latest: dict[str, Any] | None,
    valuation_gap_pct: Decimal | None,
    currency: str,
    data_mode: str,
) -> dict[str, Any]:
    flags: list[str] = []
    trace = (latest or {}).get("source_trace") or {}
    flags.extend(validate_source_trace(trace).flags)
    if latest is None:
        flags.append("missing_valuation_map")
    verdict = _valuation_verdict(valuation_gap_pct)
    evidence = [
        _evidence("Price", (latest or {}).get("price"), trace),
        _evidence("Fair value", (latest or {}).get("fair_value_price"), trace),
        _evidence("Normal multiple", (latest or {}).get("normal_multiple"), trace),
        _evidence("Fair multiple", (latest or {}).get("fair_multiple"), trace),
        _evidence("Valuation gap", valuation_gap_pct, trace, unit="percent"),
    ]
    return _section(
        ticker,
        "valuation",
        "Valuation",
        verdict,
        [
            _sentence("Latest price", (latest or {}).get("price"), currency),
            _sentence("Deterministic fair value", (latest or {}).get("fair_value_price"), currency),
            f"Valuation stance: {verdict}.",
        ],
        evidence,
        flags,
        trace,
        currency,
        data_mode,
        "valuation report section from latest non-forecast valuation row",
    )


def _quality_section(
    ticker: str,
    health_check: dict[str, Any] | None,
    latest_fitness: dict[str, dict[str, Any]],
    currency: str,
    data_mode: str,
) -> dict[str, Any]:
    flags: list[str] = list((health_check or {}).get("flags") or [])
    trace = (health_check or {}).get("source_trace") or {}
    flags.extend(validate_source_trace(trace).flags)
    if health_check is None:
        flags.append("missing_health_check")
    evidence = [
        _evidence("Health score", (health_check or {}).get("overall_score"), trace),
        _evidence("Rating", (health_check or {}).get("rating"), trace, unit="label"),
        _fitness_evidence("ROE", latest_fitness.get("roe_pct")),
        _fitness_evidence("ROIC", latest_fitness.get("roic_pct")),
        _fitness_evidence("FCF margin", latest_fitness.get("fcf_margin_pct")),
    ]
    verdict = str((health_check or {}).get("rating") or "not_scored")
    return _section(
        ticker,
        "quality",
        "Quality",
        verdict,
        [
            f"Health score is {_display((health_check or {}).get('overall_score'))}/100.",
            f"Quality rating is {verdict}.",
            f"ROE is {_display((latest_fitness.get('roe_pct') or {}).get('value'))}%.",
        ],
        evidence,
        flags,
        trace,
        currency,
        data_mode,
        "quality report section from health_check and fiscal_fitness facts",
    )


def _forecast_section(
    ticker: str,
    latest_forecast: dict[str, Any] | None,
    forecast_evidence: dict[str, Any] | None,
    currency: str,
    data_mode: str,
) -> dict[str, Any]:
    flags: list[str] = []
    trace = (
        (latest_forecast or {}).get("source_trace")
        or (forecast_evidence or {}).get("source_trace")
        or {}
    )
    flags.extend(validate_source_trace(trace).flags)
    quality = str(((forecast_evidence or {}).get("source_trace") or {}).get("quality_status", ""))
    if "fixture" in quality:
        flags.append("fixture_non_production_consensus_proxy")
    if latest_forecast is None:
        flags.append("missing_forecast_rows")
    evidence = [
        _evidence("Forecast year", (latest_forecast or {}).get("fiscal_year"), trace),
        _evidence("Forecast metric", (latest_forecast or {}).get("metric"), trace),
        _evidence("Forecast price", (latest_forecast or {}).get("price"), trace),
        _evidence(
            "Total return CAGR",
            (latest_forecast or {}).get("total_return_cagr_pct"),
            trace,
            unit="percent",
        ),
    ]
    sentiment = ((forecast_evidence or {}).get("sentiment") or {}).get("label")
    verdict = f"{sentiment or 'no_consensus'}_forecast_evidence"
    return _section(
        ticker,
        "forecast",
        "Forecast",
        verdict,
        [
            _sentence("Latest forecast target", (latest_forecast or {}).get("price"), currency),
            (
                "Total return CAGR is "
                f"{_display((latest_forecast or {}).get('total_return_cagr_pct'))}%."
            ),
            f"Revision sentiment is {sentiment or 'not loaded'}.",
        ],
        evidence,
        flags,
        trace,
        currency,
        data_mode,
        "forecast report section from valuation forecast rows and consensus snapshot evidence",
    )


def _capital_allocation_section(
    ticker: str,
    latest: dict[str, Any] | None,
    currency: str,
    data_mode: str,
) -> dict[str, Any]:
    flags: list[str] = list((latest or {}).get("flags") or [])
    trace = (latest or {}).get("source_trace") or {}
    flags.extend(validate_source_trace(trace).flags)
    if latest is None:
        flags.append("missing_use_of_cash")
    evidence = [
        _evidence("FCF margin", (latest or {}).get("fcf_margin_pct"), trace, unit="percent"),
        _evidence(
            "Dividend payout", (latest or {}).get("dividend_payout_pct"), trace, unit="percent"
        ),
        _evidence("Debt / equity", (latest or {}).get("debt_to_equity"), trace),
    ]
    payout = _decimal_or_none((latest or {}).get("dividend_payout_pct"))
    if latest is None or payout is None:
        verdict = "cash_use_source_incomplete"
    elif payout <= Decimal("70"):
        verdict = "dividend_supported"
    else:
        verdict = "dividend_requires_review"
    return _section(
        ticker,
        "capital_allocation",
        "Capital Allocation",
        verdict,
        [
            f"FCF margin is {_display((latest or {}).get('fcf_margin_pct'))}%.",
            f"Dividend payout is {_display((latest or {}).get('dividend_payout_pct'))}%.",
            f"Debt/equity is {_display((latest or {}).get('debt_to_equity'))}.",
        ],
        evidence,
        flags,
        trace,
        currency,
        data_mode,
        "capital allocation report section from use_of_cash facts",
    )


def _data_quality_section(
    ticker: str,
    snapshot: dict[str, Any] | None,
    health_check: dict[str, Any] | None,
    valuation_rows: list[dict[str, Any]],
    fiscal_fitness_rows: list[dict[str, Any]],
    forecast_evidence: dict[str, Any] | None,
    currency: str,
    data_mode: str,
) -> dict[str, Any]:
    trace = (snapshot or {}).get("source_trace") or (health_check or {}).get("source_trace") or {}
    flags = validate_source_trace(trace).flags
    report_flags = sorted(
        {
            *list((health_check or {}).get("flags") or []),
            *[flag for row in fiscal_fitness_rows for flag in row.get("flags", [])],
        }
    )
    flags.extend(report_flags)
    if data_mode != "source_backed":
        flags.append("fixture_non_production_report")
    evidence = [
        _evidence(
            "Historical valuation rows",
            len([row for row in valuation_rows if not row.get("forecast_flag")]),
            trace,
        ),
        _evidence("Fiscal Fitness rows", len(fiscal_fitness_rows), trace),
        _evidence(
            "Forecast evidence", "loaded" if forecast_evidence else "missing", trace, unit="status"
        ),
    ]
    return _section(
        ticker,
        "data_quality",
        "Data Quality",
        data_mode,
        [
            f"Data mode is {data_mode}.",
            f"Quality status is {_display((health_check or {}).get('quality_status'))}.",
            f"Open flags: {', '.join(report_flags[:6]) if report_flags else 'none'}.",
        ],
        evidence,
        flags,
        trace,
        currency,
        data_mode,
        "data quality report section from source traces and quality flags",
    )


def _section(
    ticker: str,
    key: str,
    title: str,
    verdict: str,
    bullets: list[str],
    evidence: list[dict[str, Any]],
    flags: list[str],
    input_trace: dict[str, Any],
    currency: str,
    data_mode: str,
    formula: str,
) -> dict[str, Any]:
    quality_status = _quality_status(data_mode, flags)
    source_trace = {
        "source_document_id": input_trace.get("source_document_id")
        or f"{ticker.lower()}-{key}-report",
        "source_type": "research_report_section",
        "filing_id": input_trace.get("filing_id")
        or input_trace.get("accession_number")
        or f"{ticker}-{key}-report",
        "period": input_trace.get("period") or "latest",
        "available_at": input_trace.get("available_at"),
        "unit": "narrative",
        "currency": currency,
        "method": "research_report_section",
        "formula": formula,
        "quality_status": quality_status,
        "input_trace_summary": _trace_summary(input_trace),
    }
    return {
        "section_key": key,
        "title": title,
        "verdict": verdict,
        "bullets": bullets,
        "evidence": evidence,
        "flags": sorted(set(flags)),
        "quality_status": quality_status,
        "source_trace": source_trace,
    }


def _audit_facts(
    ticker: str,
    fiscal_year: int | None,
    latest_historical: dict[str, Any] | None,
    valuation_gap_pct: Decimal | None,
    health_check: dict[str, Any] | None,
    latest_forecast: dict[str, Any] | None,
    sections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    report_year = fiscal_year or 0
    return [
        _audit_fact(
            ticker,
            report_year,
            "research_report.valuation_gap_pct",
            valuation_gap_pct,
            (latest_historical or {}).get("source_trace") or {},
        ),
        _audit_fact(
            ticker,
            report_year,
            "research_report.health_score",
            (health_check or {}).get("overall_score"),
            (health_check or {}).get("source_trace") or {},
        ),
        _audit_fact(
            ticker,
            report_year,
            "research_report.forecast_total_return_cagr_pct",
            (latest_forecast or {}).get("total_return_cagr_pct"),
            (latest_forecast or {}).get("source_trace") or {},
        ),
        _audit_fact(
            ticker,
            report_year,
            "research_report.section_count",
            len(sections),
            sections[-1]["source_trace"] if sections else {},
        ),
    ]


def _audit_fact(
    ticker: str,
    fiscal_year: int,
    fact_name: str,
    value: Any,
    source_trace: dict[str, Any],
) -> dict[str, Any]:
    flags = validate_source_trace(source_trace).flags
    quality_status = source_trace.get("quality_status") or (
        "source_trace_incomplete" if flags else "unknown_quality_status"
    )
    report_trace = {
        "source_document_id": source_trace.get("source_document_id")
        or f"{ticker.lower()}-{fiscal_year}-{fact_name.replace('.', '-')}",
        "source_type": "research_report_derived",
        "filing_id": source_trace.get("filing_id")
        or source_trace.get("accession_number")
        or f"{ticker}-{fiscal_year}-{fact_name}",
        "period": source_trace.get("period") or f"FY{fiscal_year}",
        "available_at": source_trace.get("available_at"),
        "unit": source_trace.get("unit") or "reported",
        "currency": source_trace.get("currency") or "mixed",
        "formula": source_trace.get("formula")
        or f"{fact_name} assembled from research report section evidence",
        "quality_status": quality_status,
        "input_trace_summary": _trace_summary(source_trace),
    }
    return {
        "ticker": ticker,
        "fiscal_year": fiscal_year,
        "fact_name": fact_name,
        "value": value,
        "method": "research_report_derived",
        "policy": "research_report",
        "confidence": None,
        "quality_status": quality_status,
        "flags": flags,
        "formula": report_trace["formula"],
        "source_trace": report_trace,
    }


def _executive_summary(
    ticker: str,
    latest_historical: dict[str, Any] | None,
    valuation_gap_pct: Decimal | None,
    health_check: dict[str, Any] | None,
    latest_forecast: dict[str, Any] | None,
) -> list[str]:
    latest_price = _display((latest_historical or {}).get("price"))
    fair_value = _display((latest_historical or {}).get("fair_value_price"))
    rating = _display((health_check or {}).get("rating"))
    forecast_cagr = _display((latest_forecast or {}).get("total_return_cagr_pct"))
    return [
        f"{ticker} trades at {latest_price} versus deterministic fair value {fair_value}.",
        f"Valuation gap is {_display(valuation_gap_pct)}% and quality rating is {rating}.",
        f"Latest 1-5Y forecast endpoint implies total return CAGR of {forecast_cagr}%.",
    ]


def _latest_row(rows: list[dict[str, Any]], *, forecast: bool) -> dict[str, Any] | None:
    filtered = [row for row in rows if bool(row.get("forecast_flag")) is forecast]
    if not filtered:
        return None
    return max(filtered, key=lambda row: int(row["fiscal_year"]))


def _latest_financial(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return max(rows, key=lambda row: int(row["fiscal_year"]))


def _latest_fitness_map(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest_year = max((int(row["fiscal_year"]) for row in rows), default=None)
    latest_rows = [
        row for row in rows if latest_year is not None and int(row["fiscal_year"]) == latest_year
    ]
    return {str(row["metric_key"]): row for row in latest_rows}


def _latest_year(*items: dict[str, Any] | None) -> int | None:
    years = [
        int(item["fiscal_year"]) for item in items if item and item.get("fiscal_year") is not None
    ]
    return max(years) if years else None


def _valuation_gap_pct(latest: dict[str, Any] | None) -> Decimal | None:
    if not latest:
        return None
    price = _decimal_or_none(latest.get("price"))
    fair_value = _decimal_or_none(latest.get("fair_value_price"))
    if price is None or fair_value in {None, Decimal("0")}:
        return None
    return (((price / fair_value) - Decimal("1")) * Decimal("100")).quantize(Decimal("0.01"))


def _valuation_verdict(gap: Decimal | None) -> str:
    if gap is None:
        return "not_scored"
    if gap > Decimal("25"):
        return "premium_to_fair_value"
    if gap < Decimal("-15"):
        return "discount_to_fair_value"
    return "near_fair_value"


def _fitness_evidence(label: str, row: dict[str, Any] | None) -> dict[str, Any]:
    return _evidence(
        label,
        (row or {}).get("value"),
        (row or {}).get("source_trace") or {},
        unit=(row or {}).get("unit") or "ratio",
    )


def _evidence(
    label: str,
    value: Any,
    source_trace: dict[str, Any],
    *,
    unit: str | None = None,
) -> dict[str, Any]:
    return {
        "label": label,
        "value": value,
        "unit": unit or source_trace.get("unit") or "reported",
        "source_trace": _trace_summary(source_trace),
    }


def _trace_summary(source_trace: dict[str, Any]) -> dict[str, Any]:
    if not source_trace:
        return {"source_type": "missing", "quality_status": "missing_source_trace"}
    keys = (
        "source_type",
        "source_document_id",
        "filing_id",
        "accession_number",
        "period",
        "available_at",
        "unit",
        "currency",
        "formula",
        "quality_status",
    )
    return {key: source_trace[key] for key in keys if source_trace.get(key) is not None}


def _first_available_at(source_traces: list[dict[str, Any]]) -> Any:
    for trace in source_traces:
        if trace.get("available_at"):
            return trace["available_at"]
        for nested in trace.get("input_traces") or []:
            if isinstance(nested, dict) and nested.get("available_at"):
                return nested["available_at"]
    return None


def _trace_collection_summary(source_traces: list[dict[str, Any]]) -> dict[str, Any]:
    summaries = [_trace_summary(trace) for trace in source_traces]
    return {
        "input_trace_count": len(summaries),
        "input_trace_source_types": sorted(
            {str(summary.get("source_type")) for summary in summaries if summary.get("source_type")}
        ),
        "input_trace_quality_statuses": sorted(
            {
                str(summary.get("quality_status"))
                for summary in summaries
                if summary.get("quality_status")
            }
        ),
    }


def _sentence(label: str, value: Any, currency: str) -> str:
    return f"{label} is {_display(value)} {currency}."


def _display(value: Any) -> str:
    if value is None or value == "":
        return "not available"
    return str(value)


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _quality_status(data_mode: str, flags: list[str]) -> str:
    if data_mode != "source_backed":
        return "fixture_non_production_research_report"
    if any(flag.startswith("missing_source_trace") for flag in flags):
        return "source_backed_warning"
    if flags:
        return "source_backed_partial"
    return "source_backed_derived"
