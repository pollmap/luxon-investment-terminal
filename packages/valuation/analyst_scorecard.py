from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from packages.quality import validate_source_trace

SCORECARD_FACTS = (
    "actual_eps",
    "estimate_1y_prior",
    "estimate_2y_prior",
    "error_1y_pct",
    "error_2y_pct",
)


def build_analyst_scorecard(
    ticker: str,
    forecast_evidence: dict[str, Any] | None,
    *,
    currency: str,
    data_mode: str,
) -> dict[str, Any]:
    scorecard = (forecast_evidence or {}).get("scorecard") or {}
    evidence_trace = (forecast_evidence or {}).get("source_trace") or {}
    rows = [
        _row(ticker, row, evidence_trace, currency, data_mode)
        for row in scorecard.get("rows", [])
        if isinstance(row, dict)
    ]
    summary = _summary(scorecard.get("summary") or {}, rows, scorecard.get("status"), data_mode)
    flags = sorted(
        {
            flag
            for row in rows
            for flag in row["flags"]
        }
        | set(summary["flags"])
    )
    status = str(scorecard.get("status") or "pending_actual_overlap")
    quality_status = _quality_status(data_mode, flags, status)
    return {
        "ticker": ticker,
        "status": status,
        "rows": rows,
        "summary": summary,
        "quality_status": quality_status,
        "flags": flags,
        "source_trace": _trace(
            ticker,
            "summary",
            evidence_trace,
            currency,
            quality_status,
            "analyst scorecard from actual adjusted EPS and point-in-time estimate snapshots",
        ),
    }


def _row(
    ticker: str,
    row: dict[str, Any],
    evidence_trace: dict[str, Any],
    currency: str,
    data_mode: str,
) -> dict[str, Any]:
    fiscal_year = int(row["fiscal_year"])
    row_trace = row.get("source_trace") or evidence_trace
    flags = []
    flags.extend(validate_source_trace(row_trace).flags)
    status = str(row.get("quality_status") or "")
    if "fixture" in status:
        flags.append("fixture_non_production_scorecard_proxy")
    for field in SCORECARD_FACTS:
        if row.get(field) in {None, ""}:
            flags.append(f"missing_{field}")
    quality_status = _quality_status(data_mode, flags, status)
    return {
        "fiscal_year": fiscal_year,
        "actual_eps": _string_or_none(row.get("actual_eps")),
        "estimate_1y_prior": _string_or_none(row.get("estimate_1y_prior")),
        "estimate_2y_prior": _string_or_none(row.get("estimate_2y_prior")),
        "error_1y_pct": _string_or_none(row.get("error_1y_pct")),
        "error_2y_pct": _string_or_none(row.get("error_2y_pct")),
        "result_1y": str(row.get("result_1y") or "not_available"),
        "result_2y": str(row.get("result_2y") or "not_available"),
        "quality_status": quality_status,
        "flags": sorted(set(flags)),
        "source_trace": _trace(
            ticker,
            fiscal_year,
            row_trace,
            currency,
            quality_status,
            (
                "estimate_error_pct = (point_in_time_estimate_eps / "
                "actual_adjusted_eps - 1) * 100"
            ),
        ),
    }


def _summary(
    raw_summary: dict[str, Any],
    rows: list[dict[str, Any]],
    status: Any,
    data_mode: str,
) -> dict[str, Any]:
    flags = []
    if not rows:
        flags.append("pending_actual_overlap")
    if "fixture" in str(status):
        flags.append("fixture_non_production_scorecard_proxy")
    hit_rate_1y = _hit_rate(rows, "result_1y")
    hit_rate_2y = _hit_rate(rows, "result_2y")
    quality_status = _quality_status(data_mode, flags, str(status or ""))
    return {
        "hit_rate_1y_pct": _string_or_none(raw_summary.get("hit_rate_1y_pct")) or hit_rate_1y,
        "hit_rate_2y_pct": _string_or_none(raw_summary.get("hit_rate_2y_pct")) or hit_rate_2y,
        "scored_years": len(rows),
        "required_source": str(
            raw_summary.get("required_source") or "point_in_time_consensus_snapshots"
        ),
        "quality_status": quality_status,
        "flags": sorted(set(flags)),
    }


def _hit_rate(rows: list[dict[str, Any]], field: str) -> str:
    scored = [row for row in rows if row.get(field) in {"hit", "miss"}]
    if not scored:
        return "0.00"
    hits = sum(1 for row in scored if row[field] == "hit")
    return ((Decimal(hits) / Decimal(len(scored))) * Decimal("100")).quantize(
        Decimal("0.01")
    ).to_eng_string()


def _trace(
    ticker: str,
    period: int | str,
    input_trace: dict[str, Any],
    currency: str,
    quality_status: str,
    formula: str,
) -> dict[str, Any]:
    period_text = f"FY{period}" if isinstance(period, int) else str(period)
    return {
        "source_document_id": input_trace.get("source_document_id")
        or f"{ticker.lower()}-{period_text.lower()}-analyst-scorecard",
        "source_type": "analyst_scorecard_derived",
        "filing_id": input_trace.get("filing_id")
        or input_trace.get("accession_number")
        or f"{ticker}-{period_text}-analyst-scorecard",
        "period": input_trace.get("period") or period_text,
        "available_at": input_trace.get("available_at"),
        "unit": "per_share_or_percent",
        "currency": input_trace.get("currency") or currency,
        "method": "analyst_scorecard_derived",
        "formula": formula,
        "quality_status": quality_status,
        "input_trace_summary": {
            "source_type": input_trace.get("source_type"),
            "source_document_id": input_trace.get("source_document_id"),
            "quality_status": input_trace.get("quality_status"),
        },
    }


def _quality_status(data_mode: str, flags: list[str], status: str) -> str:
    if data_mode != "source_backed":
        return "fixture_non_production_scorecard_proxy"
    if "fixture" in status:
        return "source_backed_warning"
    if any(flag.startswith("missing_source_trace") for flag in flags):
        return "source_backed_warning"
    if flags:
        return "source_backed_partial"
    return "source_backed_derived"


def _string_or_none(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)
    return parsed.to_eng_string() if parsed.is_finite() else str(value)
