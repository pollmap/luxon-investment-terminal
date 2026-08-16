from __future__ import annotations

import json
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from packages.quality import validate_source_trace
from packages.valuation.engine import ValuationPoint


_DEFAULT_CACHE_ROOT = Path("storage/cache/kr-valuation-inputs")
_NON_PRODUCTION_MARKERS = (
    "fixture",
    "mock",
    "dummy",
    "sample",
    "synthetic",
    "non-production",
    "non_production",
)

_METRIC_ALIASES = {
    "adjusted_operating": "adjusted_operating_eps",
    "adjusted_operating_eps": "adjusted_operating_eps",
    "gaap_diluted_eps": "diluted_eps",
    "diluted_eps": "diluted_eps",
    "revenue_share": "revenue_per_share",
    "sales_share": "revenue_per_share",
    "fcf_share": "free_cash_flow_per_share",
}

_METRIC_LABELS = {
    "adjusted_operating_eps": "KR market-standard adjusted operating EPS",
    "diluted_eps": "GAAP diluted EPS",
    "revenue_per_share": "Revenue/share",
    "free_cash_flow_per_share": "Free cash flow/share",
}


@dataclass(frozen=True)
class KrValuationCachePayload:
    points: list[ValuationPoint]
    metric_label: str
    meta: dict[str, Any]
    price_points: list[dict[str, Any]]


def kr_valuation_cache_universe_coverage(tickers: list[str] | tuple[str, ...]) -> dict[str, Any]:
    rows = [_kr_cache_coverage_row(ticker) for ticker in tickers]
    ready_count = sum(1 for row in rows if row["valuation_ready"])
    complete_count = sum(1 for row in rows if row["coverage_status"] == "complete")
    partial_count = sum(1 for row in rows if row["coverage_status"] == "partial_source_backed")
    missing_count = len(rows) - ready_count
    if rows and complete_count == len(rows):
        coverage_status = "complete"
        quality_status = "source_backed_cache_complete"
    elif rows and ready_count == len(rows):
        coverage_status = "partial_source_backed"
        quality_status = "source_backed_cache_partial"
    else:
        coverage_status = "missing_source_backed_cache"
        quality_status = "missing_source_backed_data"
    quality_flags = sorted(
        {
            str(flag)
            for row in rows
            for flag in row.get("quality_flags", [])
            if flag
        }
    )
    if partial_count and "partial_valuation_coverage" not in quality_flags:
        quality_flags.append("partial_valuation_coverage")
    if missing_count and "missing_kr_valuation_cache" not in quality_flags:
        quality_flags.append("missing_kr_valuation_cache")
    source_trace = {
        "source": "kr_valuation_input_cache",
        "source_type": "kr_valuation_cache_universe_summary",
        "source_document_id": "kr-valuation-cache-universe-summary",
        "filing_id": "KR-VALUATION-CACHE-UNIVERSE-SUMMARY",
        "period": "latest_cache",
        "available_at": "latest_cache",
        "unit": "ticker_coverage_count",
        "currency": "KRW",
        "method": "KR_VALUATION_CACHE_METADATA_SUMMARY",
        "formula": (
            "Read KR valuation input cache files and count source-traced valuation-ready, "
            "complete, partial, and missing tickers."
        ),
        "quality_status": quality_status,
        "quality_flags": quality_flags,
    }
    return {
        "market": "KR",
        "data_backend": "kr_valuation_input_cache",
        "data_mode": "source_backed_cache" if ready_count else "source_backed_required",
        "coverage_status": coverage_status,
        "quality_status": quality_status,
        "summary": {
            "tickers_expected": len(rows),
            "cache_files_found": sum(1 for row in rows if row["cache_found"]),
            "valuation_ready": ready_count,
            "complete": complete_count,
            "partial_source_backed": partial_count,
            "missing": missing_count,
            "full_coverage_ready": sum(1 for row in rows if row["full_coverage_ready"]),
            "financial_numbers_allowed": sum(1 for row in rows if row["financial_numbers_allowed"]),
        },
        "quality_flags": quality_flags,
        "rows": rows,
        "source_trace": source_trace,
    }


def valuation_points_from_kr_cache(
    ticker: str,
    metric: str,
) -> KrValuationCachePayload | None:
    payload, path = _latest_payload(ticker)
    if payload is None or path is None:
        return None

    normalized_metric = _METRIC_ALIASES.get(metric, metric)
    raw_cache_points = [
        point
        for point in payload.get("valuation_points") or []
        if point.get("metric") == normalized_metric
    ]
    cache_points = [
        point
        for point in raw_cache_points
        if _record_has_source_backed_trace(point, "source_trace")
    ]
    dividend_facts = _dividend_facts_by_year(payload)
    rejected_points = len(raw_cache_points) - len(cache_points)
    points = [
        _valuation_point_from_cache(
            point,
            dividend_facts.get(int(point["fiscal_year"])),
        )
        for point in cache_points
    ]
    price_points = _price_points_from_facts(payload)
    quality_flags = list(payload.get("quality_flags") or [])
    if rejected_points:
        quality_flags.append("rejected_kr_cache_points_missing_source_trace")
    if dividend_facts:
        quality_flags = [flag for flag in quality_flags if flag != "missing_dividend_source"]
    metric_label = _METRIC_LABELS.get(normalized_metric, normalized_metric)
    dividend_status = payload.get("dividend_status") or {}
    if dividend_facts:
        dividend_status = {
            "status": "ok",
            "method": "source_backed_dividend_per_share",
            "dividend_years": sorted(dividend_facts),
            "quality_flags": ["source_backed_dividend"],
        }
    meta = {
        "data_backend": "kr_valuation_input_cache",
        "data_mode": "source_backed_cache" if points else "source_backed_required",
        "financial_numbers_allowed": bool(points),
        "cache_path": str(path),
        "cache_generated_at": payload.get("generated_at"),
        "cache_status": payload.get("status"),
        "coverage_status": payload.get("coverage_status"),
        "full_coverage_ready": payload.get("full_coverage_ready"),
        "coverage_years": payload.get("coverage_years") or {},
        "missing_years": payload.get("missing_years") or {},
        "market_gap_diagnostics": payload.get("market_gap_diagnostics") or [],
        "financial_gap_diagnostics": payload.get("financial_gap_diagnostics") or [],
        "valuation_ready": bool(points),
        "rejected_cache_points": rejected_points,
        "metric_status": payload.get("metric_status") or {},
        "dividend_status": dividend_status,
        "quality_flags": quality_flags,
        "source_note": (
            "KR valuation input cache is generated from append-only raw OpenDART, "
            "pykrx, and marcap evidence before database persistence."
        ),
    }
    return KrValuationCachePayload(
        points=points,
        metric_label=metric_label,
        meta=meta,
        price_points=price_points,
    )


def _kr_cache_coverage_row(ticker: str) -> dict[str, Any]:
    normalized_ticker = ticker.upper()
    payload, path = _latest_payload(normalized_ticker)
    if payload is None or path is None:
        return {
            "ticker": normalized_ticker,
            "cache_found": False,
            "valuation_ready": False,
            "financial_numbers_allowed": False,
            "full_coverage_ready": False,
            "coverage_status": "missing_source_backed_cache",
            "cache_status": "missing",
            "cache_path": None,
            "valuation_years": [],
            "missing_years": {
                "market_input": [],
                "financial_metric": [],
            },
            "gap_audit_refs": [],
            "quality_flags": ["missing_kr_valuation_cache"],
            "source_note": "No source-backed KR valuation input cache file was found for this ticker.",
        }
    valuation_points = payload.get("valuation_points") or []
    source_backed_points = [
        point
        for point in valuation_points
        if isinstance(point, dict) and _record_has_source_backed_trace(point, "source_trace")
    ]
    rejected_points = len(valuation_points) - len(source_backed_points)
    valuation_years = sorted(
        {
            int(point["fiscal_year"])
            for point in source_backed_points
            if str(point.get("fiscal_year", "")).isdigit()
        }
    )
    quality_flags = list(payload.get("quality_flags") or [])
    if rejected_points:
        quality_flags.append("rejected_kr_cache_points_missing_source_trace")
    coverage_status = str(payload.get("coverage_status") or "missing_source_backed_cache")
    valuation_ready = bool(source_backed_points)
    if not valuation_ready:
        coverage_status = "missing_source_traced_valuation_points"
        quality_flags.append("missing_source_traced_valuation_points")
    return {
        "ticker": normalized_ticker,
        "cache_found": True,
        "valuation_ready": valuation_ready,
        "financial_numbers_allowed": valuation_ready,
        "full_coverage_ready": bool(payload.get("full_coverage_ready")),
        "coverage_status": coverage_status,
        "cache_status": payload.get("status"),
        "cache_path": str(path),
        "cache_generated_at": payload.get("generated_at"),
        "valuation_years": valuation_years,
        "missing_years": payload.get("missing_years") or {
            "market_input": [],
            "financial_metric": [],
        },
        "market_gap_count": len(payload.get("market_gap_diagnostics") or []),
        "financial_gap_count": len(payload.get("financial_gap_diagnostics") or []),
        "gap_audit_refs": _kr_gap_audit_refs(normalized_ticker, payload),
        "rejected_cache_points": rejected_points,
        "quality_flags": quality_flags,
        "source_note": "KR valuation cache row is derived from append-only raw source evidence metadata.",
    }


def _kr_gap_audit_refs(ticker: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for gap in payload.get("market_gap_diagnostics") or []:
        if not isinstance(gap, dict):
            continue
        fiscal_year = _safe_year(gap.get("fiscal_year"))
        if fiscal_year is None:
            continue
        status = _safe_status(gap)
        fact_name = f"data_quality.kr_market_gap.{status}"
        source_document_id = (
            gap.get("pykrx_source_document_id")
            or gap.get("marcap_source_document_id")
            or f"kr-cache:{ticker}:{fiscal_year}:market-gap:{status}"
        )
        refs.append(
            {
                "scope": "market",
                "fiscal_year": fiscal_year,
                "status": status,
                "fact_name": fact_name,
                "fact_id": _gap_fact_id(ticker, fiscal_year, fact_name),
                "label": f"Market FY{fiscal_year}",
                "source_document_id": source_document_id,
                "source_type": "kr_cache_market_gap_diagnostic",
                "method": "KR_CACHE_MARKET_GAP_DIAGNOSTIC",
                "quality_status": status,
                "reason": gap.get("reason") or status,
                "next_action": gap.get("next_action"),
            }
        )
    for gap in payload.get("financial_gap_diagnostics") or []:
        if not isinstance(gap, dict):
            continue
        fiscal_year = _safe_year(gap.get("fiscal_year"))
        if fiscal_year is None:
            continue
        status = _safe_status(gap)
        fact_name = f"data_quality.kr_financial_gap.{status}"
        source_document_id = (
            gap.get("source_document_id")
            or gap.get("filing_id")
            or f"opendart:{ticker}:{fiscal_year}:status:{gap.get('opendart_status') or status}"
        )
        refs.append(
            {
                "scope": "financial",
                "fiscal_year": fiscal_year,
                "status": status,
                "fact_name": fact_name,
                "fact_id": _gap_fact_id(ticker, fiscal_year, fact_name),
                "label": f"Metric FY{fiscal_year}",
                "source_document_id": source_document_id,
                "source_type": "kr_cache_financial_gap_diagnostic",
                "method": "KR_CACHE_FINANCIAL_GAP_DIAGNOSTIC",
                "quality_status": status,
                "reason": gap.get("reason") or gap.get("opendart_message") or status,
                "next_action": gap.get("next_action"),
            }
        )
    return refs


def _safe_year(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_status(gap: dict[str, Any]) -> str:
    status = str(gap.get("status") or "unknown").strip()
    return status or "unknown"


def _gap_fact_id(ticker: str, fiscal_year: int, fact_name: str) -> str:
    safe_fact = fact_name.replace(" ", "_").replace("/", "_")
    return f"{ticker}-{fiscal_year}-{safe_fact}"


def _latest_payload(ticker: str) -> tuple[dict[str, Any] | None, Path | None]:
    normalized = ticker.upper().replace(".", "_")
    root = Path(os.getenv("KR_VALUATION_INPUT_CACHE_DIR") or _DEFAULT_CACHE_ROOT)
    paths = sorted(
        root.glob(f"{normalized}-*-valuation-inputs.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(payload.get("ticker", "")).upper() == ticker.upper():
            return payload, path
    return None, None


def _valuation_point_from_cache(
    point: dict[str, Any],
    dividend_fact: dict[str, Any] | None = None,
) -> ValuationPoint:
    trace = dict(point.get("source_trace") or {})
    trace["data_backend"] = "kr_valuation_input_cache"
    dividend = Decimal("0")
    if dividend_fact is not None:
        dividend = Decimal(str(dividend_fact["value"]))
        trace["dividend_source_trace"] = dict(dividend_fact["source_trace"])
        trace["quality_flags"] = sorted(
            {
                *[
                    flag
                    for flag in trace.get("quality_flags", [])
                    if flag != "missing_dividend_source"
                ],
                "source_backed_dividend",
            }
        )
    else:
        trace["dividend_source_trace"] = {
            "source": "missing",
            "source_type": "missing_source_backed_dividend",
            "source_document_id": (
                f"{point['ticker']}-{point['fiscal_year']}-missing-dividend-source"
            ),
            "filing_id": f"{point['ticker']}-{point['fiscal_year']}-missing-dividend-source",
            "period": point.get("period") or f"FY{point['fiscal_year']}",
            "unit": "KRW/share",
            "currency": point.get("currency") or "KRW",
            "method": "source_required",
            "formula": "dividend per share is not available in the KR valuation input cache",
            "quality_status": "missing_source_backed_dividend_per_share",
            "quality_flags": ["missing_dividend_source"],
        }
    return ValuationPoint(
        fiscal_year=int(point["fiscal_year"]),
        metric=Decimal(str(point["metric_value"])),
        price=Decimal(str(point["price"])),
        dividend=dividend,
        forecast_flag=False,
        source_trace=trace,
    )


def _dividend_facts_by_year(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    facts: dict[int, dict[str, Any]] = {}
    for fact in payload.get("normalized_facts") or []:
        if fact.get("metric") != "dividend_per_share":
            continue
        if not _record_has_source_backed_trace(fact, "source_trace"):
            continue
        fiscal_year = fact.get("fiscal_year")
        if not isinstance(fiscal_year, int):
            continue
        facts[fiscal_year] = fact
    return facts


def _price_points_from_facts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for fact in payload.get("normalized_facts") or []:
        if fact.get("metric") != "price_close":
            continue
        if not _record_has_source_backed_trace(fact, "source_trace"):
            continue
        trace = dict(fact.get("source_trace") or {})
        points.append(
            {
                "date": trace.get("period") or f"{fact.get('fiscal_year')}-12-31",
                "fiscal_year": fact.get("fiscal_year"),
                "price": fact.get("value"),
                "close_price": fact.get("value"),
                "currency": fact.get("currency") or trace.get("currency"),
                "frequency": "annual_source_cache",
                "source_trace": trace,
            }
        )
    return sorted(points, key=lambda item: (item.get("date") or ""))


def _record_has_source_backed_trace(record: dict[str, Any], key: str) -> bool:
    trace = record.get(key)
    if not isinstance(trace, dict):
        return False
    if validate_source_trace(trace).status != "passed":
        return False
    return not _contains_non_production_marker(trace)


def _contains_non_production_marker(value: Any) -> bool:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True).lower()
    except TypeError:
        text = str(value).lower()
    return any(marker in text for marker in _NON_PRODUCTION_MARKERS)
