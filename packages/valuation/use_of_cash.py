from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from packages.quality import validate_source_trace

MISSING_SOURCE_FLAGS = [
    "missing_operating_cash_flow_source",
    "missing_capex_source",
    "missing_dividends_paid_source",
    "missing_share_repurchases_source",
    "missing_debt_repayment_source",
    "missing_acquisitions_source",
    "net_cash_use_not_computable",
]


def build_use_of_cash_rows(
    ticker: str,
    financial_rows: list[dict[str, Any]],
    valuation_rows: list[dict[str, Any]],
    *,
    currency: str,
    data_mode: str,
) -> list[dict[str, Any]]:
    """Derive auditable capital-allocation rows from available source facts.

    The function deliberately leaves capex, buybacks, and debt repayment null
    unless those source-backed line items are available upstream.
    """

    valuation_by_year = {
        int(row["fiscal_year"]): row
        for row in valuation_rows
        if not row.get("forecast_flag") and row.get("fiscal_year") is not None
    }
    rows: list[dict[str, Any]] = []
    for financial in sorted(financial_rows, key=lambda row: int(row["fiscal_year"])):
        year = int(financial["fiscal_year"])
        valuation = valuation_by_year.get(year, {})
        flags = list(MISSING_SOURCE_FLAGS)
        revenue = _decimal_or_none(financial.get("revenue"), "revenue", flags)
        free_cash_flow = _decimal_or_none(financial.get("fcf"), "fcf", flags)
        eps = _decimal_or_none(valuation.get("metric"), "metric", flags) or _decimal_or_none(
            financial.get("eps"),
            "eps",
            flags,
        )
        dividend_per_share = _decimal_or_none(
            valuation.get("dividend"),
            "dividend",
            flags,
        )
        fcf_margin_pct = _percent_ratio(free_cash_flow, revenue)
        dividend_payout_pct = _percent_ratio(dividend_per_share, eps)
        if free_cash_flow is None:
            flags.append("missing_fcf_source")
        if dividend_per_share is None:
            flags.append("missing_dividend_source")
        if eps is None:
            flags.append("missing_eps_source")
        if dividend_payout_pct is None:
            flags.append("payout_ratio_not_computable")
        debt_to_equity = _decimal_or_none(
            financial.get("debt_to_equity"),
            "debt_to_equity",
            flags,
        )
        financial_trace = financial.get("source_trace") or {}
        valuation_trace = valuation.get("source_trace") or {}
        _extend_trace_flags(flags, "financial", financial_trace)
        _extend_trace_flags(flags, "valuation", valuation_trace)
        source_document_id = (
            financial_trace.get("source_document_id")
            or valuation_trace.get("source_document_id")
        )
        if not source_document_id:
            flags.append("missing_input_source_document_id")
            source_document_id = f"{ticker.lower()}-{year}-use-of-cash"
        filing_id = (
            financial_trace.get("filing_id")
            or valuation_trace.get("filing_id")
            or financial_trace.get("accession_number")
        )
        if not filing_id:
            flags.append("missing_input_filing_id")
            filing_id = f"{ticker}-{year}-use-of-cash"
        quality_status = _quality_status(data_mode, flags, financial_trace, valuation_trace)
        source_trace = {
            "source_document_id": source_document_id,
            "source_type": "use_of_cash_derived",
            "filing_id": filing_id,
            "period": f"FY{year}",
            "available_at": financial_trace.get("available_at")
            or valuation_trace.get("available_at"),
            "unit": "mixed",
            "currency": currency,
            "method": "use_of_cash_derived",
            "formula": (
                "fcf_margin_pct=free_cash_flow/revenue*100; "
                "dividend_payout_pct=dividend_per_share/eps*100; "
                "capex,share_repurchases,debt_repayment remain null until source facts exist"
            ),
            "quality_status": quality_status,
            "financial_fact_trace": financial_trace,
            "valuation_trace": valuation_trace,
        }
        rows.append(
            {
                "fiscal_year": year,
                "revenue": revenue,
                "operating_cash_flow": None,
                "free_cash_flow": free_cash_flow,
                "fcf_margin_pct": fcf_margin_pct,
                "dividend_per_share": dividend_per_share,
                "dividends_paid": None,
                "eps": eps,
                "dividend_payout_pct": dividend_payout_pct,
                "capex": None,
                "buybacks": None,
                "share_repurchases": None,
                "debt_repayment": None,
                "acquisitions": None,
                "net_cash_use": None,
                "debt_to_equity": debt_to_equity,
                "method": financial.get("method") or "source_trace",
                "confidence": financial.get("confidence"),
                "quality_status": quality_status,
                "flags": flags,
                "source_trace": source_trace,
            }
        )
    return rows


def _decimal_or_none(value: Any, field: str, flags: list[str]) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        flags.append(f"invalid_decimal:{field}")
        return None


def _percent_ratio(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator in {None, Decimal("0")}:
        return None
    return ((numerator / denominator) * Decimal("100")).quantize(Decimal("0.01"))


def _extend_trace_flags(flags: list[str], label: str, trace: dict[str, Any]) -> None:
    result = validate_source_trace(trace)
    flags.extend(f"{label}_{flag}" for flag in result.flags)


def _quality_status(
    data_mode: str,
    flags: list[str],
    financial_trace: dict[str, Any],
    valuation_trace: dict[str, Any],
) -> str:
    if data_mode != "source_backed":
        return "fixture_non_production_use_of_cash"
    trace_statuses = {
        str(financial_trace.get("quality_status", "")),
        str(valuation_trace.get("quality_status", "")),
    }
    if any("fixture" in status or "fallback" in status for status in trace_statuses):
        return "source_backed_warning"
    if any(
        flag.startswith(
            ("financial_missing_source_trace", "valuation_missing_source_trace", "invalid_decimal")
        )
        for flag in flags
    ):
        return "source_backed_warning"
    if flags:
        return "source_backed_partial"
    return "source_backed_derived"
