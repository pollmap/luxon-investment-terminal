from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from backend.normalize.enums import NormalizationMethod, QualityStatus, SectorPolicy
from backend.normalize.schemas import (
    AdjustedEarningsRecord,
    NormalizationPolicy,
    SourceDocument,
    SourceTrace,
)
from packages.core.universe import KR_TOP_MARKET_CAP_PRIORITY_NAMES
from packages.quality import validate_source_trace
from services.api.sample_data import SAMPLE_SECURITY_META
from services.ingestion_worker.kr_valuation_warehouse import (
    DEFAULT_KR_VALUATION_CACHE_DIR,
    _canonical_kr_ticker,
    _contains_non_production_marker,
    _latest_cache_payload,
    _parse_tickers,
)
from services.ingestion_worker.repository import IngestionRepository

DEFAULT_POLICY_KEY = NormalizationPolicy().key
LOADER_SOURCE = "kr_valuation_cache"
LOADER_METHOD = "KR_CACHE_TO_POSTGRES"
ADJUSTED_EPS_METRIC = "adjusted_operating_eps"
PRICE_METRIC = "price_close"
DIVIDEND_METRIC = "dividend_per_share"
MARKET_CAP_METRIC = "market_cap"
LISTED_SHARES_METRIC = "listed_shares"
MARKET_METRICS = {PRICE_METRIC, MARKET_CAP_METRIC, LISTED_SHARES_METRIC}


def load_kr_valuation_cache_to_postgres(
    tickers: str | Iterable[str],
    *,
    cache_dir: Path = DEFAULT_KR_VALUATION_CACHE_DIR,
    dry_run: bool = False,
    strict: bool = False,
) -> dict[str, Any]:
    """Promote source-backed KR valuation cache rows into the Postgres API schema."""

    requested_tickers = [_canonical_kr_ticker(ticker) for ticker in _parse_tickers(tickers)]
    loaded_at = datetime.now(UTC)

    rows: list[dict[str, Any]] = []
    missing_tickers: list[str] = []
    rejected_fact_rows = 0
    rejected_valuation_points = 0
    totals = _zero_counts()

    repo = None if dry_run else IngestionRepository()

    for ticker in requested_tickers:
        payload, cache_path = _latest_cache_payload(ticker, cache_dir)
        ticker_counts = _zero_counts()
        ticker_rejected_facts = 0
        ticker_rejected_points = 0
        if payload is None or cache_path is None:
            missing_tickers.append(ticker)
            rows.append(
                {
                    "ticker": ticker,
                    "cache_found": False,
                    **ticker_counts,
                    "rejected_fact_rows": 0,
                    "rejected_valuation_points": 0,
                }
            )
            continue

        normalized_facts = [
            fact
            for fact in (payload.get("normalized_facts") or [])
            if isinstance(fact, dict)
        ]
        valuation_points = [
            point
            for point in (payload.get("valuation_points") or [])
            if isinstance(point, dict)
        ]
        eligible_facts: list[dict[str, Any]] = []
        eligible_points: list[dict[str, Any]] = []
        for fact in normalized_facts:
            if _eligible_source_trace(fact.get("source_trace")):
                eligible_facts.append(fact)
            else:
                rejected_fact_rows += 1
                ticker_rejected_facts += 1
        for point in valuation_points:
            if _eligible_source_trace(point.get("source_trace")):
                eligible_points.append(point)
            else:
                rejected_valuation_points += 1
                ticker_rejected_points += 1

        index = _fact_index(eligible_facts)
        ticker_counts.update(_planned_counts(eligible_facts))
        ticker_counts["source_documents"] = 1
        ticker_counts["raw_objects"] = 1

        if not dry_run and repo is not None:
            stored_security = repo.ensure_security(
                ticker=ticker,
                name=_security_name(ticker),
                country="KR",
                currency="KRW",
                exchange="KR",
            )
            run_id = repo.start_run(
                "KR",
                LOADER_SOURCE,
                ticker,
                metadata={
                    "cache_path": str(cache_path),
                    "coverage_status": payload.get("coverage_status"),
                    "valuation_ready": payload.get("valuation_ready"),
                    "loaded_at": loaded_at.isoformat(),
                },
            )
            try:
                source_document_id = repo.store_source_document(
                    stored_security.id,
                    _cache_source_document(ticker, payload, cache_path),
                    LOADER_SOURCE,
                )
                repo.store_raw_object(
                    run_id,
                    source_document_id,
                    "KR",
                    LOADER_SOURCE,
                    ticker,
                    identifier=_cache_identifier(ticker, payload),
                    source_url=str(cache_path),
                    local_path=str(cache_path),
                    content_hash=_sha256(cache_path),
                    content_type="application/json",
                    metadata={
                        "coverage_status": payload.get("coverage_status"),
                        "valuation_ready": payload.get("valuation_ready"),
                        "source_trace_contract": "storage_ready",
                    },
                )
                _persist_facts(
                    repo,
                    stored_security.id,
                    source_document_id,
                    eligible_facts,
                    index,
                )
                repo.finish_run(run_id, "succeeded")
            except Exception as exc:
                repo.finish_run(run_id, "failed", exc.__class__.__name__)
                raise

        for key, value in ticker_counts.items():
            totals[key] += value
        rows.append(
            {
                "ticker": ticker,
                "cache_found": True,
                "cache_path": str(cache_path),
                **ticker_counts,
                "valuation_points_seen": len(eligible_points),
                "rejected_fact_rows": ticker_rejected_facts,
                "rejected_valuation_points": ticker_rejected_points,
                "coverage_status": payload.get("coverage_status"),
                "valuation_ready": bool(ticker_counts["adjusted_earnings"] and ticker_counts["price_bars"]),
            }
        )

    status = _summary_status(
        requested_tickers=requested_tickers,
        missing_tickers=missing_tickers,
        adjusted_earnings=totals["adjusted_earnings"],
        price_bars=totals["price_bars"],
        rejected_fact_rows=rejected_fact_rows,
        rejected_valuation_points=rejected_valuation_points,
        strict=strict,
    )
    quality_flags = _quality_flags(
        missing_tickers=missing_tickers,
        adjusted_earnings=totals["adjusted_earnings"],
        price_bars=totals["price_bars"],
        rejected_fact_rows=rejected_fact_rows,
        rejected_valuation_points=rejected_valuation_points,
    )
    return {
        "status": status,
        "market": "KR",
        "data_backend": "postgres",
        "dry_run": dry_run,
        "policy": DEFAULT_POLICY_KEY,
        "tickers_expected": len(requested_tickers),
        "cache_files_found": len(requested_tickers) - len(missing_tickers),
        "missing_tickers": missing_tickers,
        **totals,
        "rejected_fact_rows": rejected_fact_rows,
        "rejected_valuation_points": rejected_valuation_points,
        "strict": strict,
        "rows": rows,
        "quality_status": "passed" if status == "ok" else "warning",
        "quality_flags": quality_flags,
        "source_trace": {
            "source": "kr_valuation_postgres_loader",
            "source_type": "postgres_loader",
            "source_document_id": str(cache_dir),
            "filing_id": "KR-VALUATION-POSTGRES-LOAD",
            "period": "latest_source_backed_cache",
            "available_at": loaded_at.isoformat(),
            "unit": "row_count",
            "currency": "KRW",
            "method": LOADER_METHOD,
            "formula": (
                "Validate KR valuation input cache source traces and promote source-backed "
                "facts into adjusted_earnings, metric_values, price_bars, dividends, "
                "source_documents, and raw_objects."
            ),
            "quality_status": "passed" if status == "ok" else "warning",
            "quality_flags": quality_flags,
        },
    }


def _persist_facts(
    repo: IngestionRepository,
    security_id: Any,
    source_document_id: Any,
    facts: list[dict[str, Any]],
    index: dict[tuple[int, str], dict[str, Any]],
) -> None:
    for fact in facts:
        metric = str(fact.get("metric") or "")
        fiscal_year = int(fact["fiscal_year"])
        value = _decimal(fact.get("value"))
        unit = str(fact.get("unit") or (fact.get("source_trace") or {}).get("unit") or "unknown")
        currency = str(fact.get("currency") or (fact.get("source_trace") or {}).get("currency") or "KRW")
        trace = dict(fact.get("source_trace") or {})
        formula = str(trace.get("formula") or f"{metric} from KR source-backed valuation cache")
        method = str(trace.get("method") or LOADER_METHOD)
        quality_status = str(trace.get("quality_status") or "source_backed")
        period_end = _period_end(fact, trace)

        repo.store_metric_value(
            security_id,
            metric,
            fiscal_year,
            value,
            unit,
            currency,
            formula,
            method,
            quality_status,
            trace,
            source_document_id=source_document_id,
        )

        if metric == ADJUSTED_EPS_METRIC:
            repo.store_adjusted_earnings(
                security_id,
                source_document_id,
                AdjustedEarningsRecord(
                    security_id=str(security_id),
                    ticker=str(fact.get("ticker") or ""),
                    fiscal_year=fiscal_year,
                    fiscal_period=str(fact.get("fiscal_period") or trace.get("fiscal_period") or "FY"),
                    period_end=period_end,
                    adjusted_eps=value,
                    company_adjusted_eps=value,
                    currency=currency,
                    method=NormalizationMethod.S3_MARKET_STANDARD_KR,
                    policy=DEFAULT_POLICY_KEY,
                    sector_policy=SectorPolicy.DEFAULT,
                    confidence=_confidence(trace),
                    quality_status=QualityStatus.PASSED,
                    flags=list(trace.get("quality_flags") or []),
                    formula=formula,
                    source_trace=SourceTrace.model_validate(trace),
                    metadata={
                        "cache_fact_id": fact.get("fact_id"),
                        "loader": LOADER_METHOD,
                    },
                ),
            )
        elif metric == PRICE_METRIC:
            enriched_trace = _price_trace_with_market_context(fiscal_year, trace, index)
            repo.store_price_bar(
                security_id,
                fiscal_year,
                period_end,
                value,
                currency,
                str(trace.get("source") or trace.get("source_type") or "kr_price_source"),
                enriched_trace,
            )
        elif metric == DIVIDEND_METRIC:
            repo.store_dividend(
                security_id,
                fiscal_year,
                period_end,
                value,
                currency,
                str(trace.get("source") or trace.get("source_type") or "kr_dividend_source"),
                trace,
            )

        if metric not in MARKET_METRICS:
            source = str(trace.get("source") or trace.get("source_type") or LOADER_SOURCE)
            repo.store_financial_fact(
                security_id,
                source_document_id,
                taxonomy="kr-valuation-cache",
                tag=metric,
                label=metric,
                fiscal_year=fiscal_year,
                fiscal_period=str(fact.get("fiscal_period") or trace.get("fiscal_period") or "FY"),
                period_start=_trace_date(trace.get("period_start")),
                period_end=period_end,
                filed_at=_trace_datetime(trace.get("filed_at")),
                accession_number=str(trace.get("filing_id") or trace.get("accession_number") or ""),
                form_type=str(trace.get("form") or trace.get("form_type") or "KR_SOURCE"),
                frame=None,
                unit=unit,
                currency=currency,
                value=value,
                source=source,
                source_url=trace.get("source_url") or trace.get("filing_url"),
                quality_status=quality_status,
                source_trace=trace,
                metadata={
                    "cache_fact_id": fact.get("fact_id"),
                    "loader": LOADER_METHOD,
                },
            )


def _planned_counts(facts: list[dict[str, Any]]) -> dict[str, int]:
    counts = _zero_counts()
    for fact in facts:
        metric = str(fact.get("metric") or "")
        counts["metric_values"] += 1
        if metric == ADJUSTED_EPS_METRIC:
            counts["adjusted_earnings"] += 1
        if metric == PRICE_METRIC:
            counts["price_bars"] += 1
        if metric == DIVIDEND_METRIC:
            counts["dividends"] += 1
        if metric not in MARKET_METRICS:
            counts["financial_facts"] += 1
    return counts


def _zero_counts() -> dict[str, int]:
    return {
        "source_documents": 0,
        "raw_objects": 0,
        "adjusted_earnings": 0,
        "metric_values": 0,
        "financial_facts": 0,
        "price_bars": 0,
        "dividends": 0,
    }


def _fact_index(facts: list[dict[str, Any]]) -> dict[tuple[int, str], dict[str, Any]]:
    index: dict[tuple[int, str], dict[str, Any]] = {}
    for fact in facts:
        fiscal_year = fact.get("fiscal_year")
        metric = str(fact.get("metric") or "")
        if fiscal_year in {None, ""} or not metric:
            continue
        index[(int(fiscal_year), metric)] = fact
    return index


def _price_trace_with_market_context(
    fiscal_year: int,
    trace: dict[str, Any],
    index: dict[tuple[int, str], dict[str, Any]],
) -> dict[str, Any]:
    enriched = dict(trace)
    market_cap = index.get((fiscal_year, MARKET_CAP_METRIC))
    listed_shares = index.get((fiscal_year, LISTED_SHARES_METRIC))
    if market_cap is not None:
        enriched["market_cap"] = market_cap.get("value")
        enriched["market_cap_source_trace"] = market_cap.get("source_trace")
    if listed_shares is not None:
        enriched["listed_shares"] = listed_shares.get("value")
        enriched["listed_shares_source_trace"] = listed_shares.get("source_trace")
    return enriched


def _eligible_source_trace(trace: Any) -> bool:
    if not isinstance(trace, dict):
        return False
    if validate_source_trace(trace).status != "passed":
        return False
    return not _contains_non_production_marker(trace)


def _security_name(ticker: str) -> str:
    meta = SAMPLE_SECURITY_META.get(ticker.upper())
    if meta:
        return str(meta.get("name") or ticker)
    return KR_TOP_MARKET_CAP_PRIORITY_NAMES.get(ticker.upper(), ticker)


def _cache_source_document(ticker: str, payload: dict[str, Any], cache_path: Path) -> SourceDocument:
    content_hash = _sha256(cache_path)
    return SourceDocument(
        id=content_hash,
        ticker=ticker,
        accession_number=_cache_identifier(ticker, payload),
        form_type="KR_VALUATION_INPUT_CACHE",
        filing_url=str(cache_path),
        source_url=str(cache_path),
        description="Source-backed KR valuation input cache promoted to Postgres",
        document_type="application/json",
        local_path=str(cache_path),
        content_hash=content_hash,
        metadata={
            "market": "KR",
            "coverage_status": payload.get("coverage_status"),
            "valuation_ready": payload.get("valuation_ready"),
            "cache_generated_at": payload.get("generated_at"),
            "loader": LOADER_METHOD,
        },
    )


def _cache_identifier(ticker: str, payload: dict[str, Any]) -> str:
    years = payload.get("years") or payload.get("coverage_years") or "latest"
    if isinstance(years, list) and years:
        years_label = f"{min(years)}-{max(years)}"
    else:
        years_label = str(years)
    return f"KR-VAL-CACHE:{ticker}:{years_label}"[:64]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"invalid numeric value for KR valuation cache: {value!r}") from exc


def _confidence(trace: dict[str, Any]) -> Decimal:
    value = trace.get("confidence")
    if value in {None, ""}:
        return Decimal("0.85")
    return _decimal(value)


def _period_end(fact: dict[str, Any], trace: dict[str, Any]) -> date:
    for value in (trace.get("period_end"), fact.get("period"), trace.get("period")):
        parsed = _trace_date(value)
        if parsed is not None:
            return parsed
    return date(int(fact["fiscal_year"]), 12, 31)


def _trace_date(value: Any) -> date | None:
    if value in {None, ""}:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value)
    if text.startswith("FY"):
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _trace_datetime(value: Any) -> datetime | None:
    if value in {None, ""}:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _summary_status(
    *,
    requested_tickers: list[str],
    missing_tickers: list[str],
    adjusted_earnings: int,
    price_bars: int,
    rejected_fact_rows: int,
    rejected_valuation_points: int,
    strict: bool,
) -> str:
    if not requested_tickers or adjusted_earnings == 0 or price_bars == 0:
        return "failed"
    if missing_tickers or rejected_fact_rows or rejected_valuation_points:
        return "failed" if strict else "warning"
    return "ok"


def _quality_flags(
    *,
    missing_tickers: list[str],
    adjusted_earnings: int,
    price_bars: int,
    rejected_fact_rows: int,
    rejected_valuation_points: int,
) -> list[str]:
    flags: list[str] = []
    if missing_tickers:
        flags.append("missing_kr_valuation_cache")
    if adjusted_earnings == 0:
        flags.append("missing_adjusted_earnings_rows")
    if price_bars == 0:
        flags.append("missing_price_bar_rows")
    if rejected_fact_rows:
        flags.append("rejected_kr_fact_rows_missing_source_trace")
    if rejected_valuation_points:
        flags.append("rejected_kr_valuation_points_missing_source_trace")
    return flags or ["source_trace_passed"]
