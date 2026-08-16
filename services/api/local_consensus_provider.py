from __future__ import annotations

import csv
from collections import Counter
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
from typing import Any


DEFAULT_IMPORTS_DIR = Path("storage/imports")
AGGREGATE_CONSENSUS_CSV = "consensus_estimates.csv"
DEFAULT_METRIC_KEY = "adjusted_operating_eps"
TRACE_ANCHOR_FIELDS = ("source_url", "source_document_id", "filing_id")
VALUE_FIELDS = ("estimate_eps", "currency", "source")
BLOCKED_QUALITY_STATUSES = {
    "fixture_non_production_consensus_proxy",
    "missing_source_backed_consensus_snapshot",
    "template_pending_source_value",
}
BLOCKED_SOURCE_TOKENS = {
    "fastgraphs",
    "fast graphs",
    "app.fastgraphs.com",
    "fixture",
    "mock",
    "sample",
    "demo",
    "placeholder",
    "template",
    "llm",
    "chatgpt",
    "gemini",
    "claude",
    "ai_generated",
    "ai-generated",
}
MANUAL_SOURCE_ALIASES = {
    "manual",
    "manual_assumption",
    "manual_forecast_assumption",
    "user_manual_forecast_assumption",
    "explicit_manual_forecast_assumption",
}
MANUAL_QUALITY_STATUSES = {
    "manual_forecast_assumption",
    "source_backed_manual_forecast_assumption",
}
SOURCE_BACKED_MANUAL_QUALITY = "source_backed_manual_forecast_assumption"
SOURCE_BACKED_CONSENSUS_QUALITY = "source_backed_consensus_snapshots"


def local_consensus_projection_from_csv(
    ticker: str,
    forecast_case: str,
    start_year: int,
    years: int,
    start_metric: Decimal | None = None,
) -> dict[str, Any] | None:
    rows = _validated_rows_for_ticker(ticker)
    if not rows:
        return None

    normalized_case = _normalize_case(forecast_case)
    bounded_years = max(1, min(int(years), 5))
    end_year = int(start_year) + bounded_years
    selected_rows: list[dict[str, Any]] = []
    metric_values: list[str | None] = []
    missing_years: list[int] = []
    traces_by_year: dict[str, dict[str, Any]] = {}
    for offset in range(1, bounded_years + 1):
        fiscal_year = int(start_year) + offset
        year_rows = [
            row
            for row in rows
            if int(row["fiscal_year"]) == fiscal_year
            and row["metric_key"] == DEFAULT_METRIC_KEY
        ]
        selected = _select_projection_row(year_rows, normalized_case)
        if selected is None:
            metric_values.append(None)
            missing_years.append(fiscal_year)
            continue
        selected_rows.append(selected)
        metric_values.append(_decimal_str(selected["estimate_eps"]))
        traces_by_year[str(fiscal_year)] = _trace_from_row(selected)

    if not selected_rows:
        return None

    quality_status = _projection_quality_status(selected_rows, missing_years)
    latest_row = max(
        selected_rows,
        key=lambda row: (int(row["fiscal_year"]), row["snapshot_date"], row["row_number"]),
    )
    source_trace = _trace_from_row(latest_row)
    source_trace["quality_status"] = quality_status
    source_trace["forecast_case"] = normalized_case
    source_trace["missing_consensus_years"] = missing_years
    source_trace["formula"] = (
        "local source-backed forecast CSV EPS snapshots by fiscal year; missing years use "
        "deterministic growth fallback when valuation-map projection requires continuity"
    )
    return {
        "case": normalized_case,
        "metric_values": metric_values,
        "growth_rate_pct": _decimal_str(
            _projection_growth_rate(selected_rows, start_year, start_metric)
        ),
        "analyst_count": latest_row["analyst_count"] or 0,
        "quality_status": quality_status,
        "missing_years": missing_years,
        "source_trace": source_trace,
        "source_traces_by_year": traces_by_year,
        "source_note": "local source-backed forecast CSV overlay loaded from storage/imports",
        "source_backend": "local_consensus_csv",
        "assumption_types": dict(Counter(row["assumption_type"] for row in selected_rows)),
    }


def local_forecast_evidence_from_csv(ticker: str) -> dict[str, Any] | None:
    rows = [
        row
        for row in _validated_rows_for_ticker(ticker)
        if row["metric_key"] == DEFAULT_METRIC_KEY
    ]
    if not rows:
        return None

    years = sorted({int(row["fiscal_year"]) for row in rows})
    selected_rows: list[dict[str, Any]] = []
    for fiscal_year in years[:5]:
        selected = _select_projection_row(
            [row for row in rows if int(row["fiscal_year"]) == fiscal_year],
            "median",
        )
        if selected is not None:
            selected_rows.append(selected)
    if not selected_rows:
        return None

    first_row = selected_rows[0]
    last_row = selected_rows[-1]
    quality_status = _projection_quality_status(selected_rows, [])
    source_trace = _trace_from_row(first_row)
    source_trace["period"] = (
        f"FY{first_row['fiscal_year']}E"
        if first_row["fiscal_year"] == last_row["fiscal_year"]
        else f"FY{first_row['fiscal_year']}E-FY{last_row['fiscal_year']}E"
    )
    source_trace["quality_status"] = quality_status
    source_trace["formula"] = (
        "local source-backed forecast CSV evidence overlay; manual assumptions "
        "remain labeled separately from external consensus snapshots"
    )
    analyst_count = int(first_row.get("analyst_count") or 0)
    return {
        "ticker": first_row["ticker"],
        "forecast_year": int(first_row["fiscal_year"]),
        "metric_name": "Adjusted Operating EPS",
        "cases": _local_forecast_case_rows(rows, int(first_row["fiscal_year"])),
        "revisions": [
            {
                "as_of_label": "current",
                "age_months": 0,
                "estimate_eps": _decimal_str(first_row["estimate_eps"]),
                "analyst_count": analyst_count,
                "revision_delta_pct": None,
                "quality_status": quality_status,
                "source_trace": _trace_from_row(first_row),
            }
        ],
        "sentiment": {
            "label": "neutral",
            "net_revision_score_pct": "0",
            "up_revisions": 0,
            "down_revisions": 0,
            "unchanged": analyst_count,
            "quality_status": quality_status,
        },
        "scorecard": {
            "status": (
                "not_applicable_manual_forecast_assumption"
                if any(row["assumption_type"] == "manual_assumption" for row in selected_rows)
                else "pending_actual_overlap"
            ),
            "rows": [],
            "summary": {
                "hit_rate_1y_pct": "0.00",
                "hit_rate_2y_pct": "0.00",
                "required_source": "point_in_time_consensus_snapshots",
            },
        },
        "source_trace": source_trace,
        "meta": {
            "data_mode": "source_backed",
            "quality_status": quality_status,
            "source_note": "local source-backed forecast CSV overlay loaded from storage/imports",
        },
    }


def local_consensus_coverage_counts(
    tickers: list[str],
    *,
    min_forecast_years: int,
) -> dict[str, dict[str, Any]]:
    counts: dict[str, dict[str, Any]] = {}
    for ticker in tickers:
        rows = [
            row
            for row in _validated_rows_for_ticker(ticker)
            if row["metric_key"] == DEFAULT_METRIC_KEY
            and _normalize_case(row["estimate_case"]) in {"median", "current"}
        ]
        fiscal_years = sorted({int(row["fiscal_year"]) for row in rows})
        ready_years = fiscal_years[: max(0, min_forecast_years)]
        if len(ready_years) < min_forecast_years:
            counts[ticker.upper()] = {
                "ready": False,
                "forecast_years": len(fiscal_years),
                "valuation_years": len(fiscal_years),
                "snapshots": len(rows),
                "valuation_snapshots": len(rows),
                "latest_consensus_year": max(fiscal_years) if fiscal_years else None,
            }
            continue
        counts[ticker.upper()] = {
            "ready": True,
            "forecast_years": len(fiscal_years),
            "valuation_years": len(ready_years),
            "snapshots": len(rows),
            "valuation_snapshots": len(rows),
            "latest_consensus_year": max(ready_years),
        }
    return counts


def overlay_local_consensus_counts(
    rows: list[dict[str, Any]],
    tickers: list[str],
    *,
    min_forecast_years: int,
) -> list[dict[str, Any]]:
    counts_by_ticker = local_consensus_coverage_counts(
        tickers,
        min_forecast_years=min_forecast_years,
    )
    updated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        ticker = str(row.get("ticker") or "").upper()
        seen.add(ticker)
        counts = counts_by_ticker.get(ticker)
        if not counts or not counts["ready"]:
            updated.append(dict(row))
            continue
        merged = dict(row)
        merged["consensus_forecast_years"] = max(
            int(merged.get("consensus_forecast_years") or 0),
            int(counts["forecast_years"]),
        )
        merged["consensus_valuation_years"] = max(
            int(merged.get("consensus_valuation_years") or 0),
            int(counts["valuation_years"]),
        )
        merged["consensus_snapshots"] = max(
            int(merged.get("consensus_snapshots") or 0),
            int(counts["snapshots"]),
        )
        merged["consensus_valuation_snapshots"] = max(
            int(merged.get("consensus_valuation_snapshots") or 0),
            int(counts["valuation_snapshots"]),
        )
        merged["latest_consensus_year"] = counts["latest_consensus_year"]
        merged["local_consensus_overlay_ready"] = True
        merged["local_consensus_overlay_source"] = "local_consensus_csv"
        updated.append(merged)

    for ticker, counts in counts_by_ticker.items():
        if ticker in seen or not counts["ready"]:
            continue
        updated.append(
            {
                "ticker": ticker,
                "consensus_forecast_years": counts["forecast_years"],
                "consensus_valuation_years": counts["valuation_years"],
                "consensus_snapshots": counts["snapshots"],
                "consensus_valuation_snapshots": counts["valuation_snapshots"],
                "latest_consensus_year": counts["latest_consensus_year"],
                "local_consensus_overlay_ready": True,
                "local_consensus_overlay_source": "local_consensus_csv",
            }
        )
    return updated


def _local_forecast_case_rows(rows: list[dict[str, Any]], fiscal_year: int) -> list[dict[str, Any]]:
    year_rows = [row for row in rows if int(row["fiscal_year"]) == fiscal_year]
    cases: list[dict[str, Any]] = []
    for case in ("low", "median", "high"):
        selected = _select_projection_row(year_rows, case)
        if selected is None:
            continue
        case_label = "median" if selected["estimate_case"] == "current" else selected["estimate_case"]
        if any(row["case"] == case_label for row in cases):
            continue
        cases.append(
            {
                "case": case_label,
                "growth_rate_pct": _decimal_str(selected.get("growth_rate_pct")),
                "estimate_eps": _decimal_str(selected["estimate_eps"]),
                "source_trace": _trace_from_row(selected),
            }
        )
    return cases


def _validated_rows_for_ticker(ticker: str) -> list[dict[str, Any]]:
    path = _csv_path_for_ticker(ticker)
    if path.exists():
        return _validated_rows_from_path(path, ticker)
    aggregate_path = DEFAULT_IMPORTS_DIR / AGGREGATE_CONSENSUS_CSV
    if aggregate_path.exists():
        return _validated_rows_from_path(aggregate_path, ticker)
    return []


def _validated_rows_from_path(path: Path, ticker: str) -> list[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError:
        return []
    validated: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=2):
        try:
            validated_row = _validated_row(row, index)
        except ValueError:
            continue
        if validated_row["ticker"] == ticker.upper():
            validated_row["source_file"] = str(path)
            validated.append(validated_row)
    return validated


def _csv_path_for_ticker(ticker: str) -> Path:
    normalized = ticker.strip().upper()
    for suffix in (".KS", ".KQ", ".T", ".US"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    slug = re.sub(r"[^A-Z0-9]+", "_", normalized).strip("_").lower()
    slug = re.sub(r"_+", "_", slug)
    return DEFAULT_IMPORTS_DIR / f"consensus_{slug or 'custom'}.csv"


def _validated_row(row: dict[str, str], index: int) -> dict[str, Any]:
    ticker = (row.get("ticker") or "").strip().upper()
    if not ticker:
        raise ValueError(f"row {index}: ticker is required")
    fiscal_year = _required_int(row.get("fiscal_year"), "fiscal_year", index)
    snapshot_date = _required_date(row.get("snapshot_date"), "snapshot_date", index)
    estimate_case = _normalize_case(row.get("estimate_case") or "")
    estimate_eps = _required_decimal(row.get("estimate_eps"), "estimate_eps", index)
    if estimate_eps <= 0:
        raise ValueError(f"row {index}: estimate_eps must be positive")
    growth_rate_pct = (
        _required_decimal(row.get("growth_rate_pct"), "growth_rate_pct", index)
        if row.get("growth_rate_pct")
        else None
    )
    analyst_count = (
        _required_int(row.get("analyst_count"), "analyst_count", index)
        if row.get("analyst_count")
        else None
    )
    if analyst_count is not None and analyst_count < 0:
        raise ValueError(f"row {index}: analyst_count must be non-negative")
    currency = (row.get("currency") or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError(f"row {index}: currency must be a 3-letter ISO code")
    source = (row.get("source") or "").strip()
    if not source:
        raise ValueError(f"row {index}: source is required")
    source_url = (row.get("source_url") or "").strip()
    if source_url and not source_url.startswith(("https://", "http://")):
        raise ValueError(f"row {index}: source_url must start with http:// or https://")
    source_document_id = (row.get("source_document_id") or "").strip()
    filing_id = (row.get("filing_id") or "").strip()
    if not (source_url or source_document_id or filing_id):
        raise ValueError(
            f"row {index}: one of source_url, source_document_id, or filing_id is required"
        )
    quality_status = (row.get("quality_status") or "").strip() or (
        "manual_forecast_assumption"
        if _is_manual_source(source)
        else "user_provided_consensus_snapshot"
    )
    if quality_status.lower() in BLOCKED_QUALITY_STATUSES:
        raise ValueError(f"row {index}: quality_status is not import-ready")
    if _blocked_evidence_reason(
        {
            "source": source,
            "source_url": source_url,
            "source_document_id": source_document_id,
            "filing_id": filing_id,
            "quality_status": quality_status,
        }
    ):
        raise ValueError(f"row {index}: blocked consensus evidence source")
    assumption_type = _assumption_type(source, quality_status)
    notes = (row.get("notes") or "").strip()
    if assumption_type == "manual_assumption" and not notes:
        raise ValueError(
            f"row {index}: manual forecast assumptions require notes with the assumption basis"
        )
    period_end = _optional_date(row.get("period_end"), "period_end", index)
    metric_key = (row.get("metric_key") or DEFAULT_METRIC_KEY).strip()
    return {
        **row,
        "row_number": index,
        "ticker": ticker,
        "fiscal_year": fiscal_year,
        "snapshot_date": snapshot_date,
        "period_end": period_end,
        "estimate_case": estimate_case,
        "estimate_eps": estimate_eps,
        "growth_rate_pct": growth_rate_pct,
        "analyst_count": analyst_count,
        "currency": currency,
        "source": source,
        "source_url": source_url,
        "source_document_id": source_document_id,
        "filing_id": filing_id,
        "quality_status": quality_status,
        "notes": notes,
        "metric_key": metric_key,
        "assumption_type": assumption_type,
    }


def _trace_from_row(row: dict[str, Any]) -> dict[str, Any]:
    assumption_type = row["assumption_type"]
    quality_status = (
        SOURCE_BACKED_MANUAL_QUALITY
        if assumption_type == "manual_assumption"
        else SOURCE_BACKED_CONSENSUS_QUALITY
    )
    upstream_source_document_id = row.get("source_document_id") or None
    filing_id = row.get("filing_id") or upstream_source_document_id or (
        f"local-consensus-csv:{row['ticker']}:{row['fiscal_year']}:{row['estimate_case']}"
    )
    source_document_id = filing_id
    return {
        "source": row["source"],
        "source_type": row["source"],
        "source_url": row.get("source_url") or None,
        "source_document_id": source_document_id,
        "upstream_source_document_id": upstream_source_document_id,
        "filing_id": filing_id,
        "period": f"FY{row['fiscal_year']}E",
        "available_at": datetime.combine(row["snapshot_date"], datetime.min.time(), tzinfo=UTC).isoformat(),
        "unit": "per_share",
        "currency": row["currency"],
        "method": (
            "explicit_manual_assumption"
            if assumption_type == "manual_assumption"
            else "point_in_time_consensus_snapshot"
        ),
        "formula": _trace_formula(assumption_type),
        "quality_status": quality_status,
        "forecast_case": row["estimate_case"],
        "metric_key": row["metric_key"],
        "fiscal_year": row["fiscal_year"],
        "fiscal_period": "FY",
        "assumption_type": assumption_type,
        "analyst_count": row.get("analyst_count"),
        "growth_rate_pct": _decimal_str(row.get("growth_rate_pct")),
        "source_file": row.get("source_file") or str(_csv_path_for_ticker(row["ticker"])),
        "source_file_row_number": row["row_number"],
        "notes": row.get("notes") or None,
        "llm_generated_numbers": False,
        "ai_role": "commentary_only",
    }


def _projection_quality_status(
    rows: list[dict[str, Any]],
    missing_years: list[int],
) -> str:
    if missing_years:
        return "partial_source_backed_manual_forecast_assumption"
    if any(row["assumption_type"] == "manual_assumption" for row in rows):
        return SOURCE_BACKED_MANUAL_QUALITY
    return SOURCE_BACKED_CONSENSUS_QUALITY


def _trace_formula(assumption_type: str) -> str:
    if assumption_type == "manual_assumption":
        return (
            "explicit user forecast assumption imported from a source-traced local CSV; "
            "no LLM-generated numbers"
        )
    return (
        "source-backed point-in-time consensus estimate snapshot imported from "
        "a source-traced local CSV; no LLM-generated numbers"
    )


def _select_projection_row(
    rows: list[dict[str, Any]],
    forecast_case: str,
) -> dict[str, Any] | None:
    if not rows:
        return None
    desired_cases = [forecast_case]
    if forecast_case == "median":
        desired_cases.append("current")
    else:
        desired_cases.extend(["median", "current"])
    sorted_rows = sorted(
        rows,
        key=lambda row: (row["snapshot_date"], row["row_number"]),
        reverse=True,
    )
    for desired_case in desired_cases:
        for row in sorted_rows:
            row_case = _normalize_case(str(row["estimate_case"]))
            if row_case == desired_case or (
                desired_case == "median" and str(row["estimate_case"]).lower() == "current"
            ):
                return row
    return sorted_rows[0]


def _projection_growth_rate(
    selected_rows: list[dict[str, Any]],
    start_year: int,
    start_metric: Decimal | None,
) -> Decimal | None:
    for row in reversed(selected_rows):
        if row.get("growth_rate_pct") is not None:
            return Decimal(str(row["growth_rate_pct"])).quantize(Decimal("0.01"))
    if start_metric is None or start_metric <= 0:
        return None
    latest_row = max(selected_rows, key=lambda row: int(row["fiscal_year"]))
    horizon = int(latest_row["fiscal_year"]) - int(start_year)
    latest_estimate = Decimal(str(latest_row["estimate_eps"]))
    if horizon <= 0 or latest_estimate <= 0:
        return None
    return (
        (
            ((latest_estimate / start_metric) ** (Decimal("1") / Decimal(horizon)))
            - Decimal("1")
        )
        * Decimal("100")
    ).quantize(Decimal("0.01"))


def _normalize_case(raw: str) -> str:
    normalized = raw.lower().replace("-", "_")
    if normalized in {"low", "bear", "pessimistic"}:
        return "low"
    if normalized in {"high", "bull", "optimistic"}:
        return "high"
    if normalized == "current":
        return "current"
    return "median"


def _assumption_type(source: str, quality_status: str) -> str:
    if _is_manual_source(source) or quality_status.strip().lower() in MANUAL_QUALITY_STATUSES:
        return "manual_assumption"
    return "external_consensus"


def _is_manual_source(source: str) -> bool:
    normalized = source.strip().lower()
    return normalized in MANUAL_SOURCE_ALIASES or "manual" in normalized


def _blocked_evidence_reason(row: dict[str, Any]) -> str | None:
    evidence_text = " ".join(
        str(row.get(key) or "")
        for key in (
            "source",
            "source_url",
            "source_document_id",
            "filing_id",
            "quality_status",
        )
    ).lower()
    for token in sorted(BLOCKED_SOURCE_TOKENS):
        if token in evidence_text:
            return token
    return None


def _required_int(raw: str | None, field: str, index: int) -> int:
    value = (raw or "").strip()
    if not value:
        raise ValueError(f"row {index}: {field} is required")
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"row {index}: {field} must be an integer") from exc


def _required_decimal(raw: str | None, field: str, index: int) -> Decimal:
    value = (raw or "").strip()
    if not value:
        raise ValueError(f"row {index}: {field} is required")
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"row {index}: {field} must be a decimal number") from exc


def _required_date(raw: str | None, field: str, index: int) -> date:
    value = (raw or "").strip()
    if not value:
        raise ValueError(f"row {index}: {field} is required")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"row {index}: {field} must be YYYY-MM-DD") from exc


def _optional_date(raw: str | None, field: str, index: int) -> date | None:
    if not (raw or "").strip():
        return None
    return _required_date(raw, field, index)


def _decimal_str(value: Any) -> str | None:
    if value is None:
        return None
    return format(Decimal(str(value)).normalize(), "f")
