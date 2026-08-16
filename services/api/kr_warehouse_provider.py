from __future__ import annotations

import json
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import duckdb

from packages.quality import validate_source_trace
from packages.valuation.engine import ValuationPoint
from services.ingestion_worker.kr_valuation_warehouse import (
    DEFAULT_WAREHOUSE_DB_PATH,
    KR_NORMALIZED_FACTS_VIEW,
    KR_VALUATION_POINTS_VIEW,
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
class KrValuationWarehousePayload:
    points: list[ValuationPoint]
    metric_label: str
    meta: dict[str, Any]
    price_points: list[dict[str, Any]]


def financials_from_kr_warehouse(ticker: str) -> list[dict[str, Any]] | None:
    facts = normalized_facts_from_kr_warehouse(ticker)
    if facts is None:
        return None

    facts_by_year: dict[int, dict[str, dict[str, Any]]] = {}
    for fact in facts:
        metric = str(fact.get("metric") or "")
        fiscal_year = int(fact["fiscal_year"])
        facts_by_year.setdefault(fiscal_year, {})[metric] = fact

    rows: list[dict[str, Any]] = []
    for fiscal_year in sorted(facts_by_year):
        metrics = facts_by_year[fiscal_year]
        eps_fact = (
            metrics.get("adjusted_operating_eps")
            or metrics.get("diluted_eps")
            or metrics.get("basic_eps")
        )
        revenue_fact = metrics.get("revenue")
        fcf_fact = metrics.get("free_cash_flow") or metrics.get("fcf")
        roe_fact = metrics.get("roe") or metrics.get("roe_pct")
        roic_fact = metrics.get("roic") or metrics.get("roic_pct")
        debt_fact = metrics.get("debt_to_equity") or metrics.get("debt_ratio")
        operating_margin_fact = metrics.get("operating_margin") or metrics.get(
            "operating_margin_pct"
        )
        net_margin_fact = metrics.get("net_margin") or metrics.get("net_margin_pct")
        net_income_fact = metrics.get("net_income_parent") or metrics.get("net_income")
        operating_margin = _value_str(operating_margin_fact)
        operating_margin_trace = (
            operating_margin_fact["source_trace"] if operating_margin_fact else None
        )
        if operating_margin is None:
            operating_margin = _percent_ratio_str(
                metrics.get("operating_income"),
                revenue_fact,
            )
            if operating_margin is not None:
                operating_margin_trace = _derived_financial_metric_trace(
                    ticker=_canonical_kr_ticker(ticker),
                    fiscal_year=fiscal_year,
                    metric="operating_margin",
                    numerator_fact=metrics["operating_income"],
                    denominator_fact=revenue_fact,
                    formula="operating_margin = operating_income / revenue * 100",
                )
        net_margin = _value_str(net_margin_fact)
        net_margin_trace = net_margin_fact["source_trace"] if net_margin_fact else None
        if net_margin is None:
            net_margin = _percent_ratio_str(net_income_fact, revenue_fact)
            if net_margin is not None:
                net_margin_trace = _derived_financial_metric_trace(
                    ticker=_canonical_kr_ticker(ticker),
                    fiscal_year=fiscal_year,
                    metric="net_margin",
                    numerator_fact=net_income_fact,
                    denominator_fact=revenue_fact,
                    formula="net_margin = net_income_to_parent / revenue * 100",
                )
        metric_traces = {
            "eps": eps_fact["source_trace"] if eps_fact else None,
            "revenue": revenue_fact["source_trace"] if revenue_fact else None,
            "fcf": fcf_fact["source_trace"] if fcf_fact else None,
            "operating_margin": operating_margin_trace,
            "net_margin": net_margin_trace,
            "roe": roe_fact["source_trace"] if roe_fact else None,
            "roic": roic_fact["source_trace"] if roic_fact else None,
            "debt_to_equity": debt_fact["source_trace"] if debt_fact else None,
        }
        metric_traces = {key: trace for key, trace in metric_traces.items() if trace}
        if not metric_traces:
            continue

        base_fact = eps_fact or revenue_fact or fcf_fact or roe_fact or roic_fact or debt_fact
        if base_fact is None:
            continue
        row_trace = _financial_row_trace(
            ticker=_canonical_kr_ticker(ticker),
            fiscal_year=fiscal_year,
            base_trace=dict(base_fact["source_trace"]),
            metrics=metrics,
        )
        rows.append(
            {
                "fiscal_year": fiscal_year,
                "revenue": _value_str(revenue_fact),
                "eps": _value_str(eps_fact),
                "gaap_eps_diluted": _value_str(metrics.get("diluted_eps")),
                "fcf": _value_str(fcf_fact),
                "gross_margin": _value_str(
                    metrics.get("gross_margin") or metrics.get("gross_margin_pct")
                ),
                "operating_margin": operating_margin,
                "net_margin": net_margin,
                "roe": _value_str(roe_fact),
                "roic": _value_str(roic_fact),
                "debt_to_equity": _value_str(debt_fact),
                "method": row_trace["method"],
                "confidence": row_trace.get("confidence"),
                "source_trace": row_trace,
                "metric_traces": metric_traces,
            }
        )
    if not rows:
        return None
    return rows


def normalized_facts_from_kr_warehouse(ticker: str) -> list[dict[str, Any]] | None:
    db_path = _warehouse_db_path()
    if db_path is None:
        return None
    if not db_path.exists():
        return None
    ticker = _canonical_kr_ticker(ticker)
    try:
        with duckdb.connect(str(db_path), read_only=True) as connection:
            rows = connection.execute(
                f"""
                SELECT fiscal_year, fiscal_period, period, metric, value, unit, currency,
                       source_trace_json, cache_path, loaded_at
                FROM {KR_NORMALIZED_FACTS_VIEW}
                WHERE ticker = ?
                ORDER BY fiscal_year, metric
                """,
                [ticker],
            ).fetchall()
    except duckdb.Error:
        return None
    if not rows:
        return None

    facts: list[dict[str, Any]] = []
    for row in rows:
        (
            fiscal_year,
            fiscal_period,
            period,
            metric,
            value,
            unit,
            currency,
            trace_json,
            cache_path,
            loaded_at,
        ) = row
        trace = _json_loads(trace_json)
        if validate_source_trace(trace).status != "passed":
            continue
        trace["data_backend"] = "kr_valuation_warehouse"
        trace["warehouse_view"] = KR_NORMALIZED_FACTS_VIEW
        trace["cache_path"] = cache_path
        trace["loaded_at"] = loaded_at
        trace["quality_flags"] = sorted(
            set([*trace.get("quality_flags", []), "source_trace_passed"])
        )
        facts.append(
            {
                "fiscal_year": int(fiscal_year),
                "fiscal_period": fiscal_period,
                "period": period,
                "metric": str(metric),
                "value": value,
                "unit": unit,
                "currency": currency,
                "method": trace.get("method") or trace.get("source_type"),
                "policy": "kr_warehouse_normalized_fact",
                "confidence": trace.get("confidence"),
                "quality_status": trace.get("quality_status"),
                "flags": trace.get("quality_flags", []),
                "formula": trace.get("formula"),
                "source_trace": trace,
            }
        )
    if not facts:
        return None
    return facts


def source_coverage_rows_from_kr_warehouse(tickers: Iterable[str]) -> list[dict[str, Any]] | None:
    """Build source-coverage rows from the local KR DuckDB/Parquet warehouse.

    This is a development E2E proof path. Protected deployment still promotes
    rows into Postgres/Neon, but local source-backed rows should not be reported
    as missing when the warehouse already contains trace-validated evidence.
    """

    db_path = _warehouse_db_path()
    if db_path is None or not db_path.exists():
        return None

    requested = [_canonical_kr_ticker(ticker) for ticker in tickers]
    coverage_rows: list[dict[str, Any]] = []
    any_rows = False
    try:
        with duckdb.connect(str(db_path), read_only=True) as connection:
            for ticker in requested:
                fact_rows = connection.execute(
                    f"""
                    SELECT fiscal_year, metric, source_document_id, filing_id, method
                    FROM {KR_NORMALIZED_FACTS_VIEW}
                    WHERE ticker = ?
                    """,
                    [ticker],
                ).fetchall()
                point_rows = connection.execute(
                    f"""
                    SELECT fiscal_year, metric, source_document_id, filing_id, method
                    FROM {KR_VALUATION_POINTS_VIEW}
                    WHERE ticker = ?
                    """,
                    [ticker],
                ).fetchall()
                if fact_rows or point_rows:
                    any_rows = True
                coverage_rows.append(_source_coverage_row_from_warehouse(ticker, fact_rows, point_rows))
    except duckdb.Error:
        return None
    if not any_rows:
        return None
    return coverage_rows


def valuation_points_from_kr_warehouse(
    ticker: str,
    metric: str,
) -> KrValuationWarehousePayload | None:
    db_path = _warehouse_db_path()
    if db_path is None:
        return None
    if not db_path.exists():
        return None
    normalized_metric = _METRIC_ALIASES.get(metric, metric)
    ticker = _canonical_kr_ticker(ticker)
    try:
        with duckdb.connect(str(db_path), read_only=True) as connection:
            rows = connection.execute(
                f"""
                SELECT fiscal_year, period, metric, metric_value, price, currency,
                       source_trace_json, quality_flags_json, cache_path, loaded_at
                FROM {KR_VALUATION_POINTS_VIEW}
                WHERE ticker = ? AND metric = ?
                ORDER BY fiscal_year
                """,
                [ticker, normalized_metric],
            ).fetchall()
            price_rows = connection.execute(
                f"""
                SELECT fiscal_year, period, value, currency, source_trace_json,
                       cache_path, loaded_at
                FROM {KR_NORMALIZED_FACTS_VIEW}
                WHERE ticker = ? AND metric = 'price_close'
                ORDER BY fiscal_year
                """,
                [ticker],
            ).fetchall()
            dividend_rows = connection.execute(
                f"""
                SELECT fiscal_year, period, value, currency, source_trace_json,
                       cache_path, loaded_at
                FROM {KR_NORMALIZED_FACTS_VIEW}
                WHERE ticker = ? AND metric = 'dividend_per_share'
                ORDER BY fiscal_year
                """,
                [ticker],
            ).fetchall()
    except duckdb.Error:
        return None
    if not rows:
        return None

    points: list[ValuationPoint] = []
    rejected_rows = 0
    cache_paths: set[str] = set()
    loaded_at_values: set[str] = set()
    dividend_facts = _dividend_facts_from_rows(dividend_rows)
    for row in rows:
        (
            fiscal_year,
            period,
            _metric,
            metric_value,
            price,
            currency,
            trace_json,
            quality_flags_json,
            cache_path,
            loaded_at,
        ) = row
        trace = _json_loads(trace_json)
        if validate_source_trace(trace).status != "passed":
            rejected_rows += 1
            continue
        trace["data_backend"] = "kr_valuation_warehouse"
        trace["warehouse_view"] = KR_VALUATION_POINTS_VIEW
        trace["cache_path"] = cache_path
        trace["loaded_at"] = loaded_at
        _promote_valuation_input_traces(trace, cache_path, loaded_at)
        dividend = Decimal("0")
        dividend_fact = dividend_facts.get(int(fiscal_year))
        if dividend_fact:
            dividend = Decimal(str(dividend_fact["value"]))
            trace["dividend_source_trace"] = dividend_fact["source_trace"]
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
            trace["dividend_source_trace"] = _missing_dividend_trace(
                ticker,
                int(fiscal_year),
                str(period),
                str(currency),
            )
        quality_flags = _json_loads(quality_flags_json)
        if isinstance(quality_flags, list):
            merged_flags = set(trace.get("quality_flags", []))
            for flag in quality_flags:
                if dividend_fact and flag == "missing_dividend_source":
                    continue
                merged_flags.add(flag)
            trace["quality_flags"] = sorted(merged_flags)
        points.append(
            ValuationPoint(
                fiscal_year=int(fiscal_year),
                metric=Decimal(str(metric_value)),
                price=Decimal(str(price)),
                dividend=dividend,
                forecast_flag=False,
                source_trace=trace,
            )
        )
        if cache_path:
            cache_paths.add(str(cache_path))
        if loaded_at:
            loaded_at_values.add(str(loaded_at))

    if not points:
        return None

    price_points = _price_points_from_rows(ticker, price_rows)
    quality_flags = ["source_trace_passed"]
    if rejected_rows:
        quality_flags.append("rejected_kr_warehouse_rows_missing_source_trace")
    dividend_status = {
        "status": "ok",
        "method": "source_backed_dividend_per_share",
        "dividend_years": sorted(dividend_facts),
        "quality_flags": ["source_backed_dividend"],
    } if dividend_facts else {
        "status": "blocked",
        "reason": "missing_source_backed_dividend_per_share",
        "quality_flags": ["missing_dividend_source"],
    }
    return KrValuationWarehousePayload(
        points=points,
        metric_label=_METRIC_LABELS.get(normalized_metric, normalized_metric),
        price_points=price_points,
        meta={
            "data_backend": "kr_valuation_warehouse",
            "data_mode": "source_backed",
            "financial_numbers_allowed": True,
            "valuation_ready": True,
            "warehouse_db_path": str(db_path),
            "warehouse_views": {
                "normalized_facts": KR_NORMALIZED_FACTS_VIEW,
                "valuation_points": KR_VALUATION_POINTS_VIEW,
            },
            "cache_paths": sorted(cache_paths),
            "loaded_at": sorted(loaded_at_values)[-1] if loaded_at_values else None,
            "rejected_warehouse_rows": rejected_rows,
            "dividend_status": dividend_status,
            "quality_flags": quality_flags,
            "source_note": (
                "KR valuation rows are read from source-traced DuckDB/Parquet warehouse "
                "views generated from append-only OpenDART, pykrx, and marcap evidence."
            ),
        },
    )


def _source_coverage_row_from_warehouse(
    ticker: str,
    fact_rows: list[tuple],
    point_rows: list[tuple],
) -> dict[str, Any]:
    facts_by_metric: dict[str, set[int]] = {}
    source_documents: set[str] = set()
    methods: set[str] = set()
    for fiscal_year, metric, source_document_id, filing_id, method in fact_rows:
        year = int(fiscal_year)
        metric_key = str(metric)
        facts_by_metric.setdefault(metric_key, set()).add(year)
        if source_document_id:
            source_documents.add(str(source_document_id))
        if filing_id:
            source_documents.add(str(filing_id))
        if method:
            methods.add(str(method))

    valuation_years: set[int] = set()
    valuation_metric_keys: set[str] = set()
    for fiscal_year, metric, source_document_id, filing_id, method in point_rows:
        year = int(fiscal_year)
        valuation_years.add(year)
        valuation_metric_keys.add(str(metric))
        if source_document_id:
            source_documents.add(str(source_document_id))
        if filing_id:
            source_documents.add(str(filing_id))
        if method:
            methods.add(str(method))

    market_metric_keys = {"price_close", "market_cap", "listed_shares", "dividend_per_share"}
    financial_metric_keys = {
        metric
        for metric in facts_by_metric
        if metric not in market_metric_keys
    }
    financial_years = sorted(
        {
            year
            for metric in financial_metric_keys
            for year in facts_by_metric.get(metric, set())
        }
    )
    adjusted_years = sorted(
        set(facts_by_metric.get("adjusted_operating_eps", set()))
        | set(facts_by_metric.get("gaap_diluted_eps", set()))
        | set(facts_by_metric.get("diluted_eps", set()))
        | valuation_years
    )
    price_years = facts_by_metric.get("price_close", set())
    market_cap_years = facts_by_metric.get("market_cap", set())
    listed_shares_years = facts_by_metric.get("listed_shares", set())
    dividend_years = facts_by_metric.get("dividend_per_share", set())
    available_metric_keys = sorted(financial_metric_keys | valuation_metric_keys)
    return {
        "ticker": ticker,
        "security_count": 1 if fact_rows or point_rows else 0,
        "name": ticker,
        "country": "KR",
        "currency": "KRW",
        "market": "KR",
        "adjusted_years": len(adjusted_years),
        "latest_adjusted_year": max(adjusted_years) if adjusted_years else None,
        "s1_periods": 0,
        "s2_periods": 0,
        "s4_periods": 0,
        "price_years": len(price_years),
        "latest_price_year": max(price_years) if price_years else None,
        "market_cap_years": len(market_cap_years),
        "listed_shares_years": len(listed_shares_years),
        "financial_fact_years": len(financial_years),
        "financial_fact_tags": len(financial_metric_keys),
        "latest_financial_fact_year": max(financial_years) if financial_years else None,
        "financial_metric_years": len(financial_years),
        "financial_metric_keys": len(available_metric_keys),
        "available_metric_keys": available_metric_keys,
        "dividend_years": len(dividend_years),
        "consensus_forecast_years": 0,
        "consensus_valuation_years": 0,
        "consensus_snapshots": 0,
        "consensus_valuation_snapshots": 0,
        "latest_consensus_year": None,
        "adjustment_rows": 0,
        "source_documents": len(source_documents),
        "raw_objects": len(source_documents),
        "warehouse_methods": sorted(methods),
    }


def _warehouse_db_path() -> Path | None:
    configured_path = os.getenv("KR_VALUATION_WAREHOUSE_DB")
    if configured_path:
        return Path(configured_path)
    if os.getenv("KR_VALUATION_INPUT_CACHE_DIR"):
        return None
    return DEFAULT_WAREHOUSE_DB_PATH


def _canonical_kr_ticker(ticker: str) -> str:
    normalized = ticker.strip().upper()
    if "." not in normalized and normalized.isdigit() and len(normalized) == 6:
        return f"{normalized}.KS"
    return normalized


def _json_loads(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value or {}


def _value_str(fact: dict[str, Any] | None) -> str | None:
    if fact is None or fact.get("value") is None:
        return None
    return str(fact["value"])


def _percent_ratio_str(
    numerator_fact: dict[str, Any] | None,
    denominator_fact: dict[str, Any] | None,
) -> str | None:
    numerator = _decimal_value(numerator_fact)
    denominator = _decimal_value(denominator_fact)
    if numerator is None or denominator in {None, Decimal("0")}:
        return None
    return str(((numerator / denominator) * Decimal("100")).quantize(Decimal("0.01")))


def _decimal_value(fact: dict[str, Any] | None) -> Decimal | None:
    if fact is None or fact.get("value") in {None, ""}:
        return None
    try:
        return Decimal(str(fact["value"]))
    except Exception:
        return None


def _derived_financial_metric_trace(
    *,
    ticker: str,
    fiscal_year: int,
    metric: str,
    numerator_fact: dict[str, Any],
    denominator_fact: dict[str, Any] | None,
    formula: str,
) -> dict[str, Any]:
    numerator_trace = dict(numerator_fact["source_trace"])
    denominator_trace = dict(denominator_fact["source_trace"]) if denominator_fact else {}
    input_traces = {
        "numerator": numerator_trace,
        "denominator": denominator_trace,
    }
    input_fact_ids = [
        _trace_fact_id(numerator_fact),
        _trace_fact_id(denominator_fact),
    ]
    input_fact_ids = [fact_id for fact_id in input_fact_ids if fact_id]
    confidence = _minimum_trace_confidence(numerator_trace, denominator_trace)
    quality_flags = sorted(
        {
            *numerator_trace.get("quality_flags", []),
            *denominator_trace.get("quality_flags", []),
            "kr_warehouse_financials_derived_metric",
            "source_trace_passed",
        }
    )
    trace = {
        **denominator_trace,
        "source": "derived",
        "source_type": "kr_warehouse_financials_derived_metric",
        "source_document_id": f"derived:kr:{ticker}:{fiscal_year}:{metric}",
        "filing_id": f"derived:kr:{ticker}:{fiscal_year}:{metric}",
        "accession_number": f"derived:kr:{ticker}:{fiscal_year}:{metric}",
        "form": "derived_metric",
        "form_type": "derived_metric",
        "period": denominator_trace.get("period")
        or numerator_trace.get("period")
        or f"FY{fiscal_year}",
        "fiscal_year": fiscal_year,
        "fiscal_period": "FY",
        "period_start": denominator_trace.get("period_start")
        or numerator_trace.get("period_start"),
        "period_end": denominator_trace.get("period_end")
        or numerator_trace.get("period_end"),
        "available_at": denominator_trace.get("available_at")
        or numerator_trace.get("available_at"),
        "unit": "percent",
        "currency": denominator_trace.get("currency")
        or numerator_trace.get("currency")
        or "KRW",
        "method": "KR_WAREHOUSE_DERIVED_FINANCIAL_METRIC",
        "formula": formula,
        "input_fact_ids": input_fact_ids,
        "input_metrics": [
            str(numerator_fact.get("metric")),
            str(denominator_fact.get("metric")) if denominator_fact else "",
        ],
        "input_source_traces": input_traces,
        "adjustments": [],
        "confidence": confidence,
        "quality_flags": quality_flags,
        "quality_status": "source_backed_derived",
        "version": denominator_trace.get("version") or numerator_trace.get("version") or 1,
        "ticker": ticker,
        "data_backend": "kr_valuation_warehouse",
        "warehouse_view": KR_NORMALIZED_FACTS_VIEW,
    }
    validation = validate_source_trace(trace)
    if validation.status != "passed":
        trace["quality_flags"] = sorted({*quality_flags, *validation.flags})
        trace["quality_status"] = "warning"
    return trace


def _trace_fact_id(fact: dict[str, Any] | None) -> str | None:
    if not fact:
        return None
    trace = fact.get("source_trace") or {}
    return str(trace.get("source_document_id") or fact.get("metric") or "")


def _minimum_trace_confidence(*traces: dict[str, Any]) -> str | None:
    values: list[Decimal] = []
    for trace in traces:
        if not trace:
            continue
        value = trace.get("confidence")
        if value in {None, ""}:
            continue
        try:
            values.append(Decimal(str(value)))
        except Exception:
            continue
    if not values:
        return None
    return str(min(values))


def _financial_row_trace(
    *,
    ticker: str,
    fiscal_year: int,
    base_trace: dict[str, Any],
    metrics: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    input_metrics = sorted(metrics)
    input_fact_ids = [
        str(metric_fact["source_trace"].get("source_document_id") or metric_fact["metric"])
        for metric_fact in metrics.values()
        if isinstance(metric_fact.get("source_trace"), dict)
    ]
    quality_flags = sorted(
        {
            *base_trace.get("quality_flags", []),
            "kr_warehouse_financials_partial_row",
            "source_trace_passed",
        }
    )
    return {
        **base_trace,
        "source": base_trace.get("source") or "kr_valuation_warehouse",
        "source_type": "kr_warehouse_financials_row",
        "source_document_id": (
            base_trace.get("source_document_id")
            or f"kr-warehouse:{ticker}:{fiscal_year}:financials-row"
        ),
        "filing_id": (
            base_trace.get("filing_id")
            or base_trace.get("accession_number")
            or f"kr-warehouse:{ticker}:{fiscal_year}:financials-row"
        ),
        "period": base_trace.get("period") or f"FY{fiscal_year}",
        "unit": "financial_statement_row",
        "currency": base_trace.get("currency") or "KRW",
        "method": "KR_WAREHOUSE_FINANCIALS_ROW_ASSEMBLY",
        "formula": (
            "financial row assembled from source-traced KR warehouse normalized facts; "
            "cells without a matching source-backed fact remain null"
        ),
        "quality_status": base_trace.get("quality_status") or "source_backed_partial_financials",
        "quality_flags": quality_flags,
        "confidence": base_trace.get("confidence"),
        "ticker": ticker,
        "fiscal_year": fiscal_year,
        "input_metrics": input_metrics,
        "input_fact_ids": input_fact_ids,
        "data_backend": "kr_valuation_warehouse",
        "warehouse_view": KR_NORMALIZED_FACTS_VIEW,
    }


def _missing_dividend_trace(
    ticker: str,
    fiscal_year: int,
    period: str,
    currency: str,
) -> dict[str, Any]:
    return {
        "source": "missing",
        "source_type": "missing_source_backed_dividend",
        "source_document_id": f"{ticker}-{fiscal_year}-missing-dividend-source",
        "filing_id": f"{ticker}-{fiscal_year}-missing-dividend-source",
        "period": period,
        "unit": "KRW/share",
        "currency": currency,
        "method": "source_required",
        "formula": "dividend per share is not available in the KR valuation warehouse",
        "quality_status": "missing_source_backed_dividend_per_share",
        "quality_flags": ["missing_dividend_source"],
    }


def _price_points_from_rows(ticker: str, rows: list[tuple]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for fiscal_year, period, value, currency, trace_json, cache_path, loaded_at in rows:
        trace = _json_loads(trace_json)
        if validate_source_trace(trace).status != "passed":
            continue
        _enrich_kr_child_trace(
            trace,
            cache_path=cache_path,
            loaded_at=loaded_at,
            warehouse_view=KR_NORMALIZED_FACTS_VIEW,
        )
        points.append(
            {
                "date": trace.get("period") or period or f"{fiscal_year}-12-31",
                "fiscal_year": fiscal_year,
                "price": value,
                "close_price": value,
                "currency": currency or trace.get("currency"),
                "frequency": "annual_warehouse",
                "source_trace": trace,
                "ticker": ticker,
            }
        )
    return points


def _dividend_facts_from_rows(rows: list[tuple]) -> dict[int, dict[str, Any]]:
    facts: dict[int, dict[str, Any]] = {}
    for fiscal_year, period, value, currency, trace_json, cache_path, loaded_at in rows:
        trace = _json_loads(trace_json)
        if validate_source_trace(trace).status != "passed":
            continue
        _enrich_kr_child_trace(
            trace,
            cache_path=cache_path,
            loaded_at=loaded_at,
            warehouse_view=KR_NORMALIZED_FACTS_VIEW,
        )
        facts[int(fiscal_year)] = {
            "period": period,
            "value": value,
            "currency": currency,
            "source_trace": trace,
        }
    return facts


def _promote_valuation_input_traces(
    trace: dict[str, Any],
    cache_path: Any,
    loaded_at: Any,
) -> None:
    metadata = trace.get("metadata")
    if not isinstance(metadata, dict):
        return
    for key in ("price_source_trace", "metric_source_trace", "dividend_source_trace"):
        nested_trace = metadata.get(key)
        if not isinstance(nested_trace, dict):
            continue
        promoted_trace = dict(nested_trace)
        _enrich_kr_child_trace(
            promoted_trace,
            cache_path=cache_path,
            loaded_at=loaded_at,
            warehouse_view=KR_NORMALIZED_FACTS_VIEW,
        )
        trace.setdefault(key, promoted_trace)


def _enrich_kr_child_trace(
    trace: dict[str, Any],
    *,
    cache_path: Any,
    loaded_at: Any,
    warehouse_view: str,
) -> None:
    trace["data_backend"] = "kr_valuation_warehouse"
    trace["warehouse_view"] = warehouse_view
    if cache_path:
        trace["cache_path"] = str(cache_path)
    if loaded_at:
        trace["loaded_at"] = str(loaded_at)
    if validate_source_trace(trace).status == "passed":
        trace["quality_flags"] = sorted(
            set([*trace.get("quality_flags", []), "source_trace_passed"])
        )
