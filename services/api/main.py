from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend.normalize.api import router as adjusted_router
from backend.normalize.schemas import NormalizationPolicy
from packages.core.env import load_local_env
from packages.core.universe import (
    KR_TOP_MARKET_CAP_PRIORITY_TICKERS,
    SUPPORTED_PRIORITY_MARKETS,
    all_top_market_cap_priority_universes,
    kr_top_market_cap_priority_universe,
    top_market_cap_priority_universe,
    top_market_cap_rank_coverage_meta,
)
from packages.valuation.analyst_scorecard import build_analyst_scorecard
from packages.valuation.chart import render_valuation_png, render_valuation_svg
from packages.valuation.engine import ValuationPoint, build_valuation_map
from packages.valuation.exports import (
    audit_row_with_trace_sections,
    audit_rows_to_csv,
    build_research_export_bundle,
    export_filename,
    research_bundle_to_json,
    research_report_to_markdown,
)
from packages.valuation.fiscal_fitness import build_fiscal_fitness_rows
from packages.valuation.forecast import (
    ForecastAssumption,
    ForecastSource,
    build_calculation_lines,
    build_forecast,
    build_manual_forecast,
)
from packages.valuation.fun_graphs import build_fun_graphs
from packages.valuation.health_check import build_health_check_score
from packages.valuation.performance import build_performance_table
from packages.valuation.portfolio import build_portfolio_summary, parse_transactions_csv
from packages.valuation.research_report import build_research_report
from packages.valuation.screener import (
    ScreenerConfig,
    apply_screener_filters,
    screener_filter_descriptions,
)
from packages.valuation.use_of_cash import build_use_of_cash_rows
from services.api.auth import ApiAuthMiddleware, api_auth_required, request_owner_key
from services.api.chart_cache import render_cached_valuation_chart
from services.api.chart_layouts import delete_chart_layout, list_chart_layouts, save_chart_layout
from services.api.chart_runs import create_chart_run, load_chart_run
from services.api.database import fixture_fallback_allowed, postgres_enabled
from services.api.kr_cache_provider import (
    kr_valuation_cache_universe_coverage,
    valuation_points_from_kr_cache,
)
from services.api.kr_warehouse_provider import (
    financials_from_kr_warehouse,
    normalized_facts_from_kr_warehouse,
    source_coverage_rows_from_kr_warehouse,
    valuation_points_from_kr_warehouse,
)
from services.api.local_consensus_provider import (
    local_consensus_projection_from_csv,
    local_forecast_evidence_from_csv,
    overlay_local_consensus_counts,
)
from services.api.postgres_provider import (
    DEFAULT_POLICY_KEY,
    add_watchlist_item_to_postgres,
    adjusted_series_from_postgres,
    company_snapshot_from_postgres,
    consensus_projection_from_postgres,
    financial_facts_from_postgres,
    financials_from_postgres,
    forecast_evidence_from_postgres,
    industry_series_from_postgres,
    macro_series_from_postgres,
    portfolio_from_postgres,
    price_points_from_postgres,
    recession_periods_from_postgres,
    remove_watchlist_item_from_postgres,
    research_metadata_from_postgres,
    screener_rows_from_postgres,
    search_securities_from_postgres,
    source_coverage_from_postgres,
    source_readiness_from_postgres,
    store_portfolio_csv_to_postgres,
    top_market_cap_universe_from_postgres,
    use_of_cash_inputs_from_postgres,
    valuation_points_from_postgres,
    watchlist_from_postgres,
)
from services.api.research_contracts import router as research_contracts_router
from services.api.sample_data import (
    FORECAST_PRESETS,
    PORTFOLIO_FIXTURE_CSV,
    SAMPLE_SECURITY_META,
    financials_for,
    forecast_evidence_for,
    price_dividend_for,
    price_points_for,
    recession_bands_for,
    sample_normalization_result,
    screener_rows,
    selected_valuation_metric,
    snapshot_for,
    source_trace_for,
)
from services.api.security import (
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    cors_allow_credentials,
    cors_allow_origins,
    cors_is_restricted,
    rate_limit_enabled,
)
from services.api.source_coverage import (
    build_source_coverage_report,
    normalize_coverage_tickers,
)
from services.api.source_documents import resolve_source_document

load_local_env()

app = FastAPI(title="LUXON Investment Terminal API", version="0.1.0")
app.add_middleware(ApiAuthMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allow_origins(),
    allow_credentials=cors_allow_credentials(),
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SecurityHeadersMiddleware)
app.include_router(adjusted_router)
app.include_router(research_contracts_router)

SOURCE_BACKED_VALUATION_BACKENDS = {
    "postgres",
    "kr_valuation_input_cache",
    "kr_valuation_warehouse",
}
KR_SOURCE_BACKED_VALUATION_BACKENDS = {
    "kr_valuation_input_cache",
    "kr_valuation_warehouse",
}


class PortfolioImportRequest(BaseModel):
    csv_text: str
    persist: bool = True
    replace_existing: bool = True


class WatchlistItemRequest(BaseModel):
    ticker: str
    note: str | None = None
    name: str = "Default"
    persist: bool = True


class ValuationChartRunRequest(BaseModel):
    company_id: str
    metric: str = "adjusted_operating"
    forecast_mode: str = "custom"
    forecast_case: str = "median"
    forecast_years: int = 5
    start_year: int | None = None
    end_year: int | None = None
    normal_multiple_years: int | None = None
    user_growth_rate: Decimal | None = None
    target_multiple: Decimal | None = None
    show_price: bool = True
    show_metric_area: bool = True
    show_fair_value: bool = True
    show_normal_multiple: bool = True
    show_current_valuation: bool = True
    show_custom_valuation: bool = False
    custom_valuation_multiple: Decimal | None = None
    show_dividend_floor: bool = True
    show_payout_ratio: bool = True
    show_dividend_yield: bool = False
    show_recession_bands: bool = True
    show_forecast: bool = True
    show_scenario_lines: bool = True
    hidden_scenario_lines: list[str] = Field(default_factory=list)
    manual_eps_values: str | None = None


class ChartLayoutRequest(BaseModel):
    name: str = "Default layout"
    owner_key: str = "default"
    company_id: str
    metric: str = "adjusted_operating"
    forecast_mode: str = "custom"
    forecast_case: str = "median"
    forecast_years: int = 5
    start_year: int | None = None
    end_year: int | None = None
    normal_multiple_years: int | None = None
    user_growth_rate: Decimal | None = None
    target_multiple: Decimal | None = None
    manual_eps_values: str | None = None
    visibility: dict[str, bool] = Field(default_factory=dict)
    hidden_scenario_lines: list[str] = Field(default_factory=list)


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "deployment": "vercel-first",
        "data_backend": "postgres" if postgres_enabled() else "fixture",
        "data_mode": "source_backed" if postgres_enabled() else "fixture_non_production",
    }


@app.get("/api/source-documents/resolve")
def source_document_resolution(
    source_document_id: str,
    include_preview: bool = True,
) -> dict:
    return {
        "data": resolve_source_document(
            source_document_id,
            include_preview=include_preview,
        )
    }


@app.get("/api/v1/system/readiness")
def system_readiness() -> dict:
    postgres_configured = postgres_enabled()
    postgres_status = source_readiness_from_postgres()
    postgres_reachable = bool(postgres_status and postgres_status["reachable"])
    source_counts = postgres_status["counts"] if postgres_status else {}
    source_backed_ready = postgres_reachable and all(
        source_counts.get(name, 0) > 0 for name in ("securities", "adjusted_earnings", "price_bars")
    )
    checks = [
        _readiness_check(
            "postgres_configured",
            postgres_configured,
            True,
            "DATA_BACKEND=postgres and DATABASE_URL are required for source-backed reads",
        ),
        _readiness_check(
            "postgres_reachable",
            postgres_reachable,
            postgres_configured,
            "Postgres SELECT count checks",
        ),
        _readiness_check(
            "source_backed_core_rows",
            source_backed_ready,
            postgres_configured,
            "requires securities, adjusted_earnings, and price_bars rows",
        ),
        _readiness_check(
            "api_auth_required",
            api_auth_required(),
            False,
            "private deployment should set API_AUTH_REQUIRED=true",
        ),
        _readiness_check(
            "api_cors_restricted",
            cors_is_restricted(),
            False,
            "private deployment should set API_CORS_ORIGINS to explicit origins",
        ),
        _readiness_check(
            "api_rate_limit_enabled",
            rate_limit_enabled(),
            False,
            "private deployment should set API_RATE_LIMIT_ENABLED=true",
        ),
        _readiness_check(
            "sec_user_agent_present",
            bool(os.getenv("SEC_USER_AGENT")),
            False,
            "required for SEC ingestion worker",
        ),
        _readiness_check(
            "fred_api_key_present",
            bool(os.getenv("FRED_API_KEY")),
            False,
            "required for FRED macro, rates, FX, and recession-band ingestion worker",
        ),
        _readiness_check(
            "blob_token_present",
            bool(os.getenv("BLOB_READ_WRITE_TOKEN")),
            False,
            "required for Vercel Blob sync",
        ),
        _readiness_check(
            "fixture_fallback_allowed",
            fixture_fallback_allowed(),
            False,
            "must be false for source-backed production unless explicitly allowed",
        ),
    ]
    status = (
        "source_backed_ready"
        if source_backed_ready
        else "source_configured_empty"
        if postgres_reachable
        else "fixture_only"
    )
    return {
        "status": status,
        "data_backend": "postgres" if postgres_configured else "fixture",
        "data_mode": "source_backed" if source_backed_ready else "fixture_non_production",
        "checks": checks,
        "postgres": postgres_status
        or {"reachable": False, "counts": {}, "error": "not_configured"},
    }


@app.get("/api/v1/system/source-coverage")
def system_source_coverage(
    tickers: str | None = None,
    market: str = "KR",
    min_historical_years: int = 3,
    min_forecast_years: int = 5,
    require_consensus_forecast: bool = False,
) -> dict:
    if min_historical_years < 1 or min_forecast_years < 1:
        raise HTTPException(
            status_code=400,
            detail="min_historical_years and min_forecast_years must be positive",
        )
    try:
        expected_tickers = normalize_coverage_tickers(tickers, market=market)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    coverage = source_coverage_from_postgres(
        expected_tickers,
        min_historical_years=min_historical_years,
        min_forecast_years=min_forecast_years,
        require_consensus_forecast=require_consensus_forecast,
    )
    data_backend = "postgres" if coverage is not None and postgres_enabled() else "fixture"
    if coverage is None:
        local_rows = source_coverage_rows_from_kr_warehouse(expected_tickers) or []
        if local_rows:
            local_rows = overlay_local_consensus_counts(
                local_rows,
                expected_tickers,
                min_forecast_years=min_forecast_years,
            )
            data_backend = "kr_valuation_warehouse"
        coverage = build_source_coverage_report(
            local_rows,
            expected_tickers,
            min_historical_years=min_historical_years,
            min_forecast_years=min_forecast_years,
            require_consensus_forecast=require_consensus_forecast,
            postgres_reachable=False,
            error="not_configured_local_warehouse" if local_rows else "not_configured",
        )
        if local_rows:
            coverage["data_mode"] = "local_source_backed_warehouse"
            coverage["local_overlays"] = {
                "forecast_csv": "enabled",
                "production_db_pending": True,
            }
    return coverage | {"data_backend": data_backend}


@app.get("/api/v1/system/priority-universe")
def system_priority_universe(market: str = "KR") -> dict:
    normalized_market = market.strip().upper()
    if normalized_market == "ALL":
        fallback = all_top_market_cap_priority_universes()
        universes = [
            top_market_cap_universe_from_postgres(item["market"]) or item
            for item in fallback["universes"]
        ]
        data_mode = (
            "source_backed"
            if all(item.get("data_mode") == "source_backed" for item in universes)
            else "source_backed_required"
        )
        rank_count = sum(_priority_rank_count(item) for item in universes)
        rank_limit = sum(_priority_rank_limit(item) for item in universes)
        rank_coverage = top_market_cap_rank_coverage_meta(rank_count, rank_limit)
        if rank_count == 0:
            rank_coverage["rank_coverage_status"] = "coverage_contract_only"
            rank_coverage["quality_status"] = "coverage_contract_not_financial_data"
            rank_coverage["quality_flags"] = [
                "not_live_market_cap_rank",
                "requires_source_backed_rank_recompute",
            ]
        source_trace = dict(fallback.get("source_trace") or {})
        source_trace.update(
            {
                "rank_coverage_status": rank_coverage["rank_coverage_status"],
                "rank_count": rank_coverage["rank_count"],
                "rank_limit": rank_coverage["rank_limit"],
                "missing_rank_slots": rank_coverage["missing_rank_slots"],
                "quality_status": rank_coverage["quality_status"],
                "quality_flags": rank_coverage["quality_flags"],
            }
        )
        return fallback | {
            "data_mode": data_mode,
            "rank_coverage_status": rank_coverage["rank_coverage_status"],
            "rank_count": rank_coverage["rank_count"],
            "rank_limit": rank_coverage["rank_limit"],
            "missing_rank_slots": rank_coverage["missing_rank_slots"],
            "source_trace": source_trace,
            "universes": universes,
        }
    if normalized_market not in SUPPORTED_PRIORITY_MARKETS:
        raise HTTPException(
            status_code=400,
            detail=f"market must be one of {', '.join(SUPPORTED_PRIORITY_MARKETS)} or ALL",
        )
    return (
        top_market_cap_universe_from_postgres(normalized_market)
        or top_market_cap_priority_universe(normalized_market)
    )


@app.get("/api/v1/system/kr-valuation-cache-coverage")
def system_kr_valuation_cache_coverage(tickers: str | None = None) -> dict:
    expected_tickers = (
        [item.strip().upper() for item in tickers.split(",") if item.strip()]
        if tickers
        else list(KR_TOP_MARKET_CAP_PRIORITY_TICKERS)
    )
    if not expected_tickers:
        raise HTTPException(status_code=400, detail="tickers must include at least one KR ticker")
    return kr_valuation_cache_universe_coverage(expected_tickers)


def _priority_rank_count(universe: dict) -> int:
    raw_count = universe.get("rank_count")
    if isinstance(raw_count, int):
        return max(0, raw_count)
    return sum(
        1
        for row in universe.get("tickers", [])
        if isinstance(row, dict)
        and row.get("rank_policy") == "source_backed_latest_market_cap"
    )


def _priority_rank_limit(universe: dict) -> int:
    raw_limit = universe.get("rank_limit")
    if isinstance(raw_limit, int) and raw_limit > 0:
        return raw_limit
    tickers = universe.get("tickers", [])
    if isinstance(tickers, list) and tickers:
        return len(tickers)
    return 10


@app.get("/api/v1/macro-series")
def macro_series(
    source: str | None = None,
    series_id: str | None = None,
    frequency: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = 250,
) -> dict:
    if limit < 1 or limit > 1000:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 1000")
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date must be before end_date")
    rows = macro_series_from_postgres(
        source=source,
        series_id=series_id,
        frequency=frequency,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )
    filters = {
        "source": source,
        "series_id": series_id,
        "frequency": frequency,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "limit": limit,
    }
    if rows is None:
        return {
            "data": [],
            "meta": {
                "data_mode": "source_backed_required",
                "quality_status": "missing_source_backed_data",
                "filters": filters,
                "source_note": (
                    "macro_series requires DATA_BACKEND=postgres and source-backed "
                    "macro or official statistics ingestion"
                ),
            },
        }
    return {
        "data": rows,
        "meta": {
            "data_mode": "source_backed",
            "quality_status": "source_backed" if rows else "empty",
            "filters": filters,
            "row_count": len(rows),
            "source_note": "source-backed macro and official statistics observations",
        },
    }


@app.get("/api/v1/industry-series")
def industry_series(
    market: str | None = None,
    source: str | None = None,
    category: str | None = None,
    series_id: str | None = None,
    limit: int = 250,
) -> dict:
    if limit < 1 or limit > 1000:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 1000")
    rows = industry_series_from_postgres(
        market=market,
        source=source,
        category=category,
        series_id=series_id,
        limit=limit,
    )
    filters = {
        "market": market,
        "source": source,
        "category": category,
        "series_id": series_id,
        "limit": limit,
    }
    if rows is None:
        return {
            "data": [],
            "meta": {
                "data_mode": "source_backed_required",
                "quality_status": "missing_source_backed_data",
                "filters": filters,
                "source_note": (
                    "industry_series requires DATA_BACKEND=postgres and source-backed "
                    "official statistics ingestion"
                ),
            },
        }
    return {
        "data": rows,
        "meta": {
            "data_mode": "source_backed",
            "quality_status": "source_backed" if rows else "empty",
            "filters": filters,
            "row_count": len(rows),
            "source_note": "source-backed official statistics industry observations",
        },
    }


def _readiness_check(name: str, ok: bool, required: bool, detail: str) -> dict:
    return {
        "name": name,
        "ok": ok,
        "required": required,
        "detail": detail,
    }


def _require_fixture_fallback(surface: str) -> None:
    if fixture_fallback_allowed():
        return
    raise HTTPException(
        status_code=503,
        detail={
            "code": "source_data_required",
            "surface": surface,
            "message": (
                "Fixture fallback is disabled. Run source-backed ingestion or set "
                "ALLOW_FIXTURE_FALLBACK=true for non-production preview use."
            ),
        },
    )


def _kr_priority_requires_source_backed(ticker: str) -> bool:
    if ticker.upper() not in KR_TOP_MARKET_CAP_PRIORITY_TICKERS:
        return False
    if _env_truthy("REQUIRE_SOURCE_BACKED_KR_PRIORITY"):
        return True
    return os.getenv("VERCEL_ENV", "").lower() == "production"


def _kr_priority_source_required_payload(
    ticker: str,
    surface: str,
    *,
    data: list | dict | None = None,
    extra_meta: dict | None = None,
) -> dict:
    universe = kr_top_market_cap_priority_universe()
    contract_row = next(
        row for row in universe["tickers"] if row["ticker"] == ticker.upper()
    )
    trace = dict(contract_row["source_trace"])
    trace["method"] = "source_backed_required_gate"
    trace["formula"] = (
        f"{surface} for KR priority tickers requires source-backed DB rows; "
        "fixture financial values are blocked for production-facing KR priority coverage"
    )
    trace["quality_status"] = "missing_source_backed_data"
    trace["quality_flags"] = sorted(
        {
            *trace.get("quality_flags", []),
            "source_backed_rows_not_loaded",
            "fixture_fallback_blocked_for_kr_priority",
        }
    )
    trace["financial_numbers_allowed"] = False
    meta = {
        "ticker": ticker.upper(),
        "surface": surface,
        "data_mode": "source_backed_required",
        "data_backend": "postgres_required",
        "quality_status": "missing_source_backed_data",
        "financial_numbers_allowed": False,
        "source_note": (
            "KR priority coverage is product-priority metadata only until "
            "OpenDART/pykrx/source-backed rows are loaded."
        ),
        "source_trace": trace,
    }
    if extra_meta:
        meta.update(extra_meta)
    return {"data": [] if data is None else data, "meta": meta}


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").lower() in {"1", "true", "yes", "on"}


@app.get("/api/v1/securities/search")
def search_securities(q: str = "") -> dict:
    db_rows = search_securities_from_postgres(q)
    if db_rows is not None:
        return {"data": db_rows, "meta": {"total": len(db_rows), "source": "postgres"}}
    _require_fixture_fallback("securities_search")
    query = q.upper()
    rows = [
        {
            "ticker": ticker,
            "name": meta["name"],
            "market": meta["market"],
            "country": meta["country"],
            "currency": meta["currency"],
        }
        for ticker, meta in SAMPLE_SECURITY_META.items()
        if not query or query in ticker or query in meta["name"].upper()
    ]
    return {"data": rows, "meta": {"total": len(rows), "source": "fixture_non_production"}}


@app.get("/api/securities/search")
def search_securities_compat(q: str = "") -> dict:
    return search_securities(q)


@app.get("/api/v1/companies/{company_id}/snapshot")
def company_snapshot(company_id: str) -> dict:
    ticker = company_id.upper()
    db_payload = company_snapshot_from_postgres(ticker)
    if db_payload is not None:
        return {"data": db_payload}
    if _kr_priority_requires_source_backed(ticker):
        return _kr_priority_source_required_payload(
            ticker,
            "company_snapshot",
            data={
                "ticker": ticker,
                "name": next(
                    row["name"]
                    for row in kr_top_market_cap_priority_universe()["tickers"]
                    if row["ticker"] == ticker
                ),
                "market": "KR",
                "country": "KR",
                "currency": "KRW",
                "source_note": "source_backed_required",
            },
        )
    if ticker not in SAMPLE_SECURITY_META:
        raise HTTPException(status_code=404, detail="company not found")
    _require_fixture_fallback("company_snapshot")
    return {"data": snapshot_for(ticker)}


@app.get("/api/company/{company_id}/snapshot")
def company_snapshot_compat(company_id: str) -> dict:
    return company_snapshot(company_id)


@app.get("/api/v1/companies/{company_id}/financials")
def company_financials(company_id: str) -> dict:
    ticker = company_id.upper()
    db_payload = financials_from_postgres(ticker)
    if db_payload is not None:
        return {
            "data": db_payload,
            "meta": {"ticker": ticker, "data_mode": "source_backed"},
        }
    kr_warehouse_payload = (
        financials_from_kr_warehouse(ticker)
        if ticker in KR_TOP_MARKET_CAP_PRIORITY_TICKERS
        else None
    )
    if kr_warehouse_payload is not None:
        return {
            "data": kr_warehouse_payload,
            "meta": {
                "ticker": ticker,
                "data_mode": "source_backed",
                "data_backend": "kr_valuation_warehouse",
                "source": "kr_valuation_warehouse",
                "financial_numbers_allowed": True,
            },
        }
    if _kr_priority_requires_source_backed(ticker):
        return _kr_priority_source_required_payload(ticker, "company_financials")
    if ticker not in SAMPLE_SECURITY_META:
        raise HTTPException(status_code=404, detail="company not found")
    _require_fixture_fallback("company_financials")
    return {
        "data": financials_for(ticker),
        "meta": {"ticker": ticker, "data_mode": "fixture_non_production"},
    }


@app.get("/api/company/{company_id}/financials")
def company_financials_compat(company_id: str) -> dict:
    return company_financials(company_id)


@app.get("/api/v1/companies/{company_id}/fun-graphs")
def company_fun_graphs(company_id: str) -> dict:
    ticker = company_id.upper()
    db_financials = financials_from_postgres(ticker)
    if db_financials is not None:
        rows = build_fun_graphs(
            ticker,
            db_financials,
            currency=_fiscal_fitness_currency(ticker),
            data_mode="source_backed",
        )
        return {
            "data": _json_safe_object(rows),
            "meta": {
                "ticker": ticker,
                "data_mode": "source_backed",
                "source": "postgres",
                "scope": _fun_graphs_scope(),
            },
        }
    if ticker not in SAMPLE_SECURITY_META:
        raise HTTPException(status_code=404, detail="company not found")
    _require_fixture_fallback("fun_graphs")
    rows = build_fun_graphs(
        ticker,
        financials_for(ticker),
        currency=SAMPLE_SECURITY_META[ticker]["currency"],
        data_mode="fixture_non_production",
    )
    return {
        "data": _json_safe_object(rows),
        "meta": {
            "ticker": ticker,
            "data_mode": "fixture_non_production",
            "source": "fixture",
            "scope": _fun_graphs_scope(),
        },
    }


@app.get("/api/company/{company_id}/fun-graphs")
def company_fun_graphs_compat(company_id: str) -> dict:
    return company_fun_graphs(company_id)


@app.get("/api/v1/companies/{company_id}/fiscal-fitness")
def company_fiscal_fitness(company_id: str) -> dict:
    ticker = company_id.upper()
    db_financials = financials_from_postgres(ticker)
    if db_financials is not None:
        rows = build_fiscal_fitness_rows(
            ticker,
            db_financials,
            currency=_fiscal_fitness_currency(ticker),
            data_mode="source_backed",
        )
        return {
            "data": _json_safe_object(rows),
            "meta": {
                "ticker": ticker,
                "data_mode": "source_backed",
                "source": "postgres",
                "scope": _fiscal_fitness_scope(),
                "summary": _fiscal_fitness_summary(rows),
            },
        }
    if ticker not in SAMPLE_SECURITY_META:
        raise HTTPException(status_code=404, detail="company not found")
    _require_fixture_fallback("fiscal_fitness")
    rows = build_fiscal_fitness_rows(
        ticker,
        financials_for(ticker),
        currency=SAMPLE_SECURITY_META[ticker]["currency"],
        data_mode="fixture_non_production",
    )
    return {
        "data": _json_safe_object(rows),
        "meta": {
            "ticker": ticker,
            "data_mode": "fixture_non_production",
            "source": "fixture",
            "scope": _fiscal_fitness_scope(),
            "summary": _fiscal_fitness_summary(rows),
        },
    }


@app.get("/api/company/{company_id}/fiscal-fitness")
def company_fiscal_fitness_compat(company_id: str) -> dict:
    return company_fiscal_fitness(company_id)


@app.get("/api/v1/companies/{company_id}/health-check")
def company_health_check(company_id: str) -> dict:
    ticker = company_id.upper()
    db_financials = financials_from_postgres(ticker)
    db_forecast = forecast_evidence_from_postgres(ticker)
    if db_financials is not None:
        currency = _fiscal_fitness_currency(ticker)
        fiscal_rows = build_fiscal_fitness_rows(
            ticker,
            db_financials,
            currency=currency,
            data_mode="source_backed",
        )
        score = build_health_check_score(
            ticker,
            fiscal_rows,
            currency=currency,
            data_mode="source_backed",
            forecast_evidence=db_forecast,
        )
        return {
            "data": _json_safe_object(score),
            "meta": {
                "ticker": ticker,
                "data_mode": "source_backed",
                "source": "postgres",
                "scope": _health_check_scope(),
            },
        }
    if ticker not in SAMPLE_SECURITY_META:
        raise HTTPException(status_code=404, detail="company not found")
    _require_fixture_fallback("health_check")
    currency = SAMPLE_SECURITY_META[ticker]["currency"]
    fiscal_rows = build_fiscal_fitness_rows(
        ticker,
        financials_for(ticker),
        currency=currency,
        data_mode="fixture_non_production",
    )
    score = build_health_check_score(
        ticker,
        fiscal_rows,
        currency=currency,
        data_mode="fixture_non_production",
        forecast_evidence=forecast_evidence_for(ticker),
    )
    return {
        "data": _json_safe_object(score),
        "meta": {
            "ticker": ticker,
            "data_mode": "fixture_non_production",
            "source": "fixture",
            "scope": _health_check_scope(),
        },
    }


@app.get("/api/company/{company_id}/health-check")
def company_health_check_compat(company_id: str) -> dict:
    return company_health_check(company_id)


@app.get("/api/v1/companies/{company_id}/research-report")
def company_research_report(
    company_id: str,
    metric: str = "adjusted_operating",
    forecast_mode: str = "custom",
    forecast_case: str = "median",
    forecast_years: int = 5,
    start_year: int | None = None,
    end_year: int | None = None,
    normal_multiple_years: int | None = None,
    user_growth_rate: Decimal | None = None,
    target_multiple: Decimal | None = None,
    manual_eps_values: str | None = None,
) -> dict:
    ticker = company_id.upper()
    valuation_kwargs = _valuation_context_kwargs(
        metric=metric,
        forecast_mode=forecast_mode,
        forecast_case=forecast_case,
        forecast_years=forecast_years,
        start_year=start_year,
        end_year=end_year,
        normal_multiple_years=normal_multiple_years,
        user_growth_rate=user_growth_rate,
        target_multiple=target_multiple,
        manual_eps_values=manual_eps_values,
    )
    db_financials = financials_from_postgres(ticker)
    if db_financials is not None:
        currency = _fiscal_fitness_currency(ticker)
        fiscal_rows = build_fiscal_fitness_rows(
            ticker,
            db_financials,
            currency=currency,
            data_mode="source_backed",
        )
        forecast_evidence = forecast_evidence_from_postgres(ticker)
        health_check = build_health_check_score(
            ticker,
            fiscal_rows,
            currency=currency,
            data_mode="source_backed",
            forecast_evidence=forecast_evidence,
        )
        valuation_payload = _source_backed_valuation_payload(ticker, **valuation_kwargs)
        valuation_rows = valuation_payload["data"] if valuation_payload is not None else []
        use_of_cash_rows = _source_backed_use_of_cash_rows(ticker)
        report = build_research_report(
            ticker,
            snapshot=company_snapshot_from_postgres(ticker),
            valuation_rows=valuation_rows,
            financial_rows=db_financials,
            fiscal_fitness_rows=fiscal_rows,
            health_check=health_check,
            forecast_evidence=forecast_evidence,
            use_of_cash_rows=use_of_cash_rows,
            currency=currency,
            data_mode="source_backed",
        )
        missing_scopes = _research_report_missing_scopes(report)
        return {
            "data": _json_safe_object(report),
            "meta": {
                "ticker": ticker,
                "data_mode": "source_backed",
                "source": "postgres",
                "scope": _research_report_scope(),
                "partial": bool(missing_scopes),
                "missing_scopes": missing_scopes,
                "quality_status": report["quality_status"],
            },
        }
    if ticker not in SAMPLE_SECURITY_META:
        raise HTTPException(status_code=404, detail="company not found")
    _require_fixture_fallback("research_report")
    currency = SAMPLE_SECURITY_META[ticker]["currency"]
    valuation_payload = valuation_map(ticker, **valuation_kwargs)
    fiscal_rows = build_fiscal_fitness_rows(
        ticker,
        financials_for(ticker),
        currency=currency,
        data_mode="fixture_non_production",
    )
    forecast_evidence = forecast_evidence_for(ticker)
    health_check = build_health_check_score(
        ticker,
        fiscal_rows,
        currency=currency,
        data_mode="fixture_non_production",
        forecast_evidence=forecast_evidence,
    )
    use_of_cash_rows = build_use_of_cash_rows(
        ticker,
        financials_for(ticker),
        _fixture_valuation_rows(ticker),
        currency=currency,
        data_mode="fixture_non_production",
    )
    report = build_research_report(
        ticker,
        snapshot=snapshot_for(ticker),
        valuation_rows=valuation_payload["data"],
        financial_rows=financials_for(ticker),
        fiscal_fitness_rows=fiscal_rows,
        health_check=health_check,
        forecast_evidence=forecast_evidence,
        use_of_cash_rows=use_of_cash_rows,
        currency=currency,
        data_mode="fixture_non_production",
    )
    return {
        "data": _json_safe_object(report),
        "meta": {
            "ticker": ticker,
            "data_mode": "fixture_non_production",
            "source": "fixture",
            "scope": _research_report_scope(),
            "partial": False,
            "missing_scopes": [],
            "quality_status": report["quality_status"],
        },
    }


@app.get("/api/company/{company_id}/research-report")
def company_research_report_compat(company_id: str) -> dict:
    return company_research_report(company_id)


@app.get("/api/v1/companies/{company_id}/research-metadata")
def company_research_metadata(company_id: str, limit: int = 25) -> dict:
    ticker = company_id.upper()
    payload = research_metadata_from_postgres(ticker, limit=limit)
    if payload is None:
        payload = _empty_research_metadata_payload(ticker)
        data_mode = "source_backed_required"
        source = "postgres"
    else:
        data_mode = payload["data_mode"]
        source = "postgres"
    return {
        "data": _json_safe_object(payload),
        "meta": {
            "ticker": ticker,
            "data_mode": data_mode,
            "source": source,
            "scope": [
                "raw_objects",
                "source_documents",
                "research_link_metadata",
            ],
            "quality_status": payload["quality_status"],
            "financial_numbers_allowed": False,
            "policy": "metadata_only_no_financial_numbers",
        },
    }


@app.get("/api/company/{company_id}/research-metadata")
def company_research_metadata_compat(company_id: str) -> dict:
    return company_research_metadata(company_id)


@app.get("/api/v1/companies/{company_id}/exports/research-report.md")
def company_research_report_markdown_export(
    company_id: str,
    metric: str = "adjusted_operating",
    forecast_mode: str = "custom",
    forecast_case: str = "median",
    forecast_years: int = 5,
    start_year: int | None = None,
    end_year: int | None = None,
    normal_multiple_years: int | None = None,
    user_growth_rate: Decimal | None = None,
    target_multiple: Decimal | None = None,
    manual_eps_values: str | None = None,
) -> Response:
    ticker = company_id.upper()
    bundle = _company_research_export_bundle(
        ticker,
        metric=metric,
        forecast_mode=forecast_mode,
        forecast_case=forecast_case,
        forecast_years=forecast_years,
        start_year=start_year,
        end_year=end_year,
        normal_multiple_years=normal_multiple_years,
        user_growth_rate=user_growth_rate,
        target_multiple=target_multiple,
        manual_eps_values=manual_eps_values,
    )
    content = research_report_to_markdown(bundle)
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename={export_filename(ticker, 'research-report.md')}"
            ),
            "X-Export-Version": bundle["manifest"]["export_version"],
            "X-Data-Mode": str(bundle["report"].get("data_mode") or "unknown"),
        },
    )


@app.get("/api/v1/companies/{company_id}/exports/research-report.json")
def company_research_report_json_export(
    company_id: str,
    metric: str = "adjusted_operating",
    forecast_mode: str = "custom",
    forecast_case: str = "median",
    forecast_years: int = 5,
    start_year: int | None = None,
    end_year: int | None = None,
    normal_multiple_years: int | None = None,
    user_growth_rate: Decimal | None = None,
    target_multiple: Decimal | None = None,
    manual_eps_values: str | None = None,
) -> Response:
    ticker = company_id.upper()
    bundle = _company_research_export_bundle(
        ticker,
        metric=metric,
        forecast_mode=forecast_mode,
        forecast_case=forecast_case,
        forecast_years=forecast_years,
        start_year=start_year,
        end_year=end_year,
        normal_multiple_years=normal_multiple_years,
        user_growth_rate=user_growth_rate,
        target_multiple=target_multiple,
        manual_eps_values=manual_eps_values,
    )
    return Response(
        content=research_bundle_to_json(bundle),
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename={export_filename(ticker, 'research-report.json')}"
            ),
            "X-Export-Version": bundle["manifest"]["export_version"],
            "X-Data-Mode": str(bundle["report"].get("data_mode") or "unknown"),
        },
    )


@app.get("/api/v1/companies/{company_id}/exports/data-audit.csv")
def company_data_audit_csv_export(
    company_id: str,
    metric: str = "adjusted_operating",
    forecast_mode: str = "custom",
    forecast_case: str = "median",
    forecast_years: int = 5,
    start_year: int | None = None,
    end_year: int | None = None,
    normal_multiple_years: int | None = None,
    user_growth_rate: Decimal | None = None,
    target_multiple: Decimal | None = None,
    manual_eps_values: str | None = None,
) -> Response:
    ticker = company_id.upper()
    rows = data_audit(
        ticker,
        metric=metric,
        forecast_mode=forecast_mode,
        forecast_case=forecast_case,
        forecast_years=forecast_years,
        start_year=start_year,
        end_year=end_year,
        normal_multiple_years=normal_multiple_years,
        user_growth_rate=user_growth_rate,
        target_multiple=target_multiple,
        manual_eps_values=manual_eps_values,
    )["data"]
    return Response(
        content=audit_rows_to_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename={export_filename(ticker, 'data-audit.csv')}"
            ),
            "X-Export-Version": "data_audit_csv_v1",
            "X-Data-Mode": "source_backed" if postgres_enabled() else "fixture_non_production",
        },
    )


def _company_research_export_bundle(ticker: str, **valuation_kwargs) -> dict:
    report_payload = company_research_report(ticker, **valuation_kwargs)
    audit_payload = data_audit(ticker, **valuation_kwargs)
    return build_research_export_bundle(
        ticker,
        report_payload["data"],
        audit_payload["data"],
    )


@app.get("/api/v1/companies/{company_id}/use-of-cash")
def company_use_of_cash(company_id: str) -> dict:
    ticker = company_id.upper()
    db_inputs = use_of_cash_inputs_from_postgres(ticker)
    if db_inputs is not None:
        db_financials, db_valuation_rows, db_currency = db_inputs
        rows = build_use_of_cash_rows(
            ticker,
            db_financials,
            db_valuation_rows,
            currency=db_currency,
            data_mode="source_backed",
        )
        return {
            "data": _json_safe_object(rows),
            "meta": {
                "ticker": ticker,
                "data_mode": "source_backed",
                "source": "postgres",
                "scope": _use_of_cash_scope(),
                "summary": _use_of_cash_summary(rows),
            },
        }
    if ticker not in SAMPLE_SECURITY_META:
        raise HTTPException(status_code=404, detail="company not found")
    _require_fixture_fallback("use_of_cash")
    rows = build_use_of_cash_rows(
        ticker,
        financials_for(ticker),
        _fixture_valuation_rows(ticker),
        currency=SAMPLE_SECURITY_META[ticker]["currency"],
        data_mode="fixture_non_production",
    )
    return {
        "data": _json_safe_object(rows),
        "meta": {
            "ticker": ticker,
            "data_mode": "fixture_non_production",
            "source": "fixture",
            "scope": _use_of_cash_scope(),
            "summary": _use_of_cash_summary(rows),
        },
    }


@app.get("/api/company/{company_id}/use-of-cash")
def company_use_of_cash_compat(company_id: str) -> dict:
    return company_use_of_cash(company_id)


@app.get("/api/v1/companies/{company_id}/valuation-map")
def valuation_map(
    company_id: str,
    metric: str = "adjusted_operating",
    forecast_mode: str = "custom",
    forecast_case: str = "median",
    forecast_years: int = 5,
    start_year: int | None = None,
    end_year: int | None = None,
    normal_multiple_years: int | None = None,
    user_growth_rate: Decimal | None = None,
    target_multiple: Decimal | None = None,
    show_price: bool = True,
    show_metric_area: bool = True,
    show_fair_value: bool = True,
    show_normal_multiple: bool = True,
    show_current_valuation: bool = True,
    show_custom_valuation: bool = False,
    custom_valuation_multiple: Decimal | None = None,
    show_dividend_floor: bool = True,
    show_payout_ratio: bool = True,
    show_dividend_yield: bool = False,
    show_recession_bands: bool = True,
    show_forecast: bool = True,
    show_scenario_lines: bool = True,
    hidden_scenario_lines: str | None = None,
    manual_eps_values: str | None = None,
) -> dict:
    ticker = company_id.upper()
    _validate_forecast_query_inputs(
        forecast_years=forecast_years,
        user_growth_rate=user_growth_rate,
        target_multiple=target_multiple,
        custom_valuation_multiple=custom_valuation_multiple,
        manual_eps_values=manual_eps_values,
    )
    points: list[ValuationPoint] = []
    metric_label = metric
    data_backend = "fixture"
    kr_source_price_points: list[dict] = []
    kr_cache_meta: dict | None = None
    kr_warehouse_meta: dict | None = None
    db_payload = valuation_points_from_postgres(ticker, metric)
    source_currency = SAMPLE_SECURITY_META.get(ticker, {}).get("currency", "USD")
    source_country = SAMPLE_SECURITY_META.get(ticker, {}).get("country", "US")
    if db_payload is not None:
        points, metric_label, db_meta = db_payload
        data_backend = db_meta["data_backend"]
        source_currency = db_meta.get("currency") or source_currency
        source_country = db_meta.get("country") or source_country
    else:
        kr_warehouse_payload = (
            valuation_points_from_kr_warehouse(ticker, metric)
            if ticker in KR_TOP_MARKET_CAP_PRIORITY_TICKERS
            else None
        )
        kr_cache_payload = (
            valuation_points_from_kr_cache(ticker, metric)
            if ticker in KR_TOP_MARKET_CAP_PRIORITY_TICKERS and kr_warehouse_payload is None
            else None
        )
        if kr_warehouse_payload is not None and kr_warehouse_payload.points:
            points = kr_warehouse_payload.points
            metric_label = kr_warehouse_payload.metric_label
            data_backend = kr_warehouse_payload.meta["data_backend"]
            source_currency = "KRW"
            source_country = "KR"
            kr_source_price_points = kr_warehouse_payload.price_points
            kr_warehouse_meta = kr_warehouse_payload.meta
        elif kr_cache_payload is not None and kr_cache_payload.points:
            points = kr_cache_payload.points
            metric_label = kr_cache_payload.metric_label
            data_backend = kr_cache_payload.meta["data_backend"]
            source_currency = "KRW"
            source_country = "KR"
            kr_source_price_points = kr_cache_payload.price_points
            kr_cache_meta = kr_cache_payload.meta
        elif _kr_priority_requires_source_backed(ticker):
            extra_meta = {
                "metric": metric,
                "metric_label": metric,
                "forecast": {
                    "years": forecast_years,
                    "mode": forecast_mode,
                    "case": forecast_case,
                    "source": "source_backed_required",
                },
                "line_visibility": {
                    "price": show_price,
                    "metric_area": show_metric_area,
                    "fair_value": show_fair_value,
                    "normal_multiple": show_normal_multiple,
                    "current_valuation": show_current_valuation,
                    "custom_valuation": show_custom_valuation,
                    "custom_valuation_multiple": (
                        str(custom_valuation_multiple)
                        if custom_valuation_multiple is not None
                        else None
                    ),
                    "dividend_floor": show_dividend_floor,
                    "payout_ratio": show_payout_ratio,
                    "dividend_yield": show_dividend_yield,
                    "recession_bands": show_recession_bands,
                    "forecast": show_forecast,
                    "scenario_lines": show_scenario_lines,
                    "hidden_scenario_lines": _parse_hidden_scenario_lines(
                        hidden_scenario_lines
                    ),
                },
            }
            if kr_cache_payload is not None:
                extra_meta.update(kr_cache_payload.meta)
                extra_meta["kr_cache"] = _json_safe_object(kr_cache_payload.meta)
                extra_meta["metric_label"] = kr_cache_payload.metric_label
                extra_meta["price_points"] = _json_safe_object(
                    kr_cache_payload.price_points
                )
            return _kr_priority_source_required_payload(
                ticker,
                "valuation_map",
                extra_meta=extra_meta,
            )
        else:
            _require_fixture_fallback("valuation_map")
            result = sample_normalization_result(ticker, NormalizationPolicy())
            if not result.series:
                raise HTTPException(status_code=404, detail="valuation data not found")
            for record in result.series:
                selected_metric, metric_trace, metric_label = selected_valuation_metric(
                    ticker,
                    record.fiscal_year,
                    metric,
                    record,
                )
                if selected_metric is None:
                    continue
                price, dividend = price_dividend_for(ticker, record.fiscal_year)
                metric_trace = {
                    **metric_trace,
                    "price_source_trace": source_trace_for(ticker, record.fiscal_year, "price"),
                    "dividend_source_trace": source_trace_for(
                        ticker,
                        record.fiscal_year,
                        "dividend_per_share",
                    ),
                }
                points.append(
                    ValuationPoint(
                        fiscal_year=record.fiscal_year,
                        metric=Decimal(str(selected_metric)),
                        price=price,
                        dividend=dividend,
                        source_trace=metric_trace,
                    )
                )
    if not points:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported or unavailable valuation metric: {metric}",
        )
    range_start, range_end = _year_range(start_year, end_year)
    if range_start is not None or range_end is not None:
        points = [
            point
            for point in points
            if (range_start is None or point.fiscal_year >= range_start)
            and (range_end is None or point.fiscal_year <= range_end)
        ]
    if not points:
        raise HTTPException(
            status_code=400,
            detail="valuation range has no available historical points",
        )
    normal_window = _normal_multiple_window(normal_multiple_years)
    historical = build_valuation_map(points, normal_multiple_years=normal_window)
    latest = historical[-1]
    clamped_years = min(max(forecast_years, 1), 5)
    normalized_forecast_mode = forecast_mode.lower().replace("-", "_")
    uses_consensus_projection = normalized_forecast_mode in {
        "estimates",
        "consensus",
        "normal_multiple",
        "lt_growth",
        "ai_review",
        "ai_assisted",
        "ai_assisted_review",
    }
    postgres_consensus_projection = (
        consensus_projection_from_postgres(
            ticker,
            forecast_case,
            latest.fiscal_year,
            clamped_years,
            latest.metric,
        )
        if data_backend == "postgres" and uses_consensus_projection
        else None
    )
    local_consensus_projection = (
        local_consensus_projection_from_csv(
            ticker,
            forecast_case,
            latest.fiscal_year,
            clamped_years,
            latest.metric,
        )
        if postgres_consensus_projection is None
        and data_backend in KR_SOURCE_BACKED_VALUATION_BACKENDS
        and uses_consensus_projection
        else None
    )
    consensus_projection = postgres_consensus_projection or local_consensus_projection
    forecast_config = _forecast_config(
        ticker,
        forecast_mode,
        forecast_case,
        historical,
        clamped_years,
        user_growth_rate,
        target_multiple,
        consensus_projection,
    )
    manual_metrics = _parse_manual_metrics(manual_eps_values, clamped_years)
    hidden_scenario_line_labels = _parse_hidden_scenario_lines(hidden_scenario_lines)
    forecast_assumption = ForecastAssumption(
        start_year=latest.fiscal_year,
        start_metric=latest.metric,
        start_price=latest.price,
        years=clamped_years,
        annual_growth_rate_pct=forecast_config["growth"],
        target_multiple=forecast_config["multiple"],
        annual_dividend=latest.dividend,
        source=forecast_config["source"],
        source_trace={
            **forecast_config.get("source_trace", {}),
            "source": (
                (forecast_config.get("source_trace") or {}).get("source")
                or forecast_config["source"].value
            ),
            "source_type": forecast_config["source"].value,
            "method": (
                (forecast_config.get("source_trace") or {}).get("method")
                or f"forecast_{forecast_config['mode']}"
            ),
            "formula": forecast_config["formula"],
            "forecast_mode": forecast_config["mode"],
            "forecast_case": forecast_config["case"],
            "analyst_count": forecast_config["analyst_count"],
            "consensus_quality_status": forecast_config["consensus"]["quality_status"],
            "manual_eps_values": [
                str(value) if value is not None else None for value in manual_metrics
            ],
        },
    )
    has_manual_metric = any(value is not None for value in manual_metrics)
    consensus_metrics = forecast_config.get("consensus_metric_values") or []
    has_consensus_metric = any(value is not None for value in consensus_metrics)
    if forecast_config["mode"] == "custom" and has_manual_metric:
        forecast = build_manual_forecast(forecast_assumption, manual_metrics)
    elif forecast_config["source"] == ForecastSource.CONSENSUS_SNAPSHOT and has_consensus_metric:
        forecast = build_manual_forecast(
            forecast_assumption,
            consensus_metrics,
            source=ForecastSource.CONSENSUS_SNAPSHOT,
        )
    else:
        forecast = build_forecast(forecast_assumption)
    calculation_lines = build_calculation_lines(forecast, forecast_config["multiple"])
    combined = [row.__dict__ for row in historical]
    missing_consensus_years = set(
        (forecast_config.get("source_trace") or {}).get("missing_consensus_years") or []
    )
    for item in forecast:
        year_trace = (forecast_config.get("source_traces_by_year") or {}).get(
            str(item.fiscal_year),
            {},
        )
        forecast_trace = {
            **(item.source_trace or {}),
            **year_trace,
        }
        assumption_source_document_id = forecast_trace.get("source_document_id")
        assumption_filing_id = forecast_trace.get("filing_id")
        assumption_period = forecast_trace.get("period")
        if not _missing_trace_value(assumption_source_document_id):
            forecast_trace.setdefault(
                "forecast_assumption_source_document_id",
                assumption_source_document_id,
            )
        if not _missing_trace_value(assumption_filing_id):
            forecast_trace.setdefault("forecast_assumption_filing_id", assumption_filing_id)
        if not _missing_trace_value(assumption_period):
            forecast_trace.setdefault("forecast_assumption_period", assumption_period)
        has_year_specific_trace = bool(year_trace)
        has_missing_consensus_fallback = (
            item.fiscal_year in missing_consensus_years and not year_trace
        )
        if has_missing_consensus_fallback:
            fallback_document_id = (
                f"{ticker.lower()}-{item.fiscal_year}-missing-consensus-fallback"
            )
            forecast_trace.update(
                {
                    "source_type": "consensus_gap_deterministic_fallback",
                    "source_document_id": fallback_document_id,
                    "filing_id": fallback_document_id,
                    "period": f"FY{item.fiscal_year}E",
                    "unit": "per_share",
                    "currency": source_currency,
                    "formula": (
                        "missing consensus EPS snapshot; forecast metric uses deterministic "
                        "growth fallback from the previous forecast metric"
                    ),
                    "quality_status": "missing_source_backed_consensus_snapshot",
                    "missing_consensus_year": item.fiscal_year,
                }
            )
        elif not has_year_specific_trace:
            row_document_id = f"{ticker.lower()}-{item.fiscal_year}-forecast-assumption"
            forecast_trace["source_document_id"] = row_document_id
            forecast_trace["filing_id"] = row_document_id
            forecast_trace["period"] = f"FY{item.fiscal_year}E"
        forecast_trace.setdefault(
            "source_document_id",
            f"{ticker.lower()}-{item.fiscal_year}-forecast-assumption",
        )
        forecast_trace.setdefault(
            "filing_id",
            f"{ticker.lower()}-{item.fiscal_year}-forecast-assumption",
        )
        forecast_trace.setdefault("source_type", forecast_config["source"].value)
        if _missing_trace_value(forecast_trace.get("source")):
            forecast_trace["source"] = (
                forecast_trace.get("source_type") or forecast_config["source"].value
            )
        if _missing_trace_value(forecast_trace.get("method")):
            forecast_trace["method"] = f"forecast_{forecast_config['mode']}"
        forecast_trace.setdefault("period", f"FY{item.fiscal_year}E")
        forecast_trace.setdefault(
            "available_at",
            (
                (forecast_config.get("source_trace") or {}).get("available_at")
                or (latest.source_trace or {}).get("available_at")
            ),
        )
        forecast_trace.setdefault("unit", "per_share")
        forecast_trace.setdefault("currency", source_currency)
        if _missing_trace_value(forecast_trace.get("formula")):
            forecast_trace["formula"] = forecast_config["formula"]
        forecast_trace.setdefault(
            "quality_status",
            (
                "source_backed_forecast_assumption"
                if data_backend in SOURCE_BACKED_VALUATION_BACKENDS
                else "fixture_non_production_forecast"
            ),
        )
        forecast_trace.setdefault("forecast_mode", forecast_config["mode"])
        forecast_trace.setdefault("forecast_case", forecast_config["case"])
        margin_of_safety_pct = _forecast_margin_of_safety_pct(
            latest.price,
            item.target_price,
        )
        combined.append(
            {
                "fiscal_year": item.fiscal_year,
                "metric": item.metric,
                "price": item.target_price,
                "dividend": item.dividend,
                "yoy": None,
                "normal_multiple": latest.normal_multiple,
                "fair_multiple": forecast_config["multiple"],
                "fair_value_price": item.target_price,
                "forecast_flag": True,
                "source_trace": forecast_trace,
                "forecast_source": item.source.value,
                "price_cagr_pct": item.price_cagr_pct,
                "total_return_cagr_pct": item.total_return_cagr_pct,
                "margin_of_safety_pct": margin_of_safety_pct,
            }
        )
    min_year = min(int(row["fiscal_year"]) for row in combined)
    max_year = max(int(row["fiscal_year"]) for row in combined)
    recession_bands = []
    if source_country == "US":
        db_recession_bands = recession_periods_from_postgres(
            start_year=min_year,
            end_year=max_year,
        )
        recession_bands = (
            db_recession_bands
            if db_recession_bands is not None
            else recession_bands_for(ticker, min_year, max_year)
        )
    if data_backend in KR_SOURCE_BACKED_VALUATION_BACKENDS:
        price_points = kr_source_price_points
    elif data_backend == "postgres":
        price_points = price_points_from_postgres(
            ticker,
            start_year=historical[0].fiscal_year,
            end_year=historical[-1].fiscal_year,
        )
    else:
        price_points = price_points_for(
            ticker,
            historical[0].fiscal_year,
            historical[-1].fiscal_year,
        )
    return {
        "data": _json_safe_series(combined),
        "meta": {
            "ticker": ticker,
            "metric": metric,
            "metric_label": metric_label,
            "range": {
                "requested_start_year": range_start,
                "requested_end_year": range_end,
                "start_year": historical[0].fiscal_year,
                "end_year": historical[-1].fiscal_year,
                "historical_points": len(historical),
            },
            "forecast": {
                "years": forecast_years,
                "mode": forecast_config["mode"],
                "case": forecast_config["case"],
                "growth_rate_pct": str(forecast_config["growth"]),
                "target_multiple": str(forecast_config["multiple"]),
                "analyst_count": forecast_config["analyst_count"],
                "source": forecast[-1].source.value if forecast else None,
                "formula": forecast_config["formula"],
                "consensus": forecast_config["consensus"],
                "source_trace": _json_safe_object(forecast_assumption.source_trace or {}),
                "manual_eps_values": [
                    str(value) if value is not None else None for value in manual_metrics
                ],
                "calculation_lines": _json_safe_object(
                    [
                        {
                            "multiple": line.multiple,
                            "label": line.label,
                            "points": [
                                {
                                    "fiscal_year": point.fiscal_year,
                                    "target_price": point.target_price,
                                }
                                for point in line.points
                            ],
                        }
                        for line in calculation_lines
                    ]
                ),
            },
            "normal_multiple": {
                "window_years": normal_window,
                "formula": (
                    "trimmed_mean(price / metric) over selected historical "
                    "fiscal-year window"
                ),
            },
            "price_points": _json_safe_object(price_points or []),
            "price_points_meta": {
                "frequency": (
                    "monthly"
                    if data_backend == "postgres"
                    else "annual_source_cache"
                    if data_backend == "kr_valuation_input_cache"
                    else "annual_warehouse"
                    if data_backend == "kr_valuation_warehouse"
                    else "annual_fixture"
                ),
                "data_mode": (
                    "source_backed"
                    if data_backend == "postgres"
                    else "source_backed"
                    if data_backend == "kr_valuation_warehouse"
                    else "source_backed_cache"
                    if data_backend == "kr_valuation_input_cache"
                    else "fixture_non_production"
                ),
                "source": (
                    "price_bars_month_end"
                    if data_backend == "postgres"
                    else "kr_valuation_input_cache_year_end_price"
                    if data_backend == "kr_valuation_input_cache"
                    else "kr_valuation_warehouse_year_end_price"
                    if data_backend == "kr_valuation_warehouse"
                    else "fixture_year_end_price"
                ),
                "historical_only": True,
            },
            "recession_bands": _json_safe_object(recession_bands),
            "line_visibility": {
                "price": show_price,
                "metric_area": show_metric_area,
                "fair_value": show_fair_value,
                "normal_multiple": show_normal_multiple,
                "current_valuation": show_current_valuation,
                "custom_valuation": show_custom_valuation,
                "custom_valuation_multiple": (
                    str(custom_valuation_multiple)
                    if custom_valuation_multiple is not None
                    else None
                ),
                "dividend_floor": show_dividend_floor,
                "payout_ratio": show_payout_ratio,
                "dividend_yield": show_dividend_yield,
                "recession_bands": show_recession_bands,
                "forecast": show_forecast,
                "scenario_lines": show_scenario_lines,
                "hidden_scenario_lines": hidden_scenario_line_labels,
            },
            "data_mode": (
                "source_backed"
                if data_backend == "postgres"
                else "source_backed"
                if data_backend == "kr_valuation_warehouse"
                else "source_backed_cache"
                if data_backend == "kr_valuation_input_cache"
                else "fixture_non_production"
            ),
            "data_backend": data_backend,
            "financial_numbers_allowed": data_backend in SOURCE_BACKED_VALUATION_BACKENDS,
            "kr_cache": _json_safe_object(kr_cache_meta) if kr_cache_meta else None,
            "kr_warehouse": (
                _json_safe_object(kr_warehouse_meta) if kr_warehouse_meta else None
            ),
        },
    }


@app.get("/api/company/{company_id}/valuation-map")
def valuation_map_compat(
    company_id: str,
    metric: str = "adjusted_operating",
    forecast_mode: str = "custom",
    forecast_case: str = "median",
    forecast_years: int = 5,
    start_year: int | None = None,
    end_year: int | None = None,
    normal_multiple_years: int | None = None,
    user_growth_rate: Decimal | None = None,
    target_multiple: Decimal | None = None,
    show_price: bool = True,
    show_metric_area: bool = True,
    show_fair_value: bool = True,
    show_normal_multiple: bool = True,
    show_current_valuation: bool = True,
    show_custom_valuation: bool = False,
    custom_valuation_multiple: Decimal | None = None,
    show_dividend_floor: bool = True,
    show_payout_ratio: bool = True,
    show_dividend_yield: bool = False,
    show_recession_bands: bool = True,
    show_forecast: bool = True,
    show_scenario_lines: bool = True,
    hidden_scenario_lines: str | None = None,
    manual_eps_values: str | None = None,
) -> dict:
    return valuation_map(
        company_id,
        metric,
        forecast_mode,
        forecast_case,
        forecast_years,
        start_year,
        end_year,
        normal_multiple_years,
        user_growth_rate,
        target_multiple,
        show_price,
        show_metric_area,
        show_fair_value,
        show_normal_multiple,
        show_current_valuation,
        show_custom_valuation,
        custom_valuation_multiple,
        show_dividend_floor,
        show_payout_ratio,
        show_dividend_yield,
        show_recession_bands,
        show_forecast,
        show_scenario_lines,
        hidden_scenario_lines,
        manual_eps_values,
    )


@app.get("/api/v1/companies/{company_id}/performance")
def company_performance(
    company_id: str,
    initial_investment: Decimal = Decimal("10000"),
) -> dict:
    ticker = company_id.upper()
    db_payload = _source_backed_valuation_payload(ticker)
    if db_payload is not None:
        currency = SAMPLE_SECURITY_META.get(ticker, {}).get("currency", "USD")
        snapshot = company_snapshot_from_postgres(ticker)
        if snapshot and snapshot.get("currency"):
            currency = str(snapshot["currency"])
        performance = build_performance_table(
            ticker,
            db_payload["data"],
            currency=currency,
            initial_investment=initial_investment,
            data_mode="source_backed",
        )
        return {
            "data": _json_safe_object(performance),
            "meta": {
                "ticker": ticker,
                "data_mode": "source_backed",
                "source": "postgres",
                "scope": _performance_scope(),
            },
        }
    if ticker not in SAMPLE_SECURITY_META:
        raise HTTPException(status_code=404, detail="company not found")
    _require_fixture_fallback("performance")
    currency = SAMPLE_SECURITY_META[ticker]["currency"]
    valuation_payload = valuation_map(ticker, forecast_years=5)
    performance = build_performance_table(
        ticker,
        valuation_payload["data"],
        currency=currency,
        initial_investment=initial_investment,
        data_mode="fixture_non_production",
    )
    return {
        "data": _json_safe_object(performance),
        "meta": {
            "ticker": ticker,
            "data_mode": "fixture_non_production",
            "source": "fixture",
            "scope": _performance_scope(),
        },
    }


@app.get("/api/company/{company_id}/performance")
def company_performance_compat(
    company_id: str,
    initial_investment: Decimal = Decimal("10000"),
) -> dict:
    return company_performance(company_id, initial_investment)


@app.get("/api/v1/companies/{company_id}/forecast-snapshots")
def forecast_snapshots(company_id: str) -> dict:
    ticker = company_id.upper()
    db_payload = forecast_evidence_from_postgres(ticker)
    if db_payload is not None:
        return {"data": db_payload}
    local_payload = local_forecast_evidence_from_csv(ticker)
    if local_payload is not None:
        return {"data": local_payload}
    if ticker not in SAMPLE_SECURITY_META:
        raise HTTPException(status_code=404, detail="company not found")
    _require_fixture_fallback("forecast_snapshots")
    return {"data": forecast_evidence_for(ticker)}


@app.get("/api/v1/companies/{company_id}/analyst-scorecard")
def company_analyst_scorecard(company_id: str) -> dict:
    ticker = company_id.upper()
    db_payload = forecast_evidence_from_postgres(ticker)
    if db_payload is not None:
        scorecard = build_analyst_scorecard(
            ticker,
            db_payload,
            currency=_fiscal_fitness_currency(ticker),
            data_mode="source_backed",
        )
        return {
            "data": _json_safe_object(scorecard),
            "meta": {
                "ticker": ticker,
                "data_mode": "source_backed",
                "source": "postgres",
                "scope": _analyst_scorecard_scope(),
            },
        }
    if ticker not in SAMPLE_SECURITY_META:
        raise HTTPException(status_code=404, detail="company not found")
    _require_fixture_fallback("analyst_scorecard")
    scorecard = build_analyst_scorecard(
        ticker,
        forecast_evidence_for(ticker),
        currency=SAMPLE_SECURITY_META[ticker]["currency"],
        data_mode="fixture_non_production",
    )
    return {
        "data": _json_safe_object(scorecard),
        "meta": {
            "ticker": ticker,
            "data_mode": "fixture_non_production",
            "source": "fixture",
            "scope": _analyst_scorecard_scope(),
        },
    }


@app.get("/api/company/{company_id}/analyst-scorecard")
def company_analyst_scorecard_compat(company_id: str) -> dict:
    return company_analyst_scorecard(company_id)


@app.get("/api/v1/screener")
def screener(
    max_per: Decimal = Decimal("25"),
    min_roe: Decimal | None = None,
    min_eps_cagr: Decimal | None = None,
    max_debt_to_equity: Decimal | None = None,
    min_market_cap: Decimal | None = None,
    min_market_cap_usd: Decimal | None = None,
    relative_discount_pct: Decimal = Decimal("0"),
    require_roe_gt_roic: bool = True,
) -> dict:
    config = ScreenerConfig(
        max_per=max_per,
        min_roe=min_roe,
        min_eps_cagr=min_eps_cagr,
        max_debt_to_equity=max_debt_to_equity,
        min_market_cap=min_market_cap,
        min_market_cap_usd=min_market_cap_usd,
        relative_discount_pct=relative_discount_pct,
        require_roe_gt_roic=require_roe_gt_roic,
    )
    db_rows = screener_rows_from_postgres()
    if db_rows is not None:
        rows = _with_screener_source_traces(
            apply_screener_filters(db_rows, config),
            data_mode="source_backed",
        )
        return {
            "data": _json_safe_object(rows),
            "meta": {
                "filters": screener_filter_descriptions(config),
                "config": config.as_meta(),
                "total": len(rows),
                "pass_count": sum(1 for row in rows if row["filters"]["passes_all"]),
                "data_mode": "source_backed",
            },
        }
    _require_fixture_fallback("screener")
    rows = _with_screener_source_traces(
        apply_screener_filters(screener_rows(), config),
        data_mode="fixture_non_production",
    )
    return {
        "data": _json_safe_object(rows),
        "meta": {
            "filters": screener_filter_descriptions(config),
            "config": config.as_meta(),
            "total": len(rows),
            "pass_count": sum(1 for row in rows if row["filters"]["passes_all"]),
            "data_mode": "fixture_non_production",
        },
    }


@app.get("/api/screener")
def screener_compat() -> dict:
    return screener()


@app.get("/api/v1/portfolio/sample")
def portfolio_sample() -> dict:
    _require_fixture_fallback("portfolio_sample")
    return _portfolio_payload(PORTFOLIO_FIXTURE_CSV)


@app.get("/api/v1/portfolio")
def portfolio(request: Request) -> dict:
    owner_key = request_owner_key(request)
    db_payload = portfolio_from_postgres(owner_key=owner_key)
    if db_payload is not None:
        return {
            "data": _json_safe_object(db_payload),
            "meta": {"source": "postgres", "data_mode": "source_backed"},
        }
    _require_fixture_fallback("portfolio")
    return _portfolio_payload(PORTFOLIO_FIXTURE_CSV)


@app.post("/api/v1/portfolio/import")
def portfolio_import(payload: PortfolioImportRequest, request: Request) -> dict:
    owner_key = request_owner_key(request)
    if payload.persist:
        db_payload = store_portfolio_csv_to_postgres(
            payload.csv_text,
            owner_key=owner_key,
            replace_existing=payload.replace_existing,
        )
        if db_payload is not None:
            return {
                "data": _json_safe_object(db_payload),
                "meta": {"source": "postgres", "data_mode": "source_backed"},
            }
    return _portfolio_payload(payload.csv_text)


@app.get("/api/v1/watchlist")
def watchlist(request: Request, name: str = "Default") -> dict:
    owner_key = request_owner_key(request)
    db_payload = watchlist_from_postgres(owner_key=owner_key, name=name)
    if db_payload is not None:
        return {
            "data": _json_safe_object(db_payload),
            "meta": {"source": "postgres", "data_mode": "source_backed"},
        }
    _require_fixture_fallback("watchlist")
    return _watchlist_payload(name)


@app.post("/api/v1/watchlist/items")
def watchlist_add_item(payload: WatchlistItemRequest, request: Request) -> dict:
    owner_key = request_owner_key(request)
    if payload.persist:
        db_payload = add_watchlist_item_to_postgres(
            payload.ticker,
            note=payload.note,
            owner_key=owner_key,
            name=payload.name,
        )
        if db_payload is not None:
            return {
                "data": _json_safe_object(db_payload),
                "meta": {"source": "postgres", "data_mode": "source_backed"},
            }
    return _watchlist_payload(payload.name, payload.ticker, payload.note)


@app.delete("/api/v1/watchlist/items/{ticker}")
def watchlist_remove_item(ticker: str, request: Request, name: str = "Default") -> dict:
    owner_key = request_owner_key(request)
    db_payload = remove_watchlist_item_from_postgres(ticker, owner_key=owner_key, name=name)
    if db_payload is not None:
        return {
            "data": _json_safe_object(db_payload),
            "meta": {"source": "postgres", "data_mode": "source_backed"},
        }
    _require_fixture_fallback("watchlist")
    return _watchlist_payload(name, remove_ticker=ticker)


@app.get("/api/v1/charts/valuation-map/{company_id}.svg")
def valuation_chart_svg(
    company_id: str,
    metric: str = "adjusted_operating",
    forecast_mode: str = "custom",
    forecast_case: str = "median",
    forecast_years: int = 5,
    start_year: int | None = None,
    end_year: int | None = None,
    normal_multiple_years: int | None = None,
    user_growth_rate: Decimal | None = None,
    target_multiple: Decimal | None = None,
    show_price: bool = True,
    show_metric_area: bool = True,
    show_fair_value: bool = True,
    show_normal_multiple: bool = True,
    show_current_valuation: bool = True,
    show_custom_valuation: bool = False,
    custom_valuation_multiple: Decimal | None = None,
    show_dividend_floor: bool = True,
    show_payout_ratio: bool = True,
    show_dividend_yield: bool = False,
    show_recession_bands: bool = True,
    show_forecast: bool = True,
    show_scenario_lines: bool = True,
    hidden_scenario_lines: str | None = None,
    manual_eps_values: str | None = None,
) -> Response:
    payload = _valuation_chart_payload(
        company_id,
        metric,
        forecast_mode,
        forecast_case,
        forecast_years,
        start_year,
        end_year,
        normal_multiple_years,
        user_growth_rate,
        target_multiple,
        show_price,
        show_metric_area,
        show_fair_value,
        show_normal_multiple,
        show_current_valuation,
        show_custom_valuation,
        custom_valuation_multiple,
        show_dividend_floor,
        show_payout_ratio,
        show_dividend_yield,
        show_recession_bands,
        show_forecast,
        show_scenario_lines,
        hidden_scenario_lines,
        manual_eps_values,
    )
    return _chart_response(company_id, payload, "svg")


@app.get("/api/v1/charts/valuation-map/{company_id}.png")
def valuation_chart_png(
    company_id: str,
    metric: str = "adjusted_operating",
    forecast_mode: str = "custom",
    forecast_case: str = "median",
    forecast_years: int = 5,
    start_year: int | None = None,
    end_year: int | None = None,
    normal_multiple_years: int | None = None,
    user_growth_rate: Decimal | None = None,
    target_multiple: Decimal | None = None,
    show_price: bool = True,
    show_metric_area: bool = True,
    show_fair_value: bool = True,
    show_normal_multiple: bool = True,
    show_current_valuation: bool = True,
    show_custom_valuation: bool = False,
    custom_valuation_multiple: Decimal | None = None,
    show_dividend_floor: bool = True,
    show_payout_ratio: bool = True,
    show_dividend_yield: bool = False,
    show_recession_bands: bool = True,
    show_forecast: bool = True,
    show_scenario_lines: bool = True,
    hidden_scenario_lines: str | None = None,
    manual_eps_values: str | None = None,
) -> Response:
    payload = _valuation_chart_payload(
        company_id,
        metric,
        forecast_mode,
        forecast_case,
        forecast_years,
        start_year,
        end_year,
        normal_multiple_years,
        user_growth_rate,
        target_multiple,
        show_price,
        show_metric_area,
        show_fair_value,
        show_normal_multiple,
        show_current_valuation,
        show_custom_valuation,
        custom_valuation_multiple,
        show_dividend_floor,
        show_payout_ratio,
        show_dividend_yield,
        show_recession_bands,
        show_forecast,
        show_scenario_lines,
        hidden_scenario_lines,
        manual_eps_values,
    )
    return _chart_response(company_id, payload, "png")


@app.post("/api/v1/charts/valuation-map/runs")
def create_valuation_chart_run(request: ValuationChartRunRequest) -> dict:
    payload = _valuation_chart_payload(
        request.company_id,
        request.metric,
        request.forecast_mode,
        request.forecast_case,
        request.forecast_years,
        request.start_year,
        request.end_year,
        request.normal_multiple_years,
        request.user_growth_rate,
        request.target_multiple,
        request.show_price,
        request.show_metric_area,
        request.show_fair_value,
        request.show_normal_multiple,
        request.show_current_valuation,
        request.show_custom_valuation,
        request.custom_valuation_multiple,
        request.show_dividend_floor,
        request.show_payout_ratio,
        request.show_dividend_yield,
        request.show_recession_bands,
        request.show_forecast,
        request.show_scenario_lines,
        request.hidden_scenario_lines,
        request.manual_eps_values,
    )
    record = create_chart_run(request.model_dump(mode="json"), payload)
    chart_run_id = record["id"]
    return {
        "data": {
            "chart_run_id": chart_run_id,
            "ticker": record["ticker"],
            "metric": record["metric"],
            "data_mode": record["data_mode"],
            "data_backend": record["data_backend"],
            "svg_url": f"/api/v1/charts/valuation-map/runs/{chart_run_id}.svg",
            "png_url": f"/api/v1/charts/valuation-map/runs/{chart_run_id}.png",
            "svg_cache_key": record["svg_cache_key"],
            "png_cache_key": record["png_cache_key"],
            "svg_blob_key": record["svg_blob_key"],
            "png_blob_key": record["png_blob_key"],
            "evidence_summary": record.get("evidence_summary"),
            "created_at": record["created_at"],
        }
    }


@app.get("/api/v1/charts/valuation-map/runs/{chart_run_id}.svg")
def valuation_chart_run_svg(chart_run_id: str) -> Response:
    return _chart_run_response(chart_run_id, "svg")


@app.get("/api/v1/charts/valuation-map/runs/{chart_run_id}.png")
def valuation_chart_run_png(chart_run_id: str) -> Response:
    return _chart_run_response(chart_run_id, "png")


@app.get("/api/v1/charts/valuation-map/runs/{chart_run_id}")
def valuation_chart_run(chart_run_id: str) -> dict:
    record = load_chart_run(chart_run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="chart run not found")
    return {"data": record}


@app.get("/api/v1/chart-layouts")
def chart_layouts(request: Request, owner_key: str = "default") -> dict:
    resolved_owner_key = request_owner_key(request, owner_key)
    return {"data": list_chart_layouts(resolved_owner_key)}


@app.post("/api/v1/chart-layouts")
def create_chart_layout(payload: ChartLayoutRequest, request: Request) -> dict:
    owner_key = request_owner_key(request, payload.owner_key)
    record = save_chart_layout(
        payload.name,
        {
            "company_id": payload.company_id,
            "metric": payload.metric,
            "forecast_mode": payload.forecast_mode,
            "forecast_case": payload.forecast_case,
            "forecast_years": payload.forecast_years,
            "start_year": payload.start_year,
            "end_year": payload.end_year,
            "normal_multiple_years": payload.normal_multiple_years,
            "user_growth_rate": payload.user_growth_rate,
            "target_multiple": payload.target_multiple,
            "manual_eps_values": payload.manual_eps_values,
            "visibility": payload.visibility,
            "hidden_scenario_lines": payload.hidden_scenario_lines,
        },
        owner_key=owner_key,
    )
    return {"data": record}


@app.delete("/api/v1/chart-layouts/{layout_id}")
def remove_chart_layout(layout_id: str, request: Request, owner_key: str = "default") -> dict:
    resolved_owner_key = request_owner_key(request, owner_key)
    removed = delete_chart_layout(layout_id, resolved_owner_key)
    if not removed:
        raise HTTPException(status_code=404, detail="chart layout not found")
    return {"data": list_chart_layouts(resolved_owner_key)}


def _valuation_chart_payload(
    company_id: str,
    metric: str,
    forecast_mode: str,
    forecast_case: str,
    forecast_years: int,
    start_year: int | None,
    end_year: int | None,
    normal_multiple_years: int | None,
    user_growth_rate: Decimal | None,
    target_multiple: Decimal | None,
    show_price: bool,
    show_metric_area: bool,
    show_fair_value: bool,
    show_normal_multiple: bool,
    show_current_valuation: bool,
    show_custom_valuation: bool,
    custom_valuation_multiple: Decimal | None,
    show_dividend_floor: bool,
    show_payout_ratio: bool,
    show_dividend_yield: bool,
    show_recession_bands: bool,
    show_forecast: bool,
    show_scenario_lines: bool,
    hidden_scenario_lines: str | list[str] | None,
    manual_eps_values: str | None,
) -> dict:
    return valuation_map(
        company_id,
        metric=metric,
        forecast_mode=forecast_mode,
        forecast_case=forecast_case,
        forecast_years=forecast_years,
        start_year=start_year,
        end_year=end_year,
        normal_multiple_years=normal_multiple_years,
        user_growth_rate=user_growth_rate,
        target_multiple=target_multiple,
        show_price=show_price,
        show_metric_area=show_metric_area,
        show_fair_value=show_fair_value,
        show_normal_multiple=show_normal_multiple,
        show_current_valuation=show_current_valuation,
        show_custom_valuation=show_custom_valuation,
        custom_valuation_multiple=custom_valuation_multiple,
        show_dividend_floor=show_dividend_floor,
        show_payout_ratio=show_payout_ratio,
        show_dividend_yield=show_dividend_yield,
        show_recession_bands=show_recession_bands,
        show_forecast=show_forecast,
        show_scenario_lines=show_scenario_lines,
        hidden_scenario_lines=",".join(hidden_scenario_lines)
        if isinstance(hidden_scenario_lines, list)
        else hidden_scenario_lines,
        manual_eps_values=manual_eps_values,
    )


def _chart_response(company_id: str, payload: dict, chart_format: str) -> Response:
    renderer = render_valuation_png if chart_format == "png" else render_valuation_svg
    result = render_cached_valuation_chart(company_id, payload, chart_format, renderer)
    return Response(
        content=result.content,
        media_type=result.content_type,
        headers={
            "Cache-Control": "private, max-age=300",
            "X-Chart-Cache-Key": result.cache_key,
            "X-Chart-Cache": "hit" if result.cached else "miss",
            "X-Chart-Blob-Key": result.blob_key,
            "X-Data-Mode": str(payload.get("meta", {}).get("data_mode") or "unknown"),
        },
    )


def _chart_run_response(chart_run_id: str, chart_format: str) -> Response:
    record = load_chart_run(chart_run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="chart run not found")
    return _chart_response(record["ticker"], record["payload"], chart_format)


@app.get("/api/v1/companies/{company_id}/data-audit")
def data_audit(
    company_id: str,
    metric: str = "adjusted_operating",
    forecast_mode: str = "custom",
    forecast_case: str = "median",
    forecast_years: int = 5,
    start_year: int | None = None,
    end_year: int | None = None,
    normal_multiple_years: int | None = None,
    user_growth_rate: Decimal | None = None,
    target_multiple: Decimal | None = None,
    manual_eps_values: str | None = None,
) -> dict:
    ticker = company_id.upper()
    valuation_kwargs = _valuation_context_kwargs(
        metric=metric,
        forecast_mode=forecast_mode,
        forecast_case=forecast_case,
        forecast_years=forecast_years,
        start_year=start_year,
        end_year=end_year,
        normal_multiple_years=normal_multiple_years,
        user_growth_rate=user_growth_rate,
        target_multiple=target_multiple,
        manual_eps_values=manual_eps_values,
    )
    source_payload = _source_backed_data_audit(ticker, valuation_kwargs)
    if source_payload is not None:
        return source_payload
    if ticker not in SAMPLE_SECURITY_META:
        raise HTTPException(status_code=404, detail="company not found")
    _require_fixture_fallback("data_audit")
    rows: list[dict] = []

    result = sample_normalization_result(ticker, NormalizationPolicy())
    for row in result.series:
        rows.append(
            _audit_row(
                ticker=ticker,
                fiscal_year=row.fiscal_year,
                fact_name="adjusted_earnings.adjusted_eps",
                value=row.adjusted_eps,
                method=row.method,
                policy=row.policy,
                confidence=str(row.confidence),
                quality_status=row.quality_status,
                flags=row.flags,
                formula=row.formula,
                source_trace=row.source_trace.model_dump(mode="json"),
            )
        )

    valuation_payload = valuation_map(ticker, **valuation_kwargs)
    forecast_start = _forecast_return_start(valuation_payload)
    for row in valuation_payload["data"]:
        fiscal_year = int(row["fiscal_year"])
        scope = "forecast" if row["forecast_flag"] else "valuation"
        fact_names = [
            "metric",
            "yoy",
            "price",
            "dividend",
            "normal_multiple",
            "fair_multiple",
            "fair_value_price",
        ]
        if row["forecast_flag"]:
            fact_names.extend(
                fact_name
                for fact_name in (
                    "price_cagr_pct",
                    "total_return_cagr_pct",
                    "margin_of_safety_pct",
                )
                if row.get(fact_name) is not None
            )
        for fact_name in fact_names:
            fact_trace, formula = _valuation_fact_trace(row, fact_name, forecast_start)
            rows.append(
                _audit_row(
                    ticker=ticker,
                    fiscal_year=fiscal_year,
                    fact_name=f"{scope}.{fact_name}",
                    value=row.get(fact_name),
                    method=row.get("forecast_source") or row["source_trace"].get("source_type"),
                    policy="forecast" if row["forecast_flag"] else "valuation_map",
                    confidence=None,
                    quality_status=fact_trace.get("quality_status"),
                    flags=[],
                    formula=formula,
                    source_trace=fact_trace,
                )
            )
    _append_forecast_assumption_audit_rows(rows, ticker, valuation_payload)
    _append_chart_key_audit_rows(rows, ticker, valuation_payload)
    _append_price_point_audit_rows(rows, ticker, valuation_payload)
    _append_forecast_scenario_audit_rows(rows, ticker, valuation_payload)

    forecast_evidence = forecast_evidence_for(ticker)
    forecast_year = int(forecast_evidence["forecast_year"])
    for row in forecast_evidence["cases"]:
        rows.append(
            _audit_row(
                ticker=ticker,
                fiscal_year=forecast_year,
                fact_name=f"forecast_snapshot.{row['case']}.estimate_eps",
                value=row["estimate_eps"],
                method="forecast_snapshot_fixture",
                policy="forecast_snapshot",
                confidence=None,
                quality_status=row["source_trace"].get("quality_status"),
                flags=[],
                formula=row["source_trace"].get("formula"),
                source_trace=row["source_trace"],
            )
        )
    _append_forecast_case_comparison_audit_rows(
        rows,
        ticker,
        valuation_payload,
        forecast_evidence,
    )
    for row in forecast_evidence["revisions"]:
        rows.append(
            _audit_row(
                ticker=ticker,
                fiscal_year=forecast_year,
                fact_name=f"forecast_revision.{row['as_of_label']}.estimate_eps",
                value=row["estimate_eps"],
                method="forecast_snapshot_fixture",
                policy="forecast_revision",
                confidence=None,
                quality_status=row["quality_status"],
                flags=[],
                formula=row["source_trace"].get("formula"),
                source_trace=row["source_trace"],
            )
        )
    sentiment = forecast_evidence["sentiment"]
    rows.append(
        _audit_row(
            ticker=ticker,
            fiscal_year=forecast_year,
            fact_name="analyst_sentiment.net_revision_score_pct",
            value=sentiment["net_revision_score_pct"],
            method="forecast_snapshot_fixture",
            policy="analyst_sentiment",
            confidence=None,
            quality_status=sentiment["quality_status"],
            flags=[],
            formula=forecast_evidence["source_trace"].get("formula"),
            source_trace=forecast_evidence["source_trace"],
        )
    )
    _append_analyst_scorecard_audit_rows(
        rows,
        ticker,
        company_analyst_scorecard(ticker)["data"],
    )

    snapshot = snapshot_for(ticker)
    for fact_name in (
        "current_price",
        "market_cap",
        "listed_shares",
        "per",
        "dividend_yield",
        "eps",
        "eps_cagr",
        "roe",
        "roic",
        "debt_ratio",
    ):
        fact_trace = _snapshot_fact_source_trace(snapshot, fact_name)
        rows.append(
            _audit_row(
                ticker=ticker,
                fiscal_year=result.series[-1].fiscal_year,
                fact_name=f"snapshot.{fact_name}",
                value=snapshot.get(fact_name),
                method=snapshot.get("eps_method"),
                policy="snapshot",
                confidence=snapshot.get("confidence"),
                quality_status=fact_trace.get("quality_status"),
                flags=[],
                formula=fact_trace.get("formula"),
                source_trace=fact_trace,
            )
        )

    for row in financials_for(ticker):
        fiscal_year = int(row["fiscal_year"])
        financial_trace = row["source_trace"].get("financial_fact_trace") or row["source_trace"]
        for fact_name in (
            "revenue",
            "eps",
            "fcf",
            "gross_margin",
            "operating_margin",
            "net_margin",
            "roe",
            "roic",
            "debt_to_equity",
        ):
            rows.append(
                _audit_row(
                    ticker=ticker,
                    fiscal_year=fiscal_year,
                    fact_name=f"financials.{fact_name}",
                    value=row.get(fact_name),
                    method=row.get("method"),
                    policy="financials",
                    confidence=row.get("confidence"),
                    quality_status=financial_trace.get("quality_status"),
                    flags=[],
                    formula=financial_trace.get("formula"),
                    source_trace=financial_trace,
                )
            )

    fun_graphs_payload = company_fun_graphs(ticker)
    for metric in fun_graphs_payload["data"]["metrics"]:
        for point in metric["points"]:
            rows.append(
                _audit_row(
                    ticker=ticker,
                    fiscal_year=int(point["fiscal_year"]),
                    fact_name=f"fun_graphs.{metric['metric_key']}",
                    value=point.get("value"),
                    method=point.get("method"),
                    policy="fun_graphs",
                    confidence=point.get("confidence"),
                    quality_status=point["source_trace"].get("quality_status"),
                    flags=point.get("flags", []),
                    formula=point["source_trace"].get("formula"),
                    source_trace=point["source_trace"],
                )
            )

    fiscal_fitness_payload = company_fiscal_fitness(ticker)
    for row in fiscal_fitness_payload["data"]:
        rows.append(
            _audit_row(
                ticker=ticker,
                fiscal_year=int(row["fiscal_year"]),
                fact_name=f"fiscal_fitness.{row['metric_key']}",
                value=row.get("value"),
                method=row.get("method"),
                policy="fiscal_fitness",
                confidence=row.get("confidence"),
                quality_status=row["source_trace"].get("quality_status"),
                flags=row.get("flags", []),
                formula=row["source_trace"].get("formula"),
                source_trace=row["source_trace"],
            )
        )

    health_check_payload = company_health_check(ticker)
    health_check = health_check_payload["data"]
    rows.append(
        _audit_row(
            ticker=ticker,
            fiscal_year=int(health_check["fiscal_year"]),
            fact_name="health_check.overall_score",
            value=health_check.get("overall_score"),
            method="health_check_derived",
            policy="health_check",
            confidence=None,
            quality_status=health_check["source_trace"].get("quality_status"),
            flags=health_check.get("flags", []),
            formula=health_check["source_trace"].get("formula"),
            source_trace=health_check["source_trace"],
        )
    )
    for axis in health_check["axes"]:
        rows.append(
            _audit_row(
                ticker=ticker,
                fiscal_year=int(health_check["fiscal_year"]),
                fact_name=f"health_check.{axis['axis_key']}",
                value=axis.get("score"),
                method="health_check_axis_derived",
                policy="health_check",
                confidence=None,
                quality_status=axis["source_trace"].get("quality_status"),
                flags=axis.get("flags", []),
                formula=axis["source_trace"].get("formula"),
                source_trace=axis["source_trace"],
            )
        )

    research_report_payload = company_research_report(ticker)
    for fact in research_report_payload["data"]["audit_facts"]:
        rows.append(
            _audit_row(
                ticker=ticker,
                fiscal_year=int(fact["fiscal_year"]),
                fact_name=fact["fact_name"],
                value=fact.get("value"),
                method=fact.get("method"),
                policy=fact.get("policy"),
                confidence=fact.get("confidence"),
                quality_status=fact.get("quality_status"),
                flags=fact.get("flags", []),
                formula=fact.get("formula"),
                source_trace=fact["source_trace"],
            )
        )

    performance_payload = company_performance(ticker)
    for row in performance_payload["data"]["rows"]:
        for fact_name in _performance_scope():
            rows.append(
                _audit_row(
                    ticker=ticker,
                    fiscal_year=int(row["end_year"]),
                    fact_name=f"performance.{fact_name}.{row['start_year']}",
                    value=row.get(fact_name),
                    method="performance_derived",
                    policy="performance",
                    confidence=None,
                    quality_status=row["source_trace"].get("quality_status"),
                    flags=row.get("flags", []),
                    formula=row["source_trace"].get("formula"),
                    source_trace=row["source_trace"],
                )
            )

    use_of_cash_payload = company_use_of_cash(ticker)
    for row in use_of_cash_payload["data"]:
        fiscal_year = int(row["fiscal_year"])
        for fact_name in (
            "operating_cash_flow",
            "free_cash_flow",
            "fcf_margin_pct",
            "dividends_paid",
            "dividend_per_share",
            "dividend_payout_pct",
            "capex",
            "share_repurchases",
            "debt_repayment",
            "acquisitions",
            "net_cash_use",
            "debt_to_equity",
        ):
            rows.append(
                _audit_row(
                    ticker=ticker,
                    fiscal_year=fiscal_year,
                    fact_name=f"use_of_cash.{fact_name}",
                    value=row.get(fact_name),
                    method=row.get("method"),
                    policy="use_of_cash",
                    confidence=row.get("confidence"),
                    quality_status=row["source_trace"].get("quality_status"),
                    flags=row.get("flags", []),
                    formula=row["source_trace"].get("formula"),
                    source_trace=row["source_trace"],
                )
            )

    _append_screener_audit_rows(
        rows,
        ticker,
        _with_screener_source_traces(
            apply_screener_filters(screener_rows(), ScreenerConfig()),
            data_mode="fixture_non_production",
        ),
    )
    _append_portfolio_audit_rows(rows, ticker, _portfolio_payload(PORTFOLIO_FIXTURE_CSV)["data"])
    _append_watchlist_audit_rows(rows, ticker, _watchlist_payload("Default")["data"])

    return {
        "data": rows,
        "meta": {
            "ticker": ticker,
            "total": len(rows),
            "scope": [
                "adjusted_earnings",
                "valuation_map",
                "forecast",
                "forecast_assumption",
                "chart_key",
                "price_points",
                "forecast_snapshot",
                "forecast_case",
                "forecast_scenario",
                "analyst_scorecard",
                "snapshot",
                "financials",
                "fun_graphs",
                "fiscal_fitness",
                "health_check",
                "research_report",
                "performance",
                "use_of_cash",
                "screener",
                "portfolio",
                "portfolio_transaction",
                "watchlist",
            ],
        },
    }


def _valuation_context_kwargs(
    *,
    metric: str,
    forecast_mode: str,
    forecast_case: str,
    forecast_years: int,
    start_year: int | None,
    end_year: int | None,
    normal_multiple_years: int | None,
    user_growth_rate: Decimal | None,
    target_multiple: Decimal | None,
    manual_eps_values: str | None,
) -> dict:
    _validate_forecast_query_inputs(
        forecast_years=forecast_years,
        user_growth_rate=user_growth_rate,
        target_multiple=target_multiple,
        custom_valuation_multiple=None,
        manual_eps_values=manual_eps_values,
    )
    return {
        "metric": metric,
        "forecast_mode": forecast_mode,
        "forecast_case": forecast_case,
        "forecast_years": forecast_years,
        "start_year": start_year,
        "end_year": end_year,
        "normal_multiple_years": normal_multiple_years,
        "user_growth_rate": user_growth_rate,
        "target_multiple": target_multiple,
        "manual_eps_values": manual_eps_values,
    }


def _source_backed_data_audit(
    ticker: str,
    valuation_kwargs: dict | None = None,
) -> dict | None:
    rows: list[dict] = []
    adjusted_payload = adjusted_series_from_postgres(ticker, DEFAULT_POLICY_KEY)
    if adjusted_payload is not None:
        for row in adjusted_payload["series"]:
            rows.append(
                _audit_row(
                    ticker=ticker,
                    fiscal_year=int(row["fiscal_year"]),
                    fact_name="adjusted_earnings.adjusted_eps",
                    value=row["adjusted_eps"],
                    method=row["method"],
                    policy=row["policy"],
                    confidence=row["confidence"],
                    quality_status=row["quality_status"],
                    flags=row["flags"],
                    formula=row["formula"],
                    source_trace=row["source_trace"],
                )
            )

    financial_fact_rows = financial_facts_from_postgres(ticker)
    if financial_fact_rows is not None:
        for row in financial_fact_rows:
            rows.append(
                _audit_row(
                    ticker=ticker,
                    fiscal_year=int(row["fiscal_year"]),
                    fact_name=f"financial_facts.{row['taxonomy']}.{row['tag']}",
                    value=row["value"],
                    method=row["source"],
                    policy="financial_facts",
                    confidence=None,
                    quality_status=row["quality_status"],
                    flags=[],
                    formula=row["source_trace"].get("formula"),
                    source_trace=row["source_trace"],
                )
            )

    kr_warehouse_fact_rows = normalized_facts_from_kr_warehouse(ticker)
    if kr_warehouse_fact_rows is not None:
        for row in kr_warehouse_fact_rows:
            rows.append(
                _audit_row(
                    ticker=ticker,
                    fiscal_year=int(row["fiscal_year"]),
                    fact_name=f"kr_warehouse.{row['metric']}",
                    value=row["value"],
                    method=row["method"],
                    policy=row["policy"],
                    confidence=row["confidence"],
                    quality_status=row["quality_status"],
                    flags=row["flags"],
                    formula=row["formula"],
                    source_trace=row["source_trace"],
                )
            )

    valuation_payload = _source_backed_valuation_payload(ticker, **(valuation_kwargs or {}))
    if valuation_payload is not None:
        forecast_start = _forecast_return_start(valuation_payload)
        for row in valuation_payload["data"]:
            fiscal_year = int(row["fiscal_year"])
            scope = "forecast" if row["forecast_flag"] else "valuation"
            fact_names = [
                "metric",
                "yoy",
                "price",
                "dividend",
                "normal_multiple",
                "fair_multiple",
                "fair_value_price",
            ]
            if row["forecast_flag"]:
                fact_names.extend(
                    fact_name
                    for fact_name in (
                        "price_cagr_pct",
                        "total_return_cagr_pct",
                        "margin_of_safety_pct",
                    )
                    if row.get(fact_name) is not None
                )
            for fact_name in fact_names:
                fact_trace, formula = _valuation_fact_trace(row, fact_name, forecast_start)
                rows.append(
                    _audit_row(
                        ticker=ticker,
                        fiscal_year=fiscal_year,
                        fact_name=f"{scope}.{fact_name}",
                        value=row.get(fact_name),
                        method=row.get("forecast_source") or row["source_trace"].get("source_type"),
                        policy="forecast" if row["forecast_flag"] else "valuation_map",
                        confidence=None,
                        quality_status=fact_trace.get("quality_status"),
                        flags=[],
                        formula=formula,
                        source_trace=fact_trace,
                    )
                )
        _append_forecast_assumption_audit_rows(rows, ticker, valuation_payload)
        _append_chart_key_audit_rows(rows, ticker, valuation_payload)
        _append_price_point_audit_rows(rows, ticker, valuation_payload)
        _append_forecast_scenario_audit_rows(rows, ticker, valuation_payload)
        _append_kr_cache_diagnostic_audit_rows(rows, ticker, valuation_payload)

    forecast_evidence = forecast_evidence_from_postgres(ticker)
    if forecast_evidence is None:
        forecast_evidence = local_forecast_evidence_from_csv(ticker)
    if forecast_evidence is not None:
        forecast_year = int(forecast_evidence["forecast_year"])
        for row in forecast_evidence["cases"]:
            rows.append(
                _audit_row(
                    ticker=ticker,
                    fiscal_year=forecast_year,
                    fact_name=f"forecast_snapshot.{row['case']}.estimate_eps",
                    value=row["estimate_eps"],
                    method="forecast_snapshot",
                    policy="forecast_snapshot",
                    confidence=None,
                    quality_status=row["source_trace"].get("quality_status"),
                    flags=[],
                    formula=row["source_trace"].get("formula"),
                    source_trace=row["source_trace"],
                )
            )
        if valuation_payload is not None:
            _append_forecast_case_comparison_audit_rows(
                rows,
                ticker,
                valuation_payload,
                forecast_evidence,
            )
        for row in forecast_evidence["revisions"]:
            rows.append(
                _audit_row(
                    ticker=ticker,
                    fiscal_year=forecast_year,
                    fact_name=f"forecast_revision.{row['as_of_label']}.estimate_eps",
                    value=row["estimate_eps"],
                    method="forecast_snapshot",
                    policy="forecast_revision",
                    confidence=None,
                    quality_status=row["quality_status"],
                    flags=[],
                    formula=row["source_trace"].get("formula"),
                    source_trace=row["source_trace"],
                )
            )
        sentiment = forecast_evidence["sentiment"]
        rows.append(
            _audit_row(
                ticker=ticker,
                fiscal_year=forecast_year,
                fact_name="analyst_sentiment.net_revision_score_pct",
                value=sentiment["net_revision_score_pct"],
                method="forecast_snapshot",
                policy="analyst_sentiment",
                confidence=None,
                quality_status=sentiment["quality_status"],
                flags=[],
                formula=forecast_evidence["source_trace"].get("formula"),
                source_trace=forecast_evidence["source_trace"],
            )
        )
        scorecard = build_analyst_scorecard(
            ticker,
            forecast_evidence,
            currency=_fiscal_fitness_currency(ticker),
            data_mode="source_backed",
        )
        _append_analyst_scorecard_audit_rows(rows, ticker, _json_safe_object(scorecard))

    snapshot = company_snapshot_from_postgres(ticker)
    if snapshot is not None:
        fiscal_year = _latest_audit_year(rows)
        for fact_name in (
            "current_price",
            "market_cap",
            "listed_shares",
            "per",
            "dividend_yield",
            "eps",
            "eps_cagr",
            "roe",
            "roic",
            "debt_ratio",
        ):
            fact_trace = _snapshot_fact_source_trace(snapshot, fact_name)
            rows.append(
                _audit_row(
                    ticker=ticker,
                    fiscal_year=fiscal_year,
                    fact_name=f"snapshot.{fact_name}",
                    value=snapshot.get(fact_name),
                    method=snapshot.get("eps_method"),
                    policy="snapshot",
                    confidence=snapshot.get("confidence"),
                    quality_status=fact_trace.get("quality_status"),
                    flags=[],
                    formula=fact_trace.get("formula"),
                    source_trace=fact_trace,
                )
            )

    financial_rows = financials_from_postgres(ticker)
    if financial_rows is None and ticker in KR_TOP_MARKET_CAP_PRIORITY_TICKERS:
        financial_rows = financials_from_kr_warehouse(ticker)
    if financial_rows is not None:
        _append_financials_audit_rows(rows, ticker, financial_rows)

        fun_graphs = build_fun_graphs(
            ticker,
            financial_rows,
            currency=_fiscal_fitness_currency(ticker),
            data_mode="source_backed",
        )
        for metric in fun_graphs["metrics"]:
            for point in metric["points"]:
                rows.append(
                    _audit_row(
                        ticker=ticker,
                        fiscal_year=int(point["fiscal_year"]),
                        fact_name=f"fun_graphs.{metric['metric_key']}",
                        value=point.get("value"),
                        method=point.get("method"),
                        policy="fun_graphs",
                        confidence=point.get("confidence"),
                        quality_status=point["source_trace"].get("quality_status"),
                        flags=point.get("flags", []),
                        formula=point["source_trace"].get("formula"),
                        source_trace=point["source_trace"],
                    )
                )

    if financial_rows is not None:
        fiscal_fitness_rows = build_fiscal_fitness_rows(
            ticker,
            financial_rows,
            currency=_fiscal_fitness_currency(ticker),
            data_mode="source_backed",
        )
        for row in fiscal_fitness_rows:
            rows.append(
                _audit_row(
                    ticker=ticker,
                    fiscal_year=int(row["fiscal_year"]),
                    fact_name=f"fiscal_fitness.{row['metric_key']}",
                    value=row.get("value"),
                    method=row.get("method"),
                    policy="fiscal_fitness",
                    confidence=row.get("confidence"),
                    quality_status=row["source_trace"].get("quality_status"),
                    flags=row.get("flags", []),
                    formula=row["source_trace"].get("formula"),
                    source_trace=row["source_trace"],
                )
            )

        health_check = build_health_check_score(
            ticker,
            fiscal_fitness_rows,
            currency=_fiscal_fitness_currency(ticker),
            data_mode="source_backed",
            forecast_evidence=forecast_evidence,
        )
        rows.append(
            _audit_row(
                ticker=ticker,
                fiscal_year=int(health_check["fiscal_year"]),
                fact_name="health_check.overall_score",
                value=health_check.get("overall_score"),
                method="health_check_derived",
                policy="health_check",
                confidence=None,
                quality_status=health_check["source_trace"].get("quality_status"),
                flags=health_check.get("flags", []),
                formula=health_check["source_trace"].get("formula"),
                source_trace=health_check["source_trace"],
            )
        )
        for axis in health_check["axes"]:
            rows.append(
                _audit_row(
                    ticker=ticker,
                    fiscal_year=int(health_check["fiscal_year"]),
                    fact_name=f"health_check.{axis['axis_key']}",
                    value=axis.get("score"),
                    method="health_check_axis_derived",
                    policy="health_check",
                    confidence=None,
                    quality_status=axis["source_trace"].get("quality_status"),
                    flags=axis.get("flags", []),
                    formula=axis["source_trace"].get("formula"),
                    source_trace=axis["source_trace"],
                )
            )

    use_of_cash_rows = None
    use_of_cash_inputs = use_of_cash_inputs_from_postgres(ticker)
    if use_of_cash_inputs is not None:
        use_of_cash_financials, use_of_cash_valuation_rows, use_of_cash_currency = (
            use_of_cash_inputs
        )
        use_of_cash_rows = build_use_of_cash_rows(
            ticker,
            use_of_cash_financials,
            use_of_cash_valuation_rows,
            currency=use_of_cash_currency,
            data_mode="source_backed",
        )
    if use_of_cash_rows is not None:
        for row in use_of_cash_rows:
            fiscal_year = int(row["fiscal_year"])
            for fact_name in (
                "operating_cash_flow",
                "free_cash_flow",
                "fcf_margin_pct",
                "dividends_paid",
                "dividend_per_share",
                "dividend_payout_pct",
                "capex",
                "share_repurchases",
                "debt_repayment",
                "acquisitions",
                "net_cash_use",
                "debt_to_equity",
            ):
                rows.append(
                    _audit_row(
                        ticker=ticker,
                        fiscal_year=fiscal_year,
                        fact_name=f"use_of_cash.{fact_name}",
                        value=row.get(fact_name),
                        method=row.get("method"),
                        policy="use_of_cash",
                        confidence=row.get("confidence"),
                        quality_status=row["source_trace"].get("quality_status"),
                        flags=row.get("flags", []),
                        formula=row["source_trace"].get("formula"),
                        source_trace=row["source_trace"],
                    )
                )

    if financial_rows is not None:
        report_currency = _fiscal_fitness_currency(ticker)
        fiscal_rows = build_fiscal_fitness_rows(
            ticker,
            financial_rows,
            currency=report_currency,
            data_mode="source_backed",
        )
        report_health_check = build_health_check_score(
            ticker,
            fiscal_rows,
            currency=report_currency,
            data_mode="source_backed",
            forecast_evidence=forecast_evidence,
        )
        report = build_research_report(
            ticker,
            snapshot=snapshot,
            valuation_rows=(valuation_payload or {"data": []})["data"],
            financial_rows=financial_rows,
            fiscal_fitness_rows=fiscal_rows,
            health_check=report_health_check,
            forecast_evidence=forecast_evidence,
            use_of_cash_rows=use_of_cash_rows,
            currency=report_currency,
            data_mode="source_backed",
        )
        for fact in report["audit_facts"]:
            rows.append(
                _audit_row(
                    ticker=ticker,
                    fiscal_year=int(fact["fiscal_year"]),
                    fact_name=fact["fact_name"],
                    value=fact.get("value"),
                    method=fact.get("method"),
                    policy=fact.get("policy"),
                    confidence=fact.get("confidence"),
                    quality_status=fact.get("quality_status"),
                    flags=fact.get("flags", []),
                    formula=fact.get("formula"),
                    source_trace=fact["source_trace"],
                )
            )

    if valuation_payload is not None:
        performance = build_performance_table(
            ticker,
            valuation_payload["data"],
            currency=_fiscal_fitness_currency(ticker),
            data_mode="source_backed",
        )
        for row in performance["rows"]:
            for fact_name in _performance_scope():
                rows.append(
                    _audit_row(
                        ticker=ticker,
                        fiscal_year=int(row["end_year"]),
                        fact_name=f"performance.{fact_name}.{row['start_year']}",
                        value=row.get(fact_name),
                        method="performance_derived",
                        policy="performance",
                        confidence=None,
                        quality_status=row["source_trace"].get("quality_status"),
                        flags=row.get("flags", []),
                        formula=row["source_trace"].get("formula"),
                        source_trace=row["source_trace"],
                    )
                )

    screener_db_rows = screener_rows_from_postgres()
    if screener_db_rows is not None:
        _append_screener_audit_rows(
            rows,
            ticker,
            _with_screener_source_traces(
                apply_screener_filters(screener_db_rows, ScreenerConfig()),
                data_mode="source_backed",
            ),
        )

    portfolio_payload = portfolio_from_postgres(owner_key="default")
    if portfolio_payload is not None:
        _append_portfolio_audit_rows(rows, ticker, portfolio_payload)

    watchlist_payload = watchlist_from_postgres(owner_key="default", name="Default")
    if watchlist_payload is not None:
        _append_watchlist_audit_rows(rows, ticker, watchlist_payload)

    if not rows:
        return None
    return {
        "data": rows,
        "meta": {
            "ticker": ticker,
            "total": len(rows),
            "data_mode": "source_backed",
            "scope": [
                "adjusted_earnings",
                "financial_facts",
                "valuation_map",
                "forecast",
                "forecast_assumption",
                "chart_key",
                "price_points",
                "forecast_snapshot",
                "forecast_case",
                "forecast_scenario",
                "analyst_scorecard",
                "snapshot",
                "financials",
                "fun_graphs",
                "fiscal_fitness",
                "health_check",
                "research_report",
                "performance",
                "use_of_cash",
                "screener",
                "portfolio",
                "portfolio_transaction",
                "watchlist",
                "data_quality",
            ],
        },
    }


def _source_backed_valuation_payload(ticker: str, **valuation_kwargs) -> dict | None:
    try:
        payload = valuation_map(ticker, **valuation_kwargs)
    except HTTPException:
        return None
    meta = payload.get("meta", {})
    data_mode = meta.get("data_mode")
    data_backend = meta.get("data_backend")
    if (
        data_mode not in {"source_backed", "source_backed_cache", "source_backed_required"}
        and data_backend != "kr_valuation_input_cache"
    ):
        return None
    return payload


def _append_financials_audit_rows(
    rows: list[dict],
    ticker: str,
    financial_rows: list[dict],
) -> None:
    for row in financial_rows:
        fiscal_year = int(row["fiscal_year"])
        row_trace = row["source_trace"].get("financial_fact_trace") or row["source_trace"]
        metric_traces = row.get("metric_traces") or {}
        for fact_name in (
            "revenue",
            "eps",
            "fcf",
            "gross_margin",
            "operating_margin",
            "net_margin",
            "roe",
            "roic",
            "debt_to_equity",
        ):
            value = row.get(fact_name)
            if value is None:
                continue
            financial_trace = metric_traces.get(fact_name) or row_trace
            rows.append(
                _audit_row(
                    ticker=ticker,
                    fiscal_year=fiscal_year,
                    fact_name=f"financials.{fact_name}",
                    value=value,
                    method=row.get("method"),
                    policy="financials",
                    confidence=row.get("confidence"),
                    quality_status=financial_trace.get("quality_status"),
                    flags=financial_trace.get("quality_flags", []),
                    formula=financial_trace.get("formula"),
                    source_trace=financial_trace,
                )
            )


def _append_kr_cache_diagnostic_audit_rows(
    rows: list[dict],
    ticker: str,
    valuation_payload: dict,
) -> None:
    meta = valuation_payload.get("meta") or {}
    kr_cache = meta.get("kr_cache") or {}
    if not isinstance(kr_cache, dict):
        return
    cache_generated_at = str(
        kr_cache.get("cache_generated_at")
        or meta.get("cache_generated_at")
        or ""
    )
    for gap in kr_cache.get("market_gap_diagnostics") or []:
        if not isinstance(gap, dict):
            continue
        fiscal_year = _gap_fiscal_year(gap)
        if fiscal_year is None:
            continue
        status = _gap_status(gap)
        trace = _kr_cache_gap_source_trace(
            ticker=ticker,
            fiscal_year=fiscal_year,
            gap=gap,
            status=status,
            source_type="kr_cache_market_gap_diagnostic",
            source_document_id=(
                gap.get("pykrx_source_document_id")
                or gap.get("marcap_source_document_id")
                or f"kr-cache:{ticker}:{fiscal_year}:market-gap:{status}"
            ),
            available_at=cache_generated_at,
            formula="diagnostic = KR cache price and market-structure coverage check",
            flags=["kr_cache_gap_diagnostic", f"kr_market_gap_{status}"],
        )
        rows.append(
            _audit_row(
                ticker=ticker,
                fiscal_year=fiscal_year,
                fact_name=f"data_quality.kr_market_gap.{status}",
                value=gap.get("reason") or status,
                method="KR_CACHE_MARKET_GAP_DIAGNOSTIC",
                policy="data_quality",
                confidence="0.90",
                quality_status=status,
                flags=trace["quality_flags"],
                formula=trace["formula"],
                source_trace=trace,
            )
        )
    for gap in kr_cache.get("financial_gap_diagnostics") or []:
        if not isinstance(gap, dict):
            continue
        fiscal_year = _gap_fiscal_year(gap)
        if fiscal_year is None:
            continue
        status = _gap_status(gap)
        source_document_id = (
            gap.get("source_document_id")
            or gap.get("filing_id")
            or f"opendart:{ticker}:{fiscal_year}:status:{gap.get('opendart_status') or status}"
        )
        trace = _kr_cache_gap_source_trace(
            ticker=ticker,
            fiscal_year=fiscal_year,
            gap=gap,
            status=status,
            source_type="kr_cache_financial_gap_diagnostic",
            source_document_id=source_document_id,
            available_at=cache_generated_at,
            formula="diagnostic = KR cache OpenDART annual financial metric coverage check",
            flags=["kr_cache_gap_diagnostic", f"kr_financial_gap_{status}"],
        )
        rows.append(
            _audit_row(
                ticker=ticker,
                fiscal_year=fiscal_year,
                fact_name=f"data_quality.kr_financial_gap.{status}",
                value=gap.get("reason") or gap.get("opendart_message") or status,
                method="KR_CACHE_FINANCIAL_GAP_DIAGNOSTIC",
                policy="data_quality",
                confidence="0.90",
                quality_status=status,
                flags=trace["quality_flags"],
                formula=trace["formula"],
                source_trace=trace,
            )
        )


def _gap_fiscal_year(gap: dict) -> int | None:
    try:
        return int(gap.get("fiscal_year"))
    except (TypeError, ValueError):
        return None


def _gap_status(gap: dict) -> str:
    return str(gap.get("status") or "unknown_gap").strip() or "unknown_gap"


def _kr_cache_gap_source_trace(
    *,
    ticker: str,
    fiscal_year: int,
    gap: dict,
    status: str,
    source_type: str,
    source_document_id,
    available_at: str,
    formula: str,
    flags: list[str],
) -> dict:
    source_document = str(source_document_id or f"kr-cache:{ticker}:{fiscal_year}:gap:{status}")
    period = f"FY{fiscal_year}"
    return {
        "source": "kr_valuation_input_cache",
        "source_type": source_type,
        "source_document_id": source_document,
        "filing_id": str(gap.get("filing_id") or source_document),
        "accession_number": str(gap.get("filing_id") or source_document),
        "form": str(gap.get("form") or "KR valuation input cache diagnostic"),
        "form_type": str(gap.get("form_type") or "KR_CACHE_DIAGNOSTIC"),
        "period": period,
        "fiscal_year": fiscal_year,
        "fiscal_period": "FY",
        "period_start": f"{fiscal_year}-01-01",
        "period_end": f"{fiscal_year}-12-31",
        "available_at": available_at or f"{fiscal_year + 1}-04-01T00:00:00+09:00",
        "unit": "diagnostic",
        "currency": "KRW",
        "method": source_type,
        "formula": formula,
        "input_fact_ids": [],
        "adjustments": [],
        "confidence": "0.90",
        "quality_flags": flags,
        "quality_status": status,
        "version": 1,
        "diagnostic": _json_safe_object(gap),
    }


def _source_backed_use_of_cash_rows(ticker: str) -> list[dict] | None:
    use_of_cash_inputs = use_of_cash_inputs_from_postgres(ticker)
    if use_of_cash_inputs is None:
        return None
    financial_rows, valuation_rows, currency = use_of_cash_inputs
    return build_use_of_cash_rows(
        ticker,
        financial_rows,
        valuation_rows,
        currency=currency,
        data_mode="source_backed",
    )


def _latest_audit_year(rows: list[dict]) -> int:
    if not rows:
        return 0
    return max(int(row["fiscal_year"]) for row in rows)


def _snapshot_fact_source_trace(snapshot: dict, fact_name: str) -> dict:
    source_trace = dict(snapshot.get("source_trace") or {})
    trace_key = {
        "current_price": "price_source_trace",
        "market_cap": "market_cap_source_trace",
        "market_cap_usd": "market_cap_usd_source_trace",
        "listed_shares": "listed_shares_source_trace",
    }.get(fact_name)
    if trace_key:
        specific_trace = source_trace.get(trace_key)
        if isinstance(specific_trace, dict):
            output = dict(specific_trace)
            output.setdefault("quality_status", source_trace.get("quality_status"))
            return output
    return source_trace


def _latest_reported_audit_year(rows: list[dict]) -> int:
    reported_prefixes = (
        "adjusted_earnings.",
        "valuation.",
        "snapshot.",
        "financials.",
        "fun_graphs.",
        "fiscal_fitness.",
        "health_check.",
        "research_report.",
        "performance.",
        "use_of_cash.",
    )
    years = [
        int(row["fiscal_year"])
        for row in rows
        if str(row.get("fact_name") or "").startswith(reported_prefixes)
    ]
    return max(years) if years else _latest_audit_year(rows)


def _with_screener_source_traces(rows: list[dict], data_mode: str) -> list[dict]:
    return [{**row, "source_trace": _screener_row_trace(row, data_mode)} for row in rows]


def _screener_row_trace(row: dict, data_mode: str) -> dict:
    ticker = str(row.get("ticker") or "UNKNOWN").upper()
    base_trace = dict(row.get("source_trace") or {})
    trace = {
        **base_trace,
        "source_type": base_trace.get("source_type")
        or ("postgres_screener" if data_mode == "source_backed" else "fixture_screener"),
        "source_document_id": base_trace.get("source_document_id")
        or f"{ticker.lower()}-screener-row",
        "filing_id": base_trace.get("filing_id") or f"{ticker.lower()}-screener-row",
        "period": base_trace.get("period") or "latest",
        "unit": "screening_metrics",
        "currency": base_trace.get("currency") or row.get("currency") or "mixed",
        "formula": (
            "screener row derived from current P/E, normal P/E, ROE, ROIC, EPS CAGR, "
            "debt/equity, market capitalization, and deterministic filter thresholds"
        ),
        "quality_status": base_trace.get("quality_status")
        or (
            "source_backed_screener"
            if data_mode == "source_backed"
            else "fixture_non_production_screener"
        ),
        "data_mode": data_mode,
    }
    if base_trace:
        trace["input_source_trace"] = base_trace
    return trace


def _append_screener_audit_rows(
    rows: list[dict],
    ticker: str,
    screener_payload: list[dict],
) -> None:
    row = next((item for item in screener_payload if item.get("ticker") == ticker), None)
    if row is None:
        return
    fiscal_year = _latest_reported_audit_year(rows) or date.today().year
    trace = _screener_row_trace(
        row,
        str(row.get("source_trace", {}).get("data_mode") or "fixture_non_production"),
    )
    for fact_name in (
        "market_cap",
        "market_cap_usd",
        "listed_shares",
        "per",
        "normal_pe",
        "roe",
        "roic",
        "eps_cagr",
        "debt_to_equity",
    ):
        fact_trace = _screener_fact_source_trace(row, fact_name, trace)
        rows.append(
            _audit_row(
                ticker=ticker,
                fiscal_year=fiscal_year,
                fact_name=f"screener.{fact_name}",
                value=row.get(fact_name),
                method=fact_trace.get("source_type"),
                policy="screener",
                confidence=None,
                quality_status=fact_trace.get("quality_status"),
                flags=[],
                formula=fact_trace.get("formula"),
                source_trace=fact_trace,
            )
        )


def _screener_fact_source_trace(row: dict, fact_name: str, row_trace: dict) -> dict:
    source_trace = dict(row.get("source_trace") or {})
    trace_key = {
        "market_cap": "market_cap_source_trace",
        "listed_shares": "listed_shares_source_trace",
    }.get(fact_name)
    if trace_key:
        specific_trace = source_trace.get(trace_key)
        if isinstance(specific_trace, dict):
            output = dict(specific_trace)
            output.setdefault("quality_status", row_trace.get("quality_status"))
            output.setdefault("data_mode", row_trace.get("data_mode"))
            return output
    return row_trace


def _append_portfolio_audit_rows(rows: list[dict], ticker: str, portfolio_payload: dict) -> None:
    holding = next(
        (item for item in portfolio_payload.get("holdings", []) if item.get("ticker") == ticker),
        None,
    )
    if holding is None:
        return
    fiscal_year = _latest_reported_audit_year(rows) or date.today().year
    trace = _portfolio_holding_trace(portfolio_payload, holding)
    for fact_name in (
        "quantity",
        "average_cost",
        "latest_price",
        "market_value",
        "unrealized_pnl",
        "weight_pct",
    ):
        rows.append(
            _audit_row(
                ticker=ticker,
                fiscal_year=fiscal_year,
                fact_name=f"portfolio.{fact_name}",
                value=holding.get(fact_name),
                method=trace.get("source_type"),
                policy="portfolio",
                confidence=None,
                quality_status=trace.get("quality_status"),
                flags=[],
                formula=trace.get("formula"),
                source_trace=trace,
            )
        )
    for index, transaction in enumerate(holding.get("transactions") or [], start=1):
        trade_date = str(transaction.get("date") or "")
        side = str(transaction.get("side") or "unknown")
        tx_fiscal_year = _transaction_fiscal_year(trade_date) or fiscal_year
        for fact_name, unit in (
            ("quantity", "shares"),
            ("price", holding.get("currency") or trace.get("currency") or "currency"),
            ("side", "side"),
        ):
            tx_trace = _portfolio_transaction_trace(
                portfolio_payload,
                holding,
                transaction,
                index,
                unit,
            )
            rows.append(
                _audit_row(
                    ticker=ticker,
                    fiscal_year=tx_fiscal_year,
                    fact_name=f"portfolio_transaction.{trade_date}.{side}.{index}.{fact_name}",
                    value=transaction.get(fact_name),
                    method=tx_trace.get("source_type"),
                    policy="portfolio_transaction",
                    confidence=None,
                    quality_status=tx_trace.get("quality_status"),
                    flags=[],
                    formula=tx_trace.get("formula"),
                    source_trace=tx_trace,
                )
            )


def _portfolio_holding_trace(portfolio_payload: dict, holding: dict) -> dict:
    ticker = str(holding.get("ticker") or "UNKNOWN").upper()
    base_trace = dict(
        holding.get("source_trace")
        or portfolio_payload.get("import_trace")
        or portfolio_payload.get("source_trace")
        or {}
    )
    source_document_id = base_trace.get("source_document_id") or "portfolio-source"
    holding_source_document_id = (
        source_document_id
        if base_trace.get("ticker") == ticker or str(source_document_id).endswith(f"-{ticker}")
        else f"{source_document_id}-{ticker}"
    )
    filing_id = base_trace.get("filing_id") or source_document_id
    holding_filing_id = (
        filing_id
        if base_trace.get("ticker") == ticker or str(filing_id).endswith(f"-{ticker}")
        else f"{filing_id}-{ticker}"
    )
    return {
        **base_trace,
        "source_type": base_trace.get("source_type") or "portfolio_derived",
        "source_document_id": holding_source_document_id,
        "filing_id": holding_filing_id,
        "period": portfolio_payload.get("as_of") or base_trace.get("period") or "portfolio_as_of",
        "unit": "portfolio_holding",
        "currency": holding.get("currency") or base_trace.get("currency") or "mixed",
        "formula": (
            "holding quantity, average cost, market value, unrealized P/L, and weight "
            "derived from signed CSV transactions and latest available price"
        ),
        "quality_status": base_trace.get("quality_status") or "portfolio_derived",
        "ticker": ticker,
    }


def _portfolio_transaction_trace(
    portfolio_payload: dict,
    holding: dict,
    transaction: dict,
    index: int,
    unit: object,
) -> dict:
    holding_trace = _portfolio_holding_trace(portfolio_payload, holding)
    trade_date = str(transaction.get("date") or "unknown-date")
    side = str(transaction.get("side") or "unknown")
    source_document_id = holding_trace.get("source_document_id") or "portfolio-source"
    transaction_id = f"{source_document_id}-{trade_date}-{side}-{index}"
    return {
        **holding_trace,
        "source_type": holding_trace.get("source_type") or "portfolio_transaction",
        "source_document_id": transaction_id,
        "filing_id": transaction_id,
        "period": trade_date,
        "unit": str(unit),
        "currency": holding.get("currency") or holding_trace.get("currency") or "mixed",
        "formula": (
            "portfolio transaction parsed from signed CSV; chart overlay marker uses "
            "transaction date, side, quantity, and transaction price"
        ),
        "quality_status": holding_trace.get("quality_status") or "portfolio_transaction",
        "transaction_index": index,
        "transaction_side": side,
        "ticker": holding.get("ticker"),
    }


def _transaction_fiscal_year(raw_date: str) -> int | None:
    try:
        return int(raw_date[:4])
    except (TypeError, ValueError):
        return None


def _append_watchlist_audit_rows(rows: list[dict], ticker: str, watchlist_payload: dict) -> None:
    item = next(
        (row for row in watchlist_payload.get("items", []) if row.get("ticker") == ticker),
        None,
    )
    if item is None:
        return
    fiscal_year = _latest_reported_audit_year(rows) or date.today().year
    trace = _watchlist_item_trace(watchlist_payload, item)
    for fact_name in ("current_price", "per", "dividend_yield", "eps_cagr"):
        rows.append(
            _audit_row(
                ticker=ticker,
                fiscal_year=fiscal_year,
                fact_name=f"watchlist.{fact_name}",
                value=item.get(fact_name),
                method=trace.get("source_type"),
                policy="watchlist",
                confidence=None,
                quality_status=trace.get("quality_status"),
                flags=[],
                formula=trace.get("formula"),
                source_trace=trace,
            )
        )


def _watchlist_item_trace(watchlist_payload: dict, item: dict) -> dict:
    ticker = str(item.get("ticker") or "UNKNOWN").upper()
    base_trace = dict(item.get("source_trace") or watchlist_payload.get("source_trace") or {})
    source_document_id = base_trace.get("source_document_id") or f"{ticker.lower()}-watchlist-item"
    item_source_document_id = (
        source_document_id
        if base_trace.get("ticker") == ticker or str(source_document_id).endswith(f"-{ticker}")
        else f"{source_document_id}-{ticker}"
    )
    filing_id = base_trace.get("filing_id") or source_document_id
    item_filing_id = (
        filing_id
        if base_trace.get("ticker") == ticker or str(filing_id).endswith(f"-{ticker}")
        else f"{filing_id}-{ticker}"
    )
    return {
        **base_trace,
        "source_type": base_trace.get("source_type") or "watchlist_item",
        "source_document_id": item_source_document_id,
        "filing_id": item_filing_id,
        "period": base_trace.get("period") or "current",
        "unit": "watchlist_item",
        "currency": item.get("currency") or base_trace.get("currency") or "mixed",
        "formula": (
            "watchlist item metrics copied from the current company snapshot and preserved "
            "with item-level provenance"
        ),
        "quality_status": base_trace.get("quality_status") or "watchlist_item",
        "ticker": ticker,
    }


@app.get("/api/data-audit/{fact_id}")
def data_audit_fact(
    fact_id: str,
    metric: str = "adjusted_operating",
    forecast_mode: str = "custom",
    forecast_case: str = "median",
    forecast_years: int = 5,
    start_year: int | None = None,
    end_year: int | None = None,
    normal_multiple_years: int | None = None,
    user_growth_rate: Decimal | None = None,
    target_multiple: Decimal | None = None,
    manual_eps_values: str | None = None,
) -> dict:
    ticker = _ticker_from_fact_id(fact_id)
    rows = data_audit(
        ticker,
        metric=metric,
        forecast_mode=forecast_mode,
        forecast_case=forecast_case,
        forecast_years=forecast_years,
        start_year=start_year,
        end_year=end_year,
        normal_multiple_years=normal_multiple_years,
        user_growth_rate=user_growth_rate,
        target_multiple=target_multiple,
        manual_eps_values=manual_eps_values,
    )["data"]
    for row in rows:
        if row["fact_id"] == fact_id:
            return {"data": audit_row_with_trace_sections(row)}
    raise HTTPException(status_code=404, detail="fact audit not found")


def _forecast_return_start(valuation_payload: dict) -> dict:
    historical_rows = [
        row for row in valuation_payload.get("data", []) if not row.get("forecast_flag")
    ]
    if not historical_rows:
        return {}
    latest = historical_rows[-1]
    source_trace = latest.get("source_trace") or {}
    dividend_source_trace = source_trace.get("dividend_source_trace") or {}
    dividend_source_traces = source_trace.get("dividend_source_traces") or []
    if not dividend_source_trace and dividend_source_traces:
        dividend_document_id = (
            f"{source_trace.get('source_document_id', 'unknown')}-dividend-traces"
        )
        dividend_source_trace = {
            "source_type": "dividend_trace_collection",
            "source_document_id": dividend_document_id,
            "filing_id": source_trace.get("filing_id") or "dividend-trace-collection",
            "period": source_trace.get("period") or f"FY{latest.get('fiscal_year')}",
            "unit": "per_share",
            "currency": source_trace.get("currency") or "mixed",
            "formula": "annual dividend input aggregated from dividend source traces",
            "quality_status": source_trace.get("quality_status") or "source_backed",
            "input_traces": dividend_source_traces,
        }
    return {
        "fiscal_year": int(latest["fiscal_year"]),
        "price": latest.get("price"),
        "dividend": latest.get("dividend"),
        "source_trace": source_trace,
        "price_source_trace": source_trace.get("price_source_trace") or {},
        "dividend_source_trace": dividend_source_trace,
    }


def _valuation_fact_trace(
    row: dict,
    fact_name: str,
    forecast_start: dict | None = None,
) -> tuple[dict, str | None]:
    trace = _json_safe_object(dict(row.get("source_trace") or {}))
    formula = trace.get("formula")
    if not row.get("forecast_flag"):
        if fact_name == "metric":
            metric_trace = _valuation_input_trace(trace, "metric_source_trace")
            if metric_trace:
                return metric_trace, metric_trace.get("formula")
        if fact_name == "price":
            price_trace = _valuation_input_trace(trace, "price_source_trace")
            if price_trace:
                return price_trace, price_trace.get("formula")
        if fact_name == "dividend":
            dividend_trace = _valuation_input_trace(trace, "dividend_source_trace")
            if dividend_trace:
                return dividend_trace, dividend_trace.get("formula")
        return trace, formula

    forecast_year = None
    if forecast_start and forecast_start.get("fiscal_year") is not None:
        forecast_year = max(1, int(row["fiscal_year"]) - int(forecast_start["fiscal_year"]))
    target_price = row.get("price")
    annual_dividend = row.get("dividend")
    start_price = (forecast_start or {}).get("price")
    start_price_trace = (forecast_start or {}).get("price_source_trace") or {}
    dividend_trace = (forecast_start or {}).get("dividend_source_trace") or {}

    if fact_name == "price":
        formula = "target_price = forecast metric * selected target multiple"
        trace["formula"] = formula
        trace["unit"] = trace.get("currency") or "currency"
    elif fact_name == "fair_value_price":
        formula = "fair_value_price = forecast metric * selected fair multiple"
        trace["formula"] = formula
        trace["unit"] = trace.get("currency") or "currency"
    elif fact_name == "price_cagr_pct":
        formula = (
            "price_cagr_pct = (((target_price / start_price) ** "
            "(1 / forecast_year)) - 1) * 100"
        )
        trace.update(
            {
                "formula": formula,
                "unit": "percent",
                "calculation_inputs": {
                    "start_price": start_price,
                    "target_price": target_price,
                    "forecast_year": forecast_year,
                    "start_price_trace": start_price_trace,
                },
            }
        )
    elif fact_name == "total_return_cagr_pct":
        formula = (
            "total_return_cagr_pct = ((((target_price + annual_dividend * "
            "forecast_year) / start_price) ** (1 / forecast_year)) - 1) * 100"
        )
        trace.update(
            {
                "formula": formula,
                "unit": "percent",
                "calculation_inputs": {
                    "start_price": start_price,
                    "target_price": target_price,
                    "annual_dividend": annual_dividend,
                    "forecast_year": forecast_year,
                    "start_price_trace": start_price_trace,
                    "dividend_trace": dividend_trace,
                },
            }
        )
    elif fact_name == "margin_of_safety_pct":
        formula = "margin_of_safety_pct = ((target_price - start_price) / target_price) * 100"
        trace.update(
            {
                "formula": formula,
                "unit": "percent",
                "calculation_inputs": {
                    "start_price": start_price,
                    "target_price": target_price,
                    "forecast_year": forecast_year,
                    "start_price_trace": start_price_trace,
                },
            }
        )
    return trace, formula


def _valuation_input_trace(trace: dict, key: str) -> dict | None:
    nested_trace = trace.get(key)
    if isinstance(nested_trace, dict) and nested_trace:
        return _json_safe_object(dict(nested_trace))
    metadata = trace.get("metadata")
    if isinstance(metadata, dict):
        nested_trace = metadata.get(key)
        if isinstance(nested_trace, dict) and nested_trace:
            return _json_safe_object(dict(nested_trace))
    return None


def _audit_row(
    ticker: str,
    fiscal_year: int,
    fact_name: str,
    value,
    method,
    policy,
    confidence,
    quality_status,
    flags: list,
    formula,
    source_trace: dict,
) -> dict:
    return {
        "fact_id": _fact_id(ticker, fiscal_year, fact_name),
        "fact_name": fact_name,
        "value": str(value) if value is not None else None,
        "fiscal_year": fiscal_year,
        "method": str(method) if method is not None else "source_trace",
        "policy": str(policy) if policy is not None else "audit",
        "confidence": str(confidence) if confidence is not None else None,
        "quality_status": str(quality_status or source_trace.get("quality_status") or "warning"),
        "flags": flags,
        "formula": formula or source_trace.get("formula"),
        "source_trace": source_trace,
    }


def _append_chart_key_audit_rows(rows: list[dict], ticker: str, valuation_payload: dict) -> None:
    historical_rows = [
        row for row in valuation_payload.get("data", []) if not row.get("forecast_flag")
    ]
    if not historical_rows:
        return
    latest = historical_rows[-1]
    fiscal_year = int(latest["fiscal_year"])
    price = _decimal_or_none(latest.get("price"))
    metric = _decimal_or_none(latest.get("metric"))
    dividend = _decimal_or_none(latest.get("dividend"))
    base_trace = dict(latest.get("source_trace") or {})

    derived_facts = []
    if price is not None and metric is not None and metric != 0:
        derived_facts.append(
            (
                "current_multiple",
                (price / metric).quantize(Decimal("0.01")),
                "multiple",
                "current_multiple = price / selected valuation metric",
            )
        )
    if dividend is not None and metric is not None and metric != 0:
        derived_facts.append(
            (
                "payout_ratio_pct",
                ((dividend / metric) * Decimal("100")).quantize(Decimal("0.01")),
                "percent",
                "payout_ratio_pct = dividend / selected valuation metric * 100",
            )
        )
    if dividend is not None and price is not None and price != 0:
        derived_facts.append(
            (
                "dividend_yield_pct",
                ((dividend / price) * Decimal("100")).quantize(Decimal("0.01")),
                "percent",
                "dividend_yield_pct = dividend / price * 100",
            )
        )

    for suffix, value, unit, formula in derived_facts:
        trace = {
            **base_trace,
            "source_type": base_trace.get("source_type") or "chart_key_derived",
            "source_document_id": base_trace.get("source_document_id")
            or f"{ticker.lower()}-chart-key",
            "filing_id": base_trace.get("filing_id") or f"{ticker.lower()}-chart-key",
            "period": base_trace.get("period") or f"FY{fiscal_year}",
            "unit": unit,
            "currency": base_trace.get("currency") or "mixed",
            "formula": formula,
            "quality_status": base_trace.get("quality_status") or "chart_key_derived",
        }
        rows.append(
            _audit_row(
                ticker=ticker,
                fiscal_year=fiscal_year,
                fact_name=f"chart_key.{suffix}",
                value=value,
                method="chart_key_derived",
                policy="chart_key",
                confidence=None,
                quality_status=trace.get("quality_status"),
                flags=[],
                formula=formula,
                source_trace=trace,
            )
        )

    forecast_meta = (valuation_payload.get("meta") or {}).get("forecast") or {}
    custom_multiple = _decimal_or_none(forecast_meta.get("target_multiple"))
    if custom_multiple is None:
        return
    forecast_trace = dict(forecast_meta.get("source_trace") or base_trace)
    formula = "custom_multiple = selected forecast target multiple"
    trace = {
        **forecast_trace,
        "source_type": forecast_trace.get("source_type")
        or forecast_meta.get("source")
        or "forecast_assumption",
        "source_document_id": forecast_trace.get("source_document_id")
        or f"{ticker.lower()}-forecast-assumption",
        "filing_id": forecast_trace.get("filing_id") or f"{ticker.lower()}-forecast-assumption",
        "period": forecast_trace.get("period") or f"FY{fiscal_year}",
        "unit": "multiple",
        "currency": forecast_trace.get("currency") or base_trace.get("currency") or "mixed",
        "formula": formula,
        "quality_status": forecast_trace.get("quality_status")
        or forecast_meta.get("source")
        or "forecast_assumption",
    }
    rows.append(
        _audit_row(
            ticker=ticker,
            fiscal_year=fiscal_year,
            fact_name="chart_key.custom_multiple",
            value=custom_multiple.quantize(Decimal("0.01")),
            method="chart_key_derived",
            policy="chart_key",
            confidence=None,
            quality_status=trace.get("quality_status"),
            flags=[],
            formula=formula,
            source_trace=trace,
        )
    )


def _append_price_point_audit_rows(rows: list[dict], ticker: str, valuation_payload: dict) -> None:
    meta = valuation_payload.get("meta") or {}
    price_points = meta.get("price_points") or []
    price_points_meta = meta.get("price_points_meta") or {}
    for point in price_points:
        close_price = point.get("close_price")
        point_date = str(point.get("date") or "")
        if close_price is None or not point_date:
            continue
        fiscal_year = _price_point_fiscal_year(point, point_date)
        trace = _json_safe_object(dict(point.get("source_trace") or {}))
        trace.setdefault("source_type", "price_point")
        trace.setdefault("source_document_id", f"{ticker.lower()}-{point_date}-price")
        trace.setdefault("filing_id", f"{ticker.lower()}-{point_date}-price")
        trace.setdefault("period", point_date)
        trace.setdefault("unit", "close_price")
        trace.setdefault("currency", point.get("currency") or "mixed")
        trace.setdefault("formula", "close_price = source price observation close")
        trace.setdefault(
            "quality_status",
            point.get("quality_status")
            or price_points_meta.get("quality_status")
            or "source_backed_price_point",
        )
        trace.setdefault("frequency", point.get("frequency") or price_points_meta.get("frequency"))
        trace["price_point_date"] = point_date
        rows.append(
            _audit_row(
                ticker=ticker,
                fiscal_year=fiscal_year,
                fact_name=f"price_point.close_price.{point_date}",
                value=close_price,
                method=trace.get("source_type"),
                policy="price_points",
                confidence=None,
                quality_status=trace.get("quality_status"),
                flags=[],
                formula=trace.get("formula"),
                source_trace=trace,
            )
        )


def _price_point_fiscal_year(point: dict, point_date: str) -> int:
    try:
        return int(point.get("fiscal_year"))
    except (TypeError, ValueError):
        pass
    try:
        return int(point_date[:4])
    except (TypeError, ValueError):
        return 0


def _append_forecast_scenario_audit_rows(
    rows: list[dict],
    ticker: str,
    valuation_payload: dict,
) -> None:
    forecast_meta = (valuation_payload.get("meta") or {}).get("forecast") or {}
    calculation_lines = forecast_meta.get("calculation_lines") or []
    if not calculation_lines:
        return
    forecast_rows = {
        int(row["fiscal_year"]): row
        for row in valuation_payload.get("data", [])
        if row.get("forecast_flag") and row.get("fiscal_year") is not None
    }
    forecast_trace = _json_safe_object(dict(forecast_meta.get("source_trace") or {}))
    formula = "scenario_target_price = forecast metric * scenario multiple"
    for line in calculation_lines:
        label = str(line.get("label") or "")
        multiple = line.get("multiple")
        if not label or multiple is None:
            continue
        for point in line.get("points") or []:
            if point.get("fiscal_year") is None or point.get("target_price") is None:
                continue
            fiscal_year = int(point["fiscal_year"])
            forecast_row = forecast_rows.get(fiscal_year) or {}
            row_trace = _json_safe_object(dict(forecast_row.get("source_trace") or {}))
            base_trace = row_trace or forecast_trace
            trace = {
                **base_trace,
                "source_type": base_trace.get("source_type")
                or forecast_meta.get("source")
                or "forecast_scenario",
                "source_document_id": base_trace.get("source_document_id")
                or f"{ticker.lower()}-{fiscal_year}-forecast-scenario",
                "filing_id": base_trace.get("filing_id")
                or f"{ticker.lower()}-{fiscal_year}-forecast-scenario",
                "period": base_trace.get("period") or f"FY{fiscal_year}E",
                "unit": "currency",
                "currency": base_trace.get("currency") or "mixed",
                "formula": formula,
                "quality_status": base_trace.get("quality_status")
                or forecast_meta.get("source")
                or "forecast_scenario",
                "scenario_multiple": str(multiple),
                "scenario_label": label,
                "forecast_metric": forecast_row.get("metric"),
                "forecast_metric_trace": row_trace,
            }
            rows.append(
                _audit_row(
                    ticker=ticker,
                    fiscal_year=fiscal_year,
                    fact_name=f"forecast_scenario.{label}.target_price",
                    value=point.get("target_price"),
                    method="forecast_scenario_derived",
                    policy="forecast_scenario",
                    confidence=None,
                    quality_status=trace.get("quality_status"),
                    flags=[],
                    formula=formula,
                    source_trace=trace,
                )
            )


def _decimal_or_none(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _forecast_margin_of_safety_pct(
    start_price: Decimal | str | int | float | None,
    target_price: Decimal | str | int | float | None,
) -> Decimal | None:
    start = _decimal_or_none(start_price)
    target = _decimal_or_none(target_price)
    if start is None or target is None or target <= 0:
        return None
    return (((target - start) / target) * Decimal("100")).quantize(Decimal("0.01"))


def _forecast_case_cagr_pct(
    start_price: Decimal | None,
    end_value: Decimal | None,
    years: int,
) -> Decimal | None:
    if start_price is None or end_value is None or start_price <= 0 or end_value <= 0 or years <= 0:
        return None
    return (
        (((end_value / start_price) ** (Decimal("1") / Decimal(years))) - Decimal("1"))
        * Decimal("100")
    ).quantize(Decimal("0.01"))


def _append_forecast_case_comparison_audit_rows(
    rows: list[dict],
    ticker: str,
    valuation_payload: dict,
    forecast_evidence: dict,
) -> None:
    forecast_meta = (valuation_payload.get("meta") or {}).get("forecast") or {}
    forecast_start = _forecast_return_start(valuation_payload)
    start_price = _decimal_or_none(forecast_start.get("price"))
    target_multiple = _decimal_or_none(forecast_meta.get("target_multiple"))
    if start_price is None or target_multiple is None:
        return

    start_year = int(forecast_start.get("fiscal_year") or 0)
    fiscal_year = int(forecast_evidence.get("forecast_year") or start_year + 1)
    forecast_year = max(1, fiscal_year - start_year) if start_year else 1
    annual_dividend = _decimal_or_none(forecast_start.get("dividend")) or Decimal("0")
    forecast_trace = _json_safe_object(dict(forecast_meta.get("source_trace") or {}))
    start_price_trace = _json_safe_object(dict(forecast_start.get("price_source_trace") or {}))
    dividend_trace = _json_safe_object(dict(forecast_start.get("dividend_source_trace") or {}))
    currency = (
        forecast_trace.get("currency")
        or start_price_trace.get("currency")
        or forecast_start.get("source_trace", {}).get("currency")
        or "mixed"
    )
    quality_status = (
        forecast_meta.get("consensus", {}).get("quality_status")
        or forecast_evidence.get("meta", {}).get("quality_status")
        or forecast_trace.get("quality_status")
        or "forecast_case_comparison"
    )
    formulas = {
        "target_price": "forecast_case_target_price = estimate_eps * target_multiple",
        "price_cagr_pct": (
            "forecast_case_price_cagr_pct = (((target_price / start_price) ** "
            "(1 / forecast_year)) - 1) * 100"
        ),
        "total_return_cagr_pct": (
            "forecast_case_total_return_cagr_pct = ((((target_price + annual_dividend * "
            "forecast_year) / start_price) ** (1 / forecast_year)) - 1) * 100"
        ),
        "margin_of_safety_pct": (
            "forecast_case_margin_of_safety_pct = ((target_price - start_price) / "
            "target_price) * 100"
        ),
    }

    for case_row in forecast_evidence.get("cases") or []:
        case_name = str(case_row.get("case") or "")
        estimate_eps = _decimal_or_none(case_row.get("estimate_eps"))
        if not case_name or estimate_eps is None:
            continue
        target_price = (estimate_eps * target_multiple).quantize(Decimal("0.01"))
        cumulative_dividend = annual_dividend * Decimal(forecast_year)
        values = {
            "target_price": target_price,
            "price_cagr_pct": _forecast_case_cagr_pct(start_price, target_price, forecast_year),
            "total_return_cagr_pct": _forecast_case_cagr_pct(
                start_price,
                target_price + cumulative_dividend,
                forecast_year,
            ),
            "margin_of_safety_pct": _forecast_margin_of_safety_pct(start_price, target_price),
        }
        case_trace = _json_safe_object(
            dict(case_row.get("source_trace") or forecast_evidence.get("source_trace") or {})
        )
        base_trace = {
            **case_trace,
            "source_type": "forecast_case_comparison",
            "source_document_id": case_trace.get("source_document_id")
            or f"{ticker.lower()}-{fiscal_year}-forecast-case-{case_name}",
            "filing_id": case_trace.get("filing_id")
            or f"{ticker.lower()}-{fiscal_year}-forecast-case-{case_name}",
            "period": case_trace.get("period") or f"FY{fiscal_year}E",
            "currency": currency,
            "method": "deterministic_forecast_case",
            "quality_status": case_trace.get("quality_status") or quality_status,
            "forecast_case": case_name,
            "calculation_inputs": {
                "estimate_eps": str(estimate_eps),
                "growth_rate_pct": case_row.get("growth_rate_pct"),
                "target_multiple": str(target_multiple),
                "start_price": str(start_price),
                "annual_dividend": str(annual_dividend),
                "forecast_year": forecast_year,
            },
            "input_source_traces": {
                "forecast_snapshot_trace": case_trace,
                "forecast_assumption_trace": forecast_trace,
                "start_price_trace": start_price_trace,
                "dividend_trace": dividend_trace,
            },
            "forecast_snapshot_trace": case_trace,
            "forecast_assumption_trace": forecast_trace,
            "start_price_trace": start_price_trace,
            "dividend_trace": dividend_trace,
        }
        for fact_name, value in values.items():
            if value is None:
                continue
            trace = {
                **base_trace,
                "unit": "currency" if fact_name == "target_price" else "percent",
                "formula": formulas[fact_name],
            }
            rows.append(
                _audit_row(
                    ticker=ticker,
                    fiscal_year=fiscal_year,
                    fact_name=f"forecast_case.{case_name}.{fact_name}",
                    value=value,
                    method="forecast_case_derived",
                    policy="forecast_case",
                    confidence=None,
                    quality_status=trace.get("quality_status"),
                    flags=[],
                    formula=formulas[fact_name],
                    source_trace=trace,
                )
            )


def _append_forecast_assumption_audit_rows(
    rows: list[dict],
    ticker: str,
    valuation_payload: dict,
) -> None:
    forecast_meta = (valuation_payload.get("meta") or {}).get("forecast") or {}
    if not forecast_meta:
        return

    forecast_years = [
        int(row["fiscal_year"])
        for row in valuation_payload.get("data", [])
        if row.get("forecast_flag")
    ]
    if forecast_years:
        period = f"FY{min(forecast_years)}-FY{max(forecast_years)}"
        fiscal_year = max(forecast_years)
    else:
        period = "forecast_assumption"
        fiscal_year = int(
            (valuation_payload.get("meta") or {}).get("range", {}).get("end_year") or 0
        )

    base_trace = _json_safe_object(dict(forecast_meta.get("source_trace") or {}))
    base_trace.setdefault("source_document_id", f"{ticker.lower()}-forecast-assumption")
    base_trace.setdefault("filing_id", f"{ticker.lower()}-forecast-assumption")
    base_trace.setdefault("period", period)
    base_trace.setdefault("unit", "forecast_assumption")
    base_trace.setdefault("currency", "mixed")
    base_trace.setdefault("formula", forecast_meta.get("formula") or base_trace.get("formula"))
    base_trace.setdefault(
        "quality_status",
        (
            forecast_meta.get("consensus", {}).get("quality_status")
            or base_trace.get("consensus_quality_status")
            or base_trace.get("quality_status")
            or "forecast_assumption"
        ),
    )

    manual_eps_values = forecast_meta.get("manual_eps_values") or []
    manual_override_count = len([value for value in manual_eps_values if value not in (None, "")])
    assumption_rows = [
        ("mode", forecast_meta.get("mode")),
        ("case", forecast_meta.get("case")),
        ("growth_rate_pct", forecast_meta.get("growth_rate_pct")),
        ("target_multiple", forecast_meta.get("target_multiple")),
        ("analyst_count", forecast_meta.get("analyst_count")),
        ("manual_eps_override_count", manual_override_count),
        ("formula", forecast_meta.get("formula")),
        ("source", forecast_meta.get("source")),
    ]
    method = forecast_meta.get("source") or base_trace.get("source_type") or "forecast_assumption"
    for suffix, value in assumption_rows:
        if value is None:
            continue
        rows.append(
            _audit_row(
                ticker=ticker,
                fiscal_year=fiscal_year,
                fact_name=f"forecast_assumption.{suffix}",
                value=value,
                method=method,
                policy="forecast_assumption",
                confidence=None,
                quality_status=base_trace.get("quality_status"),
                flags=[],
                formula=base_trace.get("formula"),
                source_trace=base_trace,
            )
        )


def _append_analyst_scorecard_audit_rows(
    rows: list[dict],
    ticker: str,
    scorecard: dict,
) -> None:
    for row in scorecard.get("rows", []):
        fiscal_year = int(row["fiscal_year"])
        for fact_name in (
            "actual_eps",
            "estimate_1y_prior",
            "estimate_2y_prior",
            "error_1y_pct",
            "error_2y_pct",
            "result_1y",
            "result_2y",
        ):
            rows.append(
                _audit_row(
                    ticker=ticker,
                    fiscal_year=fiscal_year,
                    fact_name=f"analyst_scorecard.{fact_name}",
                    value=row.get(fact_name),
                    method="analyst_scorecard_derived",
                    policy="analyst_scorecard",
                    confidence=None,
                    quality_status=row["source_trace"].get("quality_status"),
                    flags=row.get("flags", []),
                    formula=row["source_trace"].get("formula"),
                    source_trace=row["source_trace"],
                )
            )
    summary = scorecard.get("summary") or {}
    summary_year = max(
        (int(row["fiscal_year"]) for row in scorecard.get("rows", [])),
        default=_latest_audit_year(rows),
    )
    for fact_name in ("hit_rate_1y_pct", "hit_rate_2y_pct"):
        rows.append(
            _audit_row(
                ticker=ticker,
                fiscal_year=summary_year,
                fact_name=f"analyst_scorecard.{fact_name}",
                value=summary.get(fact_name),
                method="analyst_scorecard_derived",
                policy="analyst_scorecard",
                confidence=None,
                quality_status=scorecard["source_trace"].get("quality_status"),
                flags=scorecard.get("flags", []),
                formula=scorecard["source_trace"].get("formula"),
                source_trace=scorecard["source_trace"],
            )
        )


def _fact_id(ticker: str, fiscal_year: int, fact_name: str) -> str:
    safe_fact = fact_name.replace(" ", "_").replace("/", "_")
    return f"{ticker}-{fiscal_year}-{safe_fact}"


def _ticker_from_fact_id(fact_id: str) -> str:
    for ticker in SAMPLE_SECURITY_META:
        if fact_id.startswith(f"{ticker}-"):
            return ticker
    return fact_id.split("-", 1)[0].upper()


def _historical_growth_default(series: list) -> Decimal:
    if len(series) < 2:
        return Decimal("5")
    first = next((row for row in series if row.metric > 0), series[0])
    last = series[-1]
    years = max(1, last.fiscal_year - first.fiscal_year)
    if first.metric <= 0 or last.metric <= 0:
        return Decimal("5")
    return ((((last.metric / first.metric) ** (Decimal("1") / Decimal(years))) - 1) * 100).quantize(
        Decimal("0.01")
    )


def _forecast_assumption_source_trace(
    *,
    ticker: str,
    latest,
    source: ForecastSource,
    mode: str,
    forecast_case: str,
    forecast_years: int,
    formula: str,
    consensus: dict,
    consensus_projection: dict | None,
    historical_growth: Decimal,
    consensus_growth: Decimal,
    target_multiple: Decimal,
) -> dict:
    latest_trace = _json_safe_object(dict(latest.source_trace or {}))
    consensus_trace = _json_safe_object(
        dict((consensus_projection or {}).get("source_trace") or {})
    )
    base_trace = dict(consensus_trace or latest_trace)
    source_value = source.value
    forecast_start = latest.fiscal_year + 1
    forecast_end = latest.fiscal_year + forecast_years
    source_document_id = f"{ticker.lower()}-{forecast_start}-forecast-assumption"
    filing_id = f"{ticker.lower()}-{forecast_start}-forecast-assumption"
    if source == ForecastSource.USER_INPUT:
        quality_status = "user_input_forecast_assumption"
    elif source == ForecastSource.DETERMINISTIC_TREND:
        quality_status = "deterministic_forecast_assumption"
    elif source == ForecastSource.AI_ASSISTED_REVIEW:
        quality_status = consensus.get("quality_status") or "deterministic_ai_review_forecast"
    else:
        quality_status = consensus.get("quality_status") or base_trace.get("quality_status")

    return {
        **base_trace,
        "source": source_value,
        "source_type": source_value,
        "source_document_id": source_document_id,
        "filing_id": filing_id,
        "period": f"FY{forecast_start}E-FY{forecast_end}E",
        "available_at": base_trace.get("available_at") or latest_trace.get("available_at"),
        "unit": "forecast_assumption",
        "currency": base_trace.get("currency") or latest_trace.get("currency") or "mixed",
        "method": (
            "deterministic_ai_review"
            if source == ForecastSource.AI_ASSISTED_REVIEW
            else f"forecast_{mode}"
        ),
        "formula": formula,
        "quality_status": quality_status or "forecast_assumption",
        "forecast_mode": mode,
        "forecast_case": forecast_case,
        "forecast_years": forecast_years,
        "llm_generated_numbers": False,
        "ai_role": "commentary_only" if source == ForecastSource.AI_ASSISTED_REVIEW else "not_used",
        "consensus_quality_status": consensus.get("quality_status"),
        "consensus_revision_status": consensus.get("revision_status"),
        "calculation_inputs": {
            "historical_growth_rate_pct": str(historical_growth),
            "consensus_growth_rate_pct": str(consensus_growth),
            "target_multiple": str(target_multiple),
            "latest_metric": str(latest.metric),
            "latest_price": str(latest.price),
            "latest_fiscal_year": latest.fiscal_year,
        },
        "input_source_traces": {
            "latest_metric": latest_trace,
            "consensus": consensus_trace,
        },
        "input_source_document_ids": [
            value
            for value in {
                latest_trace.get("source_document_id"),
                consensus_trace.get("source_document_id"),
            }
            if value
        ],
    }


def _normal_multiple_window(value: int | None) -> int | None:
    if value is None:
        return None
    return max(1, min(20, int(value)))


def _year_range(start_year: int | None, end_year: int | None) -> tuple[int | None, int | None]:
    start = int(start_year) if start_year is not None else None
    end = int(end_year) if end_year is not None else None
    if start is not None and end is not None and start > end:
        return end, start
    return start, end


def _forecast_config(
    ticker: str,
    mode: str,
    forecast_case: str,
    historical: list,
    forecast_years: int,
    user_growth_rate: Decimal | None,
    target_multiple: Decimal | None,
    consensus_projection: dict | None = None,
) -> dict:
    latest = historical[-1]
    presets = FORECAST_PRESETS.get(
        ticker,
        {
            "consensus_low_growth_rate": "5",
            "consensus_growth_rate": "5",
            "consensus_high_growth_rate": "5",
            "lt_growth_rate": "5",
            "analyst_count": 0,
        },
    )
    normalized_mode = mode.lower().replace("-", "_")
    normalized_case = _normalize_forecast_case(forecast_case)
    projection_metrics = _consensus_projection_metrics(consensus_projection)
    projection_growth = _consensus_projection_growth(consensus_projection)
    consensus_growth = projection_growth or _consensus_growth_for_case(presets, normalized_case)
    consensus = _forecast_consensus_meta(
        presets,
        normalized_case,
        consensus_growth,
        consensus_projection,
    )
    historical_growth = _historical_growth_default(historical)
    if normalized_mode in {"estimates", "consensus"}:
        growth = consensus_growth
        source = ForecastSource.CONSENSUS_SNAPSHOT
        multiple = target_multiple or latest.fair_multiple
    elif normalized_mode == "normal_multiple":
        growth = consensus_growth
        source = ForecastSource.CONSENSUS_SNAPSHOT
        multiple = target_multiple or latest.normal_multiple or latest.fair_multiple
    elif normalized_mode == "lt_growth":
        growth = Decimal(presets["lt_growth_rate"])
        source = ForecastSource.CONSENSUS_SNAPSHOT
        multiple = target_multiple or latest.fair_multiple
    elif normalized_mode == "historical_cagr":
        growth = historical_growth
        source = ForecastSource.DETERMINISTIC_TREND
        multiple = target_multiple or latest.fair_multiple
    elif normalized_mode in {"ai_review", "ai_assisted", "ai_assisted_review"}:
        growth = ((historical_growth + consensus_growth) / Decimal("2")).quantize(Decimal("0.01"))
        source = ForecastSource.AI_ASSISTED_REVIEW
        multiple = target_multiple or latest.fair_multiple
        normalized_mode = "ai_review"
    else:
        growth = user_growth_rate if user_growth_rate is not None else historical_growth
        source = (
            ForecastSource.USER_INPUT
            if normalized_mode == "custom" or user_growth_rate is not None
            else ForecastSource.DETERMINISTIC_TREND
        )
        multiple = target_multiple or latest.fair_multiple
        normalized_mode = (
            "custom"
            if normalized_mode == "custom" or user_growth_rate is not None
            else "historical_cagr"
        )
    formula = "metric_t = metric_0 * (1 + growth)^t; target_price = metric_t * target_multiple"
    if normalized_mode == "custom":
        formula = "custom EPS override when provided; missing years use growth from previous metric"
    if normalized_mode == "ai_review":
        formula = (
            "deterministic review blend: (historical_cagr + consensus_growth_rate) / 2; "
            "no LLM-generated numbers"
        )
    source_trace = _forecast_assumption_source_trace(
        ticker=ticker,
        latest=latest,
        source=source,
        mode=normalized_mode,
        forecast_case=normalized_case,
        forecast_years=forecast_years,
        formula=formula,
        consensus=consensus,
        consensus_projection=consensus_projection,
        historical_growth=historical_growth,
        consensus_growth=consensus_growth,
        target_multiple=multiple,
    )
    return {
        "mode": normalized_mode,
        "case": normalized_case,
        "growth": growth,
        "multiple": multiple,
        "source": source,
        "analyst_count": (
            consensus["analyst_count"]
            if source in {ForecastSource.CONSENSUS_SNAPSHOT, ForecastSource.AI_ASSISTED_REVIEW}
            else None
        ),
        "consensus": consensus,
        "consensus_metric_values": (
            projection_metrics
            if normalized_mode in {"estimates", "consensus", "normal_multiple"}
            else []
        ),
        "source_trace": source_trace,
        "source_traces_by_year": (consensus_projection or {}).get("source_traces_by_year", {}),
        "formula": formula,
    }


def _normalize_forecast_case(forecast_case: str) -> str:
    normalized = forecast_case.lower().replace("-", "_")
    if normalized in {"low", "bear", "pessimistic"}:
        return "low"
    if normalized in {"high", "bull", "optimistic"}:
        return "high"
    return "median"


def _consensus_growth_for_case(presets: dict, forecast_case: str) -> Decimal:
    key = {
        "low": "consensus_low_growth_rate",
        "high": "consensus_high_growth_rate",
    }.get(forecast_case, "consensus_growth_rate")
    return Decimal(str(presets.get(key) or presets.get("consensus_growth_rate") or "5"))


def _forecast_consensus_meta(
    presets: dict,
    forecast_case: str,
    selected_growth: Decimal,
    consensus_projection: dict | None = None,
) -> dict:
    analyst_count = int(
        (consensus_projection or {}).get("analyst_count")
        or presets.get("analyst_count")
        or 0
    )
    if consensus_projection:
        return {
            "case": forecast_case,
            "selected_growth_rate_pct": str(selected_growth),
            "low_growth_rate_pct": None,
            "median_growth_rate_pct": str(selected_growth),
            "high_growth_rate_pct": None,
            "lt_growth_rate_pct": None,
            "analyst_count": analyst_count,
            "quality_status": consensus_projection.get(
                "quality_status",
                "source_backed_consensus_snapshots",
            ),
            "revision_status": "point_in_time_snapshot_loaded",
            "missing_years": consensus_projection.get("missing_years", []),
            "source_note": consensus_projection.get(
                "source_note",
                "point-in-time consensus estimate snapshots loaded from Postgres",
            ),
        }
    quality_status = (
        "fixture_non_production_consensus_proxy"
        if analyst_count > 0
        else "no_verified_consensus_snapshot"
    )
    return {
        "case": forecast_case,
        "selected_growth_rate_pct": str(selected_growth),
        "low_growth_rate_pct": str(
            presets.get("consensus_low_growth_rate") or presets.get("consensus_growth_rate")
        ),
        "median_growth_rate_pct": str(presets.get("consensus_growth_rate")),
        "high_growth_rate_pct": str(
            presets.get("consensus_high_growth_rate") or presets.get("consensus_growth_rate")
        ),
        "lt_growth_rate_pct": str(presets.get("lt_growth_rate")),
        "analyst_count": analyst_count,
        "quality_status": quality_status,
        "revision_status": "pending_point_in_time_snapshots",
        "source_note": (
            "fixture consensus proxy for UI and contract testing; replace with "
            "point-in-time vendor or user-entered estimates"
            if analyst_count > 0
            else "no source-backed consensus snapshot loaded for this market yet"
        ),
    }


def _consensus_projection_growth(consensus_projection: dict | None) -> Decimal | None:
    if not consensus_projection or consensus_projection.get("growth_rate_pct") is None:
        return None
    return Decimal(str(consensus_projection["growth_rate_pct"]))


def _consensus_projection_metrics(
    consensus_projection: dict | None,
) -> list[Decimal | None]:
    if not consensus_projection:
        return []
    values: list[Decimal | None] = []
    for value in consensus_projection.get("metric_values") or []:
        values.append(Decimal(str(value)) if value is not None else None)
    return values


def _validate_forecast_query_inputs(
    *,
    forecast_years: int,
    user_growth_rate: Decimal | None,
    target_multiple: Decimal | None,
    custom_valuation_multiple: Decimal | None,
    manual_eps_values: str | None,
) -> None:
    if forecast_years < 1 or forecast_years > 5:
        raise HTTPException(status_code=400, detail="forecast_years must be between 1 and 5")
    _validate_decimal_range(
        user_growth_rate,
        "user_growth_rate",
        minimum=Decimal("-100"),
        maximum=Decimal("200"),
    )
    _validate_decimal_range(
        target_multiple,
        "target_multiple",
        minimum=Decimal("0.1"),
        maximum=Decimal("200"),
    )
    _validate_decimal_range(
        custom_valuation_multiple,
        "custom_valuation_multiple",
        minimum=Decimal("0.1"),
        maximum=Decimal("200"),
    )
    if manual_eps_values:
        for index, item in enumerate(manual_eps_values.split(",")[:forecast_years], start=1):
            clean = item.strip()
            if not clean:
                continue
            try:
                value = Decimal(clean)
            except (InvalidOperation, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail=f"manual_eps_values item {index} must be a decimal number",
                ) from None
            _validate_decimal_range(
                value,
                f"manual_eps_values item {index}",
                minimum=Decimal("0.0001"),
                maximum=Decimal("1000000"),
            )


def _validate_decimal_range(
    value: Decimal | None,
    name: str,
    *,
    minimum: Decimal,
    maximum: Decimal,
) -> None:
    if value is None:
        return
    if value.is_nan() or value.is_infinite() or value < minimum or value > maximum:
        raise HTTPException(
            status_code=400,
            detail=f"{name} must be between {minimum} and {maximum}",
        )


def _parse_manual_metrics(raw: str | None, years: int) -> list[Decimal | None]:
    if not raw:
        return [None for _ in range(years)]
    values: list[Decimal | None] = []
    for item in raw.split(",")[:years]:
        clean = item.strip()
        if not clean:
            values.append(None)
            continue
        try:
            values.append(Decimal(clean))
        except (InvalidOperation, ValueError):
            raise HTTPException(
                status_code=400,
                detail="manual_eps_values must contain only decimal numbers",
            ) from None
    while len(values) < years:
        values.append(None)
    return values


def _parse_hidden_scenario_lines(raw: str | list[str] | None) -> list[str]:
    if not raw:
        return []
    values = raw if isinstance(raw, list) else str(raw).split(",")
    return [item.strip() for item in values if item and item.strip()]


def _portfolio_payload(csv_text: str) -> dict:
    transactions = parse_transactions_csv(csv_text)
    latest_prices = {
        ticker: price_dividend_for(
            ticker,
            max(price_year for price_year in SAMPLE_SECURITY_META_PRICE_YEARS[ticker]),
        )[0]
        for ticker in {transaction.ticker for transaction in transactions}
        if ticker in SAMPLE_SECURITY_META_PRICE_YEARS
    }
    summary = build_portfolio_summary(transactions, latest_prices)
    source = "user_csv" if csv_text != PORTFOLIO_FIXTURE_CSV else "fixture_non_production"
    source_document_id = (
        "portfolio-import-user-csv" if source == "user_csv" else "portfolio-fixture-csv"
    )
    quality_status = (
        "user_provided" if source == "user_csv" else "fixture_non_production_portfolio"
    )
    summary["import_trace"] = {
        "source_type": source,
        "source_document_id": source_document_id,
        "filing_id": source_document_id,
        "period": "user_import",
        "unit": "portfolio_transactions",
        "currency": "mixed",
        "formula": "holdings from signed CSV transactions and latest market value",
        "quality_status": quality_status,
        "rows": len(transactions),
    }
    summary["source_trace"] = summary["import_trace"]
    for holding in summary["holdings"]:
        holding["source_trace"] = _portfolio_holding_trace(summary, holding)
    return {
        "data": _json_safe_object(summary),
        "meta": {
            "source": source,
            "formula": (
                "holdings from signed CSV transactions; XIRR from dated cash flows "
                "and latest market value"
            ),
        },
    }


def _watchlist_payload(
    name: str,
    add_ticker: str | None = None,
    note: str | None = None,
    remove_ticker: str | None = None,
) -> dict:
    tickers = ["AAPL", "NVDA", "CRM", "O", "JPM"]
    if add_ticker:
        normalized = add_ticker.upper()
        if normalized not in tickers:
            tickers.append(normalized)
    if remove_ticker:
        tickers = [ticker for ticker in tickers if ticker != remove_ticker.upper()]
    items = [_watchlist_fixture_item(ticker, add_ticker, note) for ticker in tickers]
    return {
        "data": {
            "id": f"fixture-{name.lower()}",
            "name": name,
            "owner_key": "fixture",
            "items": items,
            "source_trace": {
                "source_type": "watchlist_fixture",
                "source_document_id": f"watchlist-{name.lower()}-fixture",
                "filing_id": f"watchlist-{name.lower()}-fixture",
                "period": "current",
                "unit": "watchlist",
                "currency": "mixed",
                "formula": "fixture watchlist assembled from seed universe snapshots",
                "quality_status": "fixture_non_production_watchlist",
            },
        },
        "meta": {"source": "fixture_non_production", "data_mode": "fixture_non_production"},
    }


def _watchlist_fixture_item(
    ticker: str,
    add_ticker: str | None = None,
    note: str | None = None,
) -> dict:
    snapshot = snapshot_for(ticker) if ticker in SAMPLE_SECURITY_META else None
    trace = (
        snapshot["source_trace"]
        if snapshot
        else {
            "source_type": "watchlist_fixture",
            "source_document_id": f"{ticker.lower()}-watchlist-fixture",
            "filing_id": f"{ticker.lower()}-watchlist-fixture",
            "period": "current",
            "unit": "ticker",
            "currency": "mixed",
            "formula": "fixture watchlist entry for UI and contract testing",
            "quality_status": "fixture_non_production_watchlist",
        }
    )
    return {
        "ticker": ticker,
        "name": snapshot["name"] if snapshot else ticker,
        "market": snapshot["market"] if snapshot else None,
        "country": snapshot["country"] if snapshot else None,
        "currency": snapshot["currency"] if snapshot else None,
        "current_price": snapshot["current_price"] if snapshot else None,
        "per": snapshot["per"] if snapshot else None,
        "dividend_yield": snapshot["dividend_yield"] if snapshot else None,
        "eps_cagr": snapshot["eps_cagr"] if snapshot else None,
        "quality_status": trace.get("quality_status"),
        "note": note if add_ticker and ticker == add_ticker.upper() else None,
        "source_trace": _watchlist_item_trace(
            {},
            {
                "ticker": ticker,
                "currency": snapshot["currency"] if snapshot else None,
                "source_trace": trace,
            },
        ),
    }


def _fixture_valuation_rows(ticker: str) -> list[dict]:
    result = sample_normalization_result(ticker, NormalizationPolicy())
    rows: list[dict] = []
    for record in result.series:
        selected_metric, metric_trace, _ = selected_valuation_metric(
            ticker,
            record.fiscal_year,
            "adjusted_operating",
            record,
        )
        _, dividend = price_dividend_for(ticker, record.fiscal_year)
        rows.append(
            {
                "fiscal_year": record.fiscal_year,
                "metric": selected_metric,
                "dividend": dividend,
                "forecast_flag": False,
                "source_trace": metric_trace,
            }
        )
    return rows


def _fiscal_fitness_currency(ticker: str) -> str:
    snapshot = company_snapshot_from_postgres(ticker)
    if snapshot is not None and snapshot.get("currency"):
        return str(snapshot["currency"])
    return SAMPLE_SECURITY_META.get(ticker, {}).get("currency", "USD")


def _fiscal_fitness_summary(rows: list[dict]) -> dict:
    latest_year = max((int(row["fiscal_year"]) for row in rows), default=None)
    latest_rows = [row for row in rows if int(row["fiscal_year"]) == latest_year]
    by_key = {row["metric_key"]: row for row in latest_rows}
    flags = sorted({flag for row in rows for flag in row.get("flags", [])})
    return {
        "latest_year": latest_year,
        "roe_pct": _summary_value(by_key.get("roe_pct")),
        "roic_pct": _summary_value(by_key.get("roic_pct")),
        "fcf_margin_pct": _summary_value(by_key.get("fcf_margin_pct")),
        "debt_to_equity": _summary_value(by_key.get("debt_to_equity")),
        "quality_status": _summary_quality(latest_rows),
        "flags": flags,
    }


def _summary_value(row: dict | None) -> str | None:
    if row is None or row.get("value") is None:
        return None
    return str(row["value"])


def _summary_quality(rows: list[dict]) -> str | None:
    if not rows:
        return None
    statuses = sorted({str(row.get("quality_status")) for row in rows})
    for marker in ("warning", "partial", "fixture_non_production"):
        match = next((status for status in statuses if marker in status), None)
        if match:
            return match
    return statuses[0]


def _fiscal_fitness_scope() -> list[str]:
    return [
        "gross_margin_pct",
        "operating_margin_pct",
        "net_margin_pct",
        "roe_pct",
        "roic_pct",
        "debt_to_equity",
        "fcf_margin_pct",
        "revenue_growth_pct",
        "eps_growth_pct",
        "current_ratio",
        "quick_ratio",
        "interest_coverage",
    ]


def _fun_graphs_scope() -> list[str]:
    return [
        "revenue",
        "adjusted_eps",
        "gaap_eps_diluted",
        "free_cash_flow",
        "gross_margin_pct",
        "operating_margin_pct",
        "net_margin_pct",
        "roe_pct",
        "roic_pct",
        "debt_to_equity",
    ]


def _health_check_scope() -> list[str]:
    return [
        "overall_score",
        "profitability",
        "cash_generation",
        "financial_strength",
        "growth",
        "predictability",
    ]


def _analyst_scorecard_scope() -> list[str]:
    return [
        "actual_eps",
        "estimate_1y_prior",
        "estimate_2y_prior",
        "error_1y_pct",
        "error_2y_pct",
        "result_1y",
        "result_2y",
        "hit_rate_1y_pct",
        "hit_rate_2y_pct",
    ]


def _research_report_scope() -> list[str]:
    return [
        "executive_summary",
        "valuation",
        "quality",
        "forecast",
        "capital_allocation",
        "data_quality",
        "audit_facts",
    ]


def _empty_research_metadata_payload(ticker: str) -> dict:
    now = datetime.now(UTC).isoformat()
    trace = {
        "source_document_id": "research_metadata:not_loaded",
        "source_type": "research_metadata",
        "source": "research_metadata",
        "filing_id": f"research_metadata:{ticker}",
        "period": "metadata",
        "available_at": now,
        "unit": "research_metadata",
        "currency": "N/A",
        "method": "metadata_only_no_financial_numbers",
        "formula": (
            "Research metadata endpoint requires source-backed raw_objects rows; "
            "no fixture or generated research links are substituted"
        ),
        "quality_status": "missing_source_backed_data",
        "quality_flags": ["research_metadata_not_loaded"],
        "financial_numbers_allowed": False,
    }
    return {
        "ticker": ticker,
        "data_mode": "source_backed_required",
        "policy": "metadata_only_no_financial_numbers",
        "quality_status": "missing_source_backed_data",
        "items": [],
        "source_trace": trace,
        "meta": {
            "source": "postgres",
            "sources": ["naver_search_research", "hankyung_consensus_metadata"],
            "financial_numbers_allowed": False,
            "row_count": 0,
            "item_count": 0,
        },
    }


def _research_report_missing_scopes(report: dict) -> list[str]:
    flags = set(report.get("flags", []))
    missing = []
    if "missing_valuation_map" in flags:
        missing.append("valuation")
    if "missing_forecast_rows" in flags:
        missing.append("forecast")
    if "missing_health_check" in flags:
        missing.append("quality")
    if "missing_use_of_cash" in flags:
        missing.append("capital_allocation")
    return missing


def _performance_scope() -> list[str]:
    return [
        "start_price",
        "end_price",
        "shares_purchased",
        "ending_value",
        "dividends_received",
        "reinvested_shares",
        "reinvested_dividends",
        "reinvested_ending_value",
        "capital_gain",
        "total_gain",
        "reinvested_total_gain",
        "price_return_pct",
        "dividend_return_pct",
        "total_return_pct",
        "reinvested_total_return_pct",
        "annualized_price_return_pct",
        "annualized_total_return_pct",
        "reinvested_annualized_total_return_pct",
    ]


def _use_of_cash_summary(rows: list[dict]) -> dict:
    latest = rows[-1] if rows else {}
    flags = sorted({flag for row in rows for flag in row.get("flags", [])})
    return {
        "latest_fcf_margin_pct": str(latest.get("fcf_margin_pct"))
        if latest.get("fcf_margin_pct") is not None
        else None,
        "latest_dividend_payout_pct": str(latest.get("dividend_payout_pct"))
        if latest.get("dividend_payout_pct") is not None
        else None,
        "latest_debt_to_equity": str(latest.get("debt_to_equity"))
        if latest.get("debt_to_equity") is not None
        else None,
        "quality_status": latest.get("quality_status"),
        "flags": flags,
    }


def _use_of_cash_scope() -> list[str]:
    return [
        "operating_cash_flow",
        "capex",
        "free_cash_flow",
        "dividends_paid",
        "dividend_per_share",
        "share_repurchases",
        "debt_repayment",
        "acquisitions",
        "net_cash_use",
        "fcf_margin_pct",
        "dividend_payout_pct",
        "debt_to_equity",
    ]


def _json_safe_series(rows: list[dict]) -> list[dict]:
    safe_rows: list[dict] = []
    for row in rows:
        safe_rows.append(
            {key: str(value) if isinstance(value, Decimal) else value for key, value in row.items()}
        )
    return safe_rows


SAMPLE_SECURITY_META_PRICE_YEARS = {
    ticker: {2020, 2021, 2022, 2023, 2024} for ticker in SAMPLE_SECURITY_META
}


def _json_safe_object(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, list):
        return [_json_safe_object(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe_object(item) for key, item in value.items()}
    return value


def _missing_trace_value(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"", "unknown", "n/a", "na", "none"}
    return False
