from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import io
import json
import os
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text

from backend.normalize.schemas import NormalizationPolicy, SourceDocument, SourceTrace
from backend.normalize.service import NormalizationService
from data.blob_queue import BlobQueueItem, BlobUploadQueue
from packages.connectors.base import ConnectorDocument, ConnectorRequest
from packages.connectors.ecos import EcosConnector
from packages.connectors.edinet import EdinetConnector
from packages.connectors.estat import EStatConnector
from packages.connectors.finance_data_reader import FinanceDataReaderConnector
from packages.connectors.fred import FredConnector
from packages.connectors.jquants import JQuantsConnector
from packages.connectors.kosis import KosisConnector
from packages.connectors.marcap import MarcapConnector
from packages.connectors.opendart import OpenDartConnector
from packages.connectors.pykrx import PyKrxConnector
from packages.connectors.research_metadata import (
    HankyungConsensusMetadataConnector,
    NaverResearchSearchConnector,
)
from packages.connectors.sec import SecBulkConnector, SecEdgarConnector
from packages.connectors.stooq import StooqConnector
from packages.core.env import load_local_env
from packages.core.universe import (
    KR_TOP_MARKET_CAP_PRIORITY_TICKERS,
    comma_join,
)
from services.api.database import get_engine
from services.api.kr_cache_provider import kr_valuation_cache_universe_coverage
from services.api.kr_warehouse_provider import source_coverage_rows_from_kr_warehouse
from services.api.local_consensus_provider import overlay_local_consensus_counts
from services.api.postgres_provider import source_coverage_from_postgres
from services.api.sample_data import SAMPLE_SECURITY_META
from services.api.source_coverage import (
    build_source_coverage_report,
    normalize_coverage_tickers,
)
from services.ingestion_worker.data_lake_plan import (
    DEFAULT_FRED_SERIES,
    build_data_lake_plan,
    render_data_lake_plan_markdown,
    write_plan_manifest,
)
from services.ingestion_worker.kr_valuation_warehouse import (
    load_kr_valuation_cache_to_warehouse,
)
from services.ingestion_worker.kr_valuation_postgres import (
    load_kr_valuation_cache_to_postgres,
)
from services.ingestion_worker.market_standard import normalize_market_standard_document
from services.ingestion_worker.official_stats import normalize_official_stat_document
from services.ingestion_worker.repository import IngestionRepository
from services.ingestion_worker.sec_bulk_warehouse import (
    SEC_COMPANYFACTS_URL,
    SEC_SUBMISSIONS_URL,
    SecBulkDerivedMetricRow,
    SecBulkFactRow,
    derived_metric_rows,
    parse_companyfacts_zip,
    parse_submissions_zip,
    primary_metric_rows,
)
from services.ingestion_worker.secret_audit import source_metadata_secret_audit
from services.ingestion_worker.source_catalog import (
    render_source_catalog_markdown,
    source_catalog_payload,
)

LOCAL_ENV_KEYS = load_local_env()

STATIC_ENV_EXAMPLE_KEYS = {
    "NEXT_PUBLIC_SITE_URL",
    "SEC_USER_AGENT",
    "DATABASE_URL",
    "BLOB_READ_WRITE_TOKEN",
    "CHART_BLOB_QUEUE_ENABLED",
    "CHART_CACHE_DIR",
    "CHART_RUN_DIR",
    "CHART_LAYOUT_DIR",
    "DATA_BACKEND",
    "ALLOW_FIXTURE_FALLBACK",
    "AUTH_REQUIRED",
    "AUTH_SECRET",
    "AUTH_GITHUB_ID",
    "AUTH_GITHUB_SECRET",
    "AUTH_ALLOWED_EMAILS",
    "PF_COOKIE_SECRET",
    "API_AUTH_REQUIRED",
    "API_AUTH_DISABLED",
    "API_CORS_ORIGINS",
    "API_RATE_LIMIT_ENABLED",
    "API_RATE_LIMIT_REQUESTS",
    "API_RATE_LIMIT_WINDOW_SECONDS",
    "API_RATE_LIMIT_EXEMPT_PATHS",
    "API_ENABLE_HSTS",
    "OPENDART_API_KEY",
    "DART_API_KEY",
    "EDINET_API_KEY",
    "FRED_API_KEY",
    "ECOS_API_KEY",
    "KOSIS_API_KEY",
    "ESTAT_APP_ID",
    "JQUANTS_EMAIL",
    "JQUANTS_PASSWORD",
    "JQUANTS_REFRESH_TOKEN",
}
KR_TOP_MARKET_CAP_PRIORITY_TICKERS_CSV = comma_join(KR_TOP_MARKET_CAP_PRIORITY_TICKERS)
PRIORITY_E2E_MARKET_ORDER = ("KR", "US", "JP")


def _env_any(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def _has_opendart_key() -> bool:
    return bool(_env_any("OPENDART_API_KEY", "DART_API_KEY"))


@dataclass(frozen=True)
class SecurityMeta:
    ticker: str
    name: str
    country: str
    currency: str
    exchange: str | None = None


@dataclass(frozen=True)
class FnguideMetricRow:
    ticker: str
    name: str | None
    fiscal_year: int
    fiscal_period: str
    metric_key: str
    metric_label: str
    value: Decimal
    raw_value: str
    unit: str
    currency: str
    row_number: int
    source_sheet: str | None = None


FNGUIDE_COLUMN_ALIASES = {
    "ticker": {
        "ticker",
        "symbol",
        "code",
        "security_code",
        "local_code",
        "종목코드",
        "종목",
        "코드",
    },
    "name": {"name", "company_name", "company", "종목명", "기업명", "회사명"},
    "fiscal_year": {"fiscal_year", "year", "fy", "회계연도", "결산연도", "결산년도", "연도"},
    "fiscal_period": {"fiscal_period", "period", "분기", "기간"},
    "metric_key": {
        "metric_key",
        "metric",
        "item",
        "account",
        "account_name",
        "항목",
        "계정",
        "계정명",
        "지표",
    },
    "value": {"value", "amount", "data", "값", "금액", "데이터", "수치"},
    "unit": {"unit", "단위"},
    "currency": {"currency", "통화"},
}


FNGUIDE_METRIC_ALIASES = {
    "매출액": "revenue",
    "매출": "revenue",
    "영업이익": "operating_income",
    "당기순이익": "net_income",
    "순이익": "net_income",
    "지배주주순이익": "net_income_to_parent",
    "지배기업소유주지분순이익": "net_income_to_parent",
    "eps": "reported_eps",
    "희석eps": "reported_eps_diluted",
    "dilutedeps": "reported_eps_diluted",
    "bps": "book_value_per_share",
    "fcf": "free_cash_flow",
    "roe": "roe",
    "roic": "roic",
    "부채비율": "debt_to_equity",
    "배당수익률": "dividend_yield",
}

CONSENSUS_TEMPLATE_COLUMNS = [
    "ticker",
    "fiscal_year",
    "snapshot_date",
    "estimate_case",
    "estimate_eps",
    "growth_rate_pct",
    "analyst_count",
    "currency",
    "source",
    "source_url",
    "metric_key",
    "period_end",
    "quality_status",
    "source_document_id",
    "filing_id",
    "notes",
]

REQUIRED_CONSENSUS_COLUMNS = {
    "ticker",
    "fiscal_year",
    "snapshot_date",
    "estimate_case",
    "estimate_eps",
    "currency",
    "source",
}

DEFAULT_CONSENSUS_VALIDATION_CASES = "median,current"

BLOCKED_CONSENSUS_QUALITY_STATUSES = {
    "fixture_non_production_consensus_proxy",
    "missing_source_backed_consensus_snapshot",
    "template_pending_source_value",
}

BLOCKED_CONSENSUS_SOURCE_TOKENS = {
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

MANUAL_FORECAST_SOURCE_ALIASES = {
    "manual",
    "manual_assumption",
    "manual_forecast_assumption",
    "user_manual_forecast_assumption",
    "explicit_manual_forecast_assumption",
}

MANUAL_FORECAST_QUALITY_STATUSES = {
    "manual_forecast_assumption",
    "user_manual_forecast_assumption",
    "explicit_manual_forecast_assumption",
}


SEED_SECURITY_META = {
    ticker: SecurityMeta(
        ticker=ticker,
        name=str(meta["name"]),
        country=str(meta["country"]),
        currency=str(meta["currency"]),
        exchange=str(meta["market"]),
    )
    for ticker, meta in SAMPLE_SECURITY_META.items()
}


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m services.ingestion_worker.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect = subparsers.add_parser("collect")
    collect.add_argument("--market", required=True, choices=["US", "KR", "JP"])
    collect.add_argument("--ticker", required=True)
    collect.add_argument("--years", required=True)
    collect.add_argument("--persist", action="store_true")
    collect.add_argument("--force-refresh", action="store_true")

    collect_opendart_dividends_parser = subparsers.add_parser("collect-opendart-dividends")
    collect_opendart_dividends_parser.add_argument("--tickers", default="005930.KS")
    collect_opendart_dividends_parser.add_argument("--years", default="2020:2025")
    collect_opendart_dividends_parser.add_argument("--persist", action="store_true")
    collect_opendart_dividends_parser.add_argument("--force-refresh", action="store_true")
    collect_opendart_dividends_parser.add_argument("--sleep-seconds", type=float, default=0.5)

    collect_fred = subparsers.add_parser("collect-fred")
    collect_fred.add_argument("--series", default=",".join(DEFAULT_FRED_SERIES))
    collect_fred.add_argument("--years", default="2020:2025")
    collect_fred.add_argument("--persist", action="store_true")
    collect_fred.add_argument("--force-refresh", action="store_true")

    collect_ecos = subparsers.add_parser("collect-ecos")
    collect_ecos.add_argument("--series", required=True)
    collect_ecos.add_argument("--years", default="2020:2025")
    collect_ecos.add_argument("--persist", action="store_true")
    collect_ecos.add_argument("--force-refresh", action="store_true")

    collect_kosis = subparsers.add_parser("collect-kosis")
    collect_kosis.add_argument("--tables", required=True)
    collect_kosis.add_argument("--years", default="2020:2025")
    collect_kosis.add_argument("--persist", action="store_true")
    collect_kosis.add_argument("--force-refresh", action="store_true")

    collect_estat = subparsers.add_parser("collect-estat")
    collect_estat.add_argument("--stats-data-ids", required=True)
    collect_estat.add_argument("--years", default="2020:2025")
    collect_estat.add_argument("--persist", action="store_true")
    collect_estat.add_argument("--force-refresh", action="store_true")

    collect_stooq = subparsers.add_parser("collect-stooq-prices")
    collect_stooq.add_argument("--market", default="US", choices=["US", "JP", "GLOBAL"])
    collect_stooq.add_argument("--tickers", default="AAPL,NVDA,CRM,O,JPM")
    collect_stooq.add_argument("--years", default="2020:2025")
    collect_stooq.add_argument("--persist", action="store_true")
    collect_stooq.add_argument("--force-refresh", action="store_true")

    collect_fdr = subparsers.add_parser("collect-fdr-prices")
    collect_fdr.add_argument("--market", default="US", choices=["US", "KR", "JP", "GLOBAL"])
    collect_fdr.add_argument("--tickers", default="AAPL,NVDA,CRM,O,JPM")
    collect_fdr.add_argument("--years", default="2020:2025")
    collect_fdr.add_argument("--persist", action="store_true")
    collect_fdr.add_argument("--force-refresh", action="store_true")

    collect_pykrx = subparsers.add_parser("collect-pykrx-prices")
    collect_pykrx.add_argument(
        "--tickers",
        default=KR_TOP_MARKET_CAP_PRIORITY_TICKERS_CSV,
    )
    collect_pykrx.add_argument("--years", default="2020:2025")
    collect_pykrx.add_argument("--persist", action="store_true")
    collect_pykrx.add_argument("--force-refresh", action="store_true")
    collect_pykrx.add_argument("--sleep-seconds", type=float, default=0.5)

    collect_pykrx_fundamentals_parser = subparsers.add_parser("collect-pykrx-fundamentals")
    collect_pykrx_fundamentals_parser.add_argument(
        "--tickers",
        default=KR_TOP_MARKET_CAP_PRIORITY_TICKERS_CSV,
    )
    collect_pykrx_fundamentals_parser.add_argument("--years", default="2020:2025")
    collect_pykrx_fundamentals_parser.add_argument("--persist", action="store_true")
    collect_pykrx_fundamentals_parser.add_argument("--force-refresh", action="store_true")
    collect_pykrx_fundamentals_parser.add_argument("--sleep-seconds", type=float, default=0.5)

    collect_marcap = subparsers.add_parser("collect-marcap")
    collect_marcap.add_argument(
        "--tickers",
        default=KR_TOP_MARKET_CAP_PRIORITY_TICKERS_CSV,
        help="Comma-separated KR tickers to load into price_bars. Empty string stores raw only.",
    )
    collect_marcap.add_argument("--years", default="2020:2025")
    collect_marcap.add_argument("--persist", action="store_true")
    collect_marcap.add_argument("--force-refresh", action="store_true")

    inspect_raw_kr = subparsers.add_parser("inspect-raw-kr")
    inspect_raw_kr.add_argument("--tickers", default="005930.KS")
    inspect_raw_kr.add_argument("--years", default="2024:2024")
    inspect_raw_kr.add_argument("--raw-root", default="storage/raw")
    inspect_raw_kr.add_argument("--require-opendart", action="store_true")
    inspect_raw_kr.add_argument("--strict", action="store_true")

    build_kr_inputs = subparsers.add_parser("build-kr-valuation-inputs")
    build_kr_inputs.add_argument("--tickers", default="005930.KS")
    build_kr_inputs.add_argument("--years", default="2024:2024")
    build_kr_inputs.add_argument("--raw-root", default="storage/raw")
    build_kr_inputs.add_argument("--out", default="storage/cache/kr-valuation-inputs")
    build_kr_inputs.add_argument("--strict", action="store_true")

    load_kr_warehouse = subparsers.add_parser("load-kr-valuation-warehouse")
    load_kr_warehouse.add_argument(
        "--tickers",
        default=KR_TOP_MARKET_CAP_PRIORITY_TICKERS_CSV,
    )
    load_kr_warehouse.add_argument("--cache-dir", default="storage/cache/kr-valuation-inputs")
    load_kr_warehouse.add_argument("--warehouse-root", default="data/warehouse/kr_valuation")
    load_kr_warehouse.add_argument("--db-path", default="data/warehouse/warehouse.duckdb")
    load_kr_warehouse.add_argument("--strict", action="store_true")

    load_kr_postgres = subparsers.add_parser("load-kr-valuation-postgres")
    load_kr_postgres.add_argument(
        "--tickers",
        default=KR_TOP_MARKET_CAP_PRIORITY_TICKERS_CSV,
    )
    load_kr_postgres.add_argument("--cache-dir", default="storage/cache/kr-valuation-inputs")
    load_kr_postgres.add_argument("--dry-run", action="store_true")
    load_kr_postgres.add_argument("--strict", action="store_true")

    collect_research = subparsers.add_parser("collect-research-metadata")
    collect_research.add_argument("--market", default="KR", choices=["KR"])
    collect_research.add_argument(
        "--tickers",
        default=KR_TOP_MARKET_CAP_PRIORITY_TICKERS_CSV,
    )
    collect_research.add_argument("--sources", default="naver,hankyung")
    collect_research.add_argument("--years", default="2020:2025")
    collect_research.add_argument("--persist", action="store_true")
    collect_research.add_argument("--force-refresh", action="store_true")
    collect_research.add_argument("--continue-on-error", action="store_true")

    collect_jquants = subparsers.add_parser("collect-jquants")
    collect_jquants.add_argument(
        "--tickers",
        default="7203.T,6758.T,6861.T,8306.T,7974.T",
    )
    collect_jquants.add_argument("--years", default="2020:2025")
    collect_jquants.add_argument("--endpoints", default="daily_quotes,statements,dividends")
    collect_jquants.add_argument("--persist", action="store_true")
    collect_jquants.add_argument("--force-refresh", action="store_true")

    collect_edinet = subparsers.add_parser("collect-edinet")
    collect_edinet.add_argument(
        "--tickers",
        default="7203.T,6758.T,6861.T,8306.T,7974.T",
    )
    collect_edinet.add_argument("--years", default="2020:2025")
    collect_edinet.add_argument("--download-types", default="metadata,csv")
    collect_edinet.add_argument("--doc-type-codes", default="120")
    collect_edinet.add_argument("--persist", action="store_true")
    collect_edinet.add_argument("--force-refresh", action="store_true")

    collect_sec_bulk = subparsers.add_parser("collect-sec-bulk")
    collect_sec_bulk.add_argument("--archives", default="companyfacts,submissions")
    collect_sec_bulk.add_argument("--persist", action="store_true")
    collect_sec_bulk.add_argument("--force-refresh", action="store_true")

    load_sec_bulk = subparsers.add_parser("load-sec-bulk-warehouse")
    load_sec_bulk.add_argument("--companyfacts-zip", default="")
    load_sec_bulk.add_argument("--submissions-zip", default="")
    load_sec_bulk.add_argument("--tickers", default="AAPL,NVDA,CRM,O,JPM")
    load_sec_bulk.add_argument("--persist", action="store_true")
    load_sec_bulk.add_argument("--max-companies", type=int, default=0)

    normalize_us = subparsers.add_parser("normalize-us")
    normalize_us.add_argument("--ticker", required=True)
    normalize_us.add_argument("--years", required=True)
    normalize_us.add_argument("--policy", default="street_comparable")
    normalize_us.add_argument("--persist", action="store_true")
    normalize_us.add_argument("--force-refresh", action="store_true")

    normalize_us_batch = subparsers.add_parser("normalize-us-batch")
    normalize_us_batch.add_argument("--tickers", default="AAPL,NVDA,CRM,O,JPM")
    normalize_us_batch.add_argument("--years", required=True)
    normalize_us_batch.add_argument("--policy", default="street_comparable")
    normalize_us_batch.add_argument("--persist", action="store_true")
    normalize_us_batch.add_argument("--force-refresh", action="store_true")
    normalize_us_batch.add_argument("--continue-on-error", action="store_true")

    market_csv = subparsers.add_parser("import-market-csv")
    market_csv.add_argument("--path", required=True)
    market_csv.add_argument("--persist", action="store_true")

    consensus_csv = subparsers.add_parser("import-consensus-csv")
    consensus_csv.add_argument("--path", required=True)
    consensus_csv.add_argument("--persist", action="store_true")

    consensus_validate = subparsers.add_parser("validate-consensus-csv")
    consensus_validate.add_argument("--path", required=True)
    consensus_validate.add_argument("--tickers", default=KR_TOP_MARKET_CAP_PRIORITY_TICKERS_CSV)
    consensus_validate.add_argument("--start-year", type=int, default=date.today().year)
    consensus_validate.add_argument("--years", type=int, default=5)
    consensus_validate.add_argument("--cases", default=DEFAULT_CONSENSUS_VALIDATION_CASES)
    consensus_validate.add_argument("--case-mode", choices=["any", "all"], default="any")
    consensus_validate.add_argument("--strict", action="store_true")

    consensus_template = subparsers.add_parser("export-consensus-template")
    consensus_template.add_argument("--tickers", default=KR_TOP_MARKET_CAP_PRIORITY_TICKERS_CSV)
    consensus_template.add_argument("--start-year", type=int, default=date.today().year)
    consensus_template.add_argument("--years", type=int, default=5)
    consensus_template.add_argument("--cases", default="low,median,high")
    consensus_template.add_argument("--snapshot-date", default=date.today().isoformat())
    consensus_template.add_argument(
        "--out",
        default="storage/imports/consensus_estimates.template.csv",
    )

    deterministic_forecast = subparsers.add_parser("export-deterministic-forecast-csv")
    deterministic_forecast.add_argument("--tickers", default="005930.KS")
    deterministic_forecast.add_argument("--start-year", type=int, default=date.today().year)
    deterministic_forecast.add_argument("--years", type=int, default=5)
    deterministic_forecast.add_argument("--cases", default="median")
    deterministic_forecast.add_argument("--snapshot-date", default=date.today().isoformat())
    deterministic_forecast.add_argument("--metric-key", default="adjusted_operating_eps")
    deterministic_forecast.add_argument(
        "--cache-dir",
        default="storage/cache/kr-valuation-inputs",
    )
    deterministic_forecast.add_argument(
        "--out",
        default="storage/imports/forecast_assumptions_005930.csv",
    )

    consensus_workpaper = subparsers.add_parser("consensus-workpaper")
    consensus_workpaper.add_argument("--tickers", default="005930.KS")
    consensus_workpaper.add_argument("--csv-path", default="storage/imports/consensus_005930.csv")
    consensus_workpaper.add_argument("--start-year", type=int, default=date.today().year)
    consensus_workpaper.add_argument("--years", type=int, default=5)
    consensus_workpaper.add_argument("--template-cases", default="median")
    consensus_workpaper.add_argument("--validation-cases", default=DEFAULT_CONSENSUS_VALIDATION_CASES)
    consensus_workpaper.add_argument("--case-mode", choices=["any", "all"], default="any")
    consensus_workpaper.add_argument("--out", default="storage/imports/consensus_005930_workpaper.md")

    fnguide_export = subparsers.add_parser("import-fnguide-export")
    fnguide_export.add_argument("--path", required=True)
    fnguide_export.add_argument("--sheet", default="")
    fnguide_export.add_argument("--persist", action="store_true")

    source_catalog = subparsers.add_parser("source-catalog")
    source_catalog.add_argument("--markets", default="")
    source_catalog.add_argument("--format", choices=["json", "markdown"], default="json")
    source_catalog.add_argument("--exclude-premium", action="store_true")

    data_lake_plan = subparsers.add_parser("data-lake-plan")
    data_lake_plan.add_argument("--markets", default="US,KR,JP")
    data_lake_plan.add_argument("--years", default="2020:2025")
    data_lake_plan.add_argument("--tickers", default="")
    data_lake_plan.add_argument("--partition", choices=["annual", "monthly"], default="annual")
    data_lake_plan.add_argument("--include-premium", action="store_true")
    data_lake_plan.add_argument("--exclude-wrappers", action="store_true")
    data_lake_plan.add_argument("--format", choices=["json", "markdown"], default="json")
    data_lake_plan.add_argument("--out", default="")

    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.add_argument("--markets", default="KR")
    doctor_parser.add_argument("--check-db", action="store_true")
    doctor_parser.add_argument("--require-blob", action="store_true")
    doctor_parser.add_argument("--strict", action="store_true")

    secret_audit_parser = subparsers.add_parser("secret-audit")
    secret_audit_parser.add_argument("--root", default=".")
    secret_audit_parser.add_argument("--include-raw", action="store_true")
    secret_audit_parser.add_argument("--strict", action="store_true")

    preflight_parser = subparsers.add_parser("preflight")
    preflight_parser.add_argument("--markets", default="KR")
    preflight_parser.add_argument("--require-blob", action="store_true")
    preflight_parser.add_argument("--strict", action="store_true")

    deploy_gate_parser = subparsers.add_parser("deploy-gate")
    deploy_gate_parser.add_argument("--markets", default="KR")
    deploy_gate_parser.add_argument("--tickers", default=KR_TOP_MARKET_CAP_PRIORITY_TICKERS_CSV)
    deploy_gate_parser.add_argument("--require-blob", action="store_true")
    deploy_gate_parser.add_argument("--require-consensus-forecast", action="store_true")
    deploy_gate_parser.add_argument("--strict", action="store_true")
    deploy_gate_parser.add_argument("--summary-only", action="store_true")

    coverage_parser = subparsers.add_parser("source-coverage")
    coverage_parser.add_argument("--market", default="KR")
    coverage_parser.add_argument("--tickers", default="")
    coverage_parser.add_argument("--min-historical-years", type=int, default=3)
    coverage_parser.add_argument("--min-forecast-years", type=int, default=5)
    coverage_parser.add_argument("--require-consensus-forecast", action="store_true")
    coverage_parser.add_argument("--strict", action="store_true")

    kr_readiness_parser = subparsers.add_parser("kr-production-readiness")
    kr_readiness_parser.add_argument("--tickers", default=KR_TOP_MARKET_CAP_PRIORITY_TICKERS_CSV)
    kr_readiness_parser.add_argument("--years", default="2020:2025")
    kr_readiness_parser.add_argument("--min-historical-years", type=int, default=3)
    kr_readiness_parser.add_argument("--min-forecast-years", type=int, default=5)
    kr_readiness_parser.add_argument("--require-consensus-forecast", action="store_true")
    kr_readiness_parser.add_argument("--summary-only", action="store_true")
    kr_readiness_parser.add_argument("--strict", action="store_true")

    source_e2e = subparsers.add_parser("run-source-e2e")
    source_e2e.add_argument("--market", default="KR", choices=["US", "KR", "JP"])
    source_e2e.add_argument(
        "--tickers",
        default="",
        help="Comma-separated tickers. Blank uses the selected market priority universe.",
    )
    source_e2e.add_argument("--years", default="2020:2025")
    source_e2e.add_argument("--policy", default="street_comparable")
    source_e2e.add_argument("--persist", action="store_true")
    source_e2e.add_argument("--force-refresh", action="store_true")
    source_e2e.add_argument("--continue-on-error", action="store_true")
    source_e2e.add_argument("--require-consensus-forecast", action="store_true")
    source_e2e.add_argument("--dry-run", action="store_true")
    source_e2e.add_argument("--summary-only", action="store_true")
    source_e2e.add_argument("--strict", action="store_true")

    priority_e2e = subparsers.add_parser("run-priority-e2e")
    priority_e2e.add_argument(
        "--markets",
        default="KR,US,JP",
        help="Comma-separated market subset. Execution is always ordered KR,US,JP.",
    )
    priority_e2e.add_argument("--years", default="2020:2025")
    priority_e2e.add_argument("--policy", default="street_comparable")
    priority_e2e.add_argument("--persist", action="store_true")
    priority_e2e.add_argument("--force-refresh", action="store_true")
    priority_e2e.add_argument("--continue-on-error", action="store_true")
    priority_e2e.add_argument("--require-consensus-forecast", action="store_true")
    priority_e2e.add_argument("--dry-run", action="store_true")
    priority_e2e.add_argument("--summary-only", action="store_true")
    priority_e2e.add_argument("--strict", action="store_true")

    p1_e2e = subparsers.add_parser("run-p1-e2e")
    p1_e2e.add_argument("--us-ticker", default="AAPL")
    p1_e2e.add_argument("--kr-ticker", default="005930.KS")
    p1_e2e.add_argument("--jp-ticker", default="7203.T")
    p1_e2e.add_argument("--years", default="2020:2025")
    p1_e2e.add_argument("--policy", default="street_comparable")
    p1_e2e.add_argument("--persist", action="store_true")
    p1_e2e.add_argument("--force-refresh", action="store_true")
    p1_e2e.add_argument("--continue-on-error", action="store_true")
    p1_e2e.add_argument("--require-consensus-forecast", action="store_true")
    p1_e2e.add_argument("--dry-run", action="store_true")
    p1_e2e.add_argument("--summary-only", action="store_true")
    p1_e2e.add_argument("--strict", action="store_true")

    args = parser.parse_args()
    if args.command == "doctor":
        summary = doctor(
            markets=args.markets,
            check_db=args.check_db,
            require_blob=args.require_blob,
            strict=args.strict,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        if args.strict and summary["status"] != "ok":
            raise SystemExit(1)
        return

    if args.command == "secret-audit":
        summary = source_metadata_secret_audit(
            root=Path(args.root),
            include_raw=args.include_raw,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        if args.strict and summary["status"] != "passed":
            raise SystemExit(1)
        return

    if args.command == "preflight":
        summary = deployment_preflight(
            markets=args.markets,
            require_blob=args.require_blob,
            strict=args.strict,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        if args.strict and summary["status"] != "ok":
            raise SystemExit(1)
        return

    if args.command == "deploy-gate":
        summary = deployment_gate(
            markets=args.markets,
            tickers=args.tickers,
            require_blob=args.require_blob,
            require_consensus_forecast=args.require_consensus_forecast,
            strict=args.strict,
        )
        output = _deployment_gate_output_summary(summary) if args.summary_only else summary
        print(json.dumps(output, indent=2, ensure_ascii=False, default=str))
        if args.strict and summary["status"] != "ok":
            raise SystemExit(1)
        return

    if args.command == "source-coverage":
        summary = source_coverage_report(
            tickers=args.tickers or None,
            market=args.market,
            min_historical_years=args.min_historical_years,
            min_forecast_years=args.min_forecast_years,
            require_consensus_forecast=args.require_consensus_forecast,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        if args.strict and summary["status"] != "ready":
            raise SystemExit(1)
        return

    if args.command == "kr-production-readiness":
        summary = kr_production_readiness(
            tickers=args.tickers,
            years=args.years,
            min_historical_years=args.min_historical_years,
            min_forecast_years=args.min_forecast_years,
            require_consensus_forecast=args.require_consensus_forecast,
        )
        output = _kr_readiness_output_summary(summary) if args.summary_only else summary
        print(json.dumps(output, indent=2, ensure_ascii=False, default=str))
        if args.strict and summary["status"] not in {
            "local_warehouse_ready",
            "ready_for_protected_smoke",
            "production_ready",
        }:
            raise SystemExit(1)
        return

    if args.command == "run-source-e2e":
        start_year, end_year = _parse_years(args.years)
        summary = run_source_e2e(
            market=args.market,
            tickers=args.tickers,
            start_year=start_year,
            end_year=end_year,
            policy=args.policy,
            persist=args.persist,
            force_refresh=args.force_refresh,
            continue_on_error=args.continue_on_error,
            require_consensus_forecast=args.require_consensus_forecast,
            dry_run=args.dry_run,
        )
        _print_json(summary, summary_only=args.summary_only)
        if args.strict and summary["status"] not in {"ok", "planned"}:
            raise SystemExit(1)
        return

    if args.command == "run-priority-e2e":
        start_year, end_year = _parse_years(args.years)
        summary = run_priority_e2e(
            markets=args.markets,
            start_year=start_year,
            end_year=end_year,
            policy=args.policy,
            persist=args.persist,
            force_refresh=args.force_refresh,
            continue_on_error=args.continue_on_error,
            require_consensus_forecast=args.require_consensus_forecast,
            dry_run=args.dry_run,
        )
        _print_json(summary, summary_only=args.summary_only)
        if args.strict and summary["status"] not in {"ok", "planned"}:
            raise SystemExit(1)
        return

    if args.command == "run-p1-e2e":
        start_year, end_year = _parse_years(args.years)
        summary = run_p1_e2e(
            us_ticker=args.us_ticker,
            kr_ticker=args.kr_ticker,
            jp_ticker=args.jp_ticker,
            start_year=start_year,
            end_year=end_year,
            policy=args.policy,
            persist=args.persist,
            force_refresh=args.force_refresh,
            continue_on_error=args.continue_on_error,
            require_consensus_forecast=args.require_consensus_forecast,
            dry_run=args.dry_run,
        )
        _print_json(summary, summary_only=args.summary_only)
        if args.strict and summary["status"] not in {"ok", "planned"}:
            raise SystemExit(1)
        return

    if args.command == "source-catalog":
        payload = source_catalog_payload(
            args.markets or None,
            include_premium=not args.exclude_premium,
        )
        if args.format == "markdown":
            print(render_source_catalog_markdown(payload))
        else:
            print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return

    if args.command == "data-lake-plan":
        plan = build_data_lake_plan(
            markets=args.markets,
            years=args.years,
            tickers=args.tickers or None,
            include_premium=args.include_premium,
            include_wrappers=not args.exclude_wrappers,
            partition=args.partition,
        )
        if args.out:
            write_plan_manifest(plan, Path(args.out))
        if args.format == "markdown":
            print(render_data_lake_plan_markdown(plan))
        else:
            print(json.dumps(plan, indent=2, ensure_ascii=False, default=str))
        return

    if args.command == "collect":
        start_year, end_year = _parse_years(args.years)
        summary = collect_market_documents(
            args.market,
            args.ticker,
            start_year,
            end_year,
            persist=args.persist,
            force_refresh=args.force_refresh,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        return

    if args.command == "collect-opendart-dividends":
        start_year, end_year = _parse_years(args.years)
        summary = collect_opendart_dividends(
            args.tickers,
            start_year,
            end_year,
            persist=args.persist,
            force_refresh=args.force_refresh,
            sleep_seconds=args.sleep_seconds,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        return

    if args.command == "collect-fred":
        start_year, end_year = _parse_years(args.years)
        summary = collect_fred_series(
            args.series,
            start_year,
            end_year,
            persist=args.persist,
            force_refresh=args.force_refresh,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        return

    if args.command == "collect-ecos":
        start_year, end_year = _parse_years(args.years)
        summary = collect_ecos_series(
            args.series,
            start_year,
            end_year,
            persist=args.persist,
            force_refresh=args.force_refresh,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        return

    if args.command == "collect-kosis":
        start_year, end_year = _parse_years(args.years)
        summary = collect_kosis_tables(
            args.tables,
            start_year,
            end_year,
            persist=args.persist,
            force_refresh=args.force_refresh,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        return

    if args.command == "collect-estat":
        start_year, end_year = _parse_years(args.years)
        summary = collect_estat_tables(
            args.stats_data_ids,
            start_year,
            end_year,
            persist=args.persist,
            force_refresh=args.force_refresh,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        return

    if args.command == "collect-stooq-prices":
        start_year, end_year = _parse_years(args.years)
        summary = collect_stooq_prices(
            args.tickers,
            args.market,
            start_year,
            end_year,
            persist=args.persist,
            force_refresh=args.force_refresh,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        return

    if args.command == "collect-fdr-prices":
        start_year, end_year = _parse_years(args.years)
        summary = collect_fdr_prices(
            args.tickers,
            args.market,
            start_year,
            end_year,
            persist=args.persist,
            force_refresh=args.force_refresh,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        return

    if args.command == "collect-pykrx-prices":
        start_year, end_year = _parse_years(args.years)
        summary = collect_pykrx_prices(
            args.tickers,
            start_year,
            end_year,
            persist=args.persist,
            force_refresh=args.force_refresh,
            sleep_seconds=args.sleep_seconds,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        return

    if args.command == "collect-pykrx-fundamentals":
        start_year, end_year = _parse_years(args.years)
        summary = collect_pykrx_fundamentals(
            args.tickers,
            start_year,
            end_year,
            persist=args.persist,
            force_refresh=args.force_refresh,
            sleep_seconds=args.sleep_seconds,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        return

    if args.command == "collect-marcap":
        start_year, end_year = _parse_years(args.years)
        summary = collect_marcap_data(
            args.tickers,
            start_year,
            end_year,
            persist=args.persist,
            force_refresh=args.force_refresh,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        return

    if args.command == "inspect-raw-kr":
        start_year, end_year = _parse_years(args.years)
        summary = inspect_raw_kr_evidence(
            args.tickers,
            start_year,
            end_year,
            raw_root=Path(args.raw_root),
            require_opendart=args.require_opendart,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        if args.strict and summary["status"] != "ok":
            raise SystemExit(1)
        return

    if args.command == "build-kr-valuation-inputs":
        start_year, end_year = _parse_years(args.years)
        summary = build_kr_valuation_inputs(
            args.tickers,
            start_year,
            end_year,
            raw_root=Path(args.raw_root),
            out_dir=Path(args.out),
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        if args.strict and (
            summary["status"] != "ok"
            or summary.get("summary", {}).get("valuation_ready") != summary.get("summary", {}).get("tickers_expected")
        ):
            raise SystemExit(1)
        return

    if args.command == "load-kr-valuation-warehouse":
        summary = load_kr_valuation_cache_to_warehouse(
            args.tickers,
            cache_dir=Path(args.cache_dir),
            warehouse_root=Path(args.warehouse_root),
            db_path=Path(args.db_path),
            strict=args.strict,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        if args.strict and summary["status"] != "ok":
            raise SystemExit(1)
        return

    if args.command == "load-kr-valuation-postgres":
        summary = load_kr_valuation_cache_to_postgres(
            args.tickers,
            cache_dir=Path(args.cache_dir),
            dry_run=args.dry_run,
            strict=args.strict,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        if args.strict and summary["status"] != "ok":
            raise SystemExit(1)
        return

    if args.command == "collect-research-metadata":
        start_year, end_year = _parse_years(args.years)
        summary = collect_research_metadata(
            args.tickers,
            args.market,
            args.sources,
            start_year,
            end_year,
            persist=args.persist,
            force_refresh=args.force_refresh,
            continue_on_error=args.continue_on_error,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        return

    if args.command == "collect-jquants":
        start_year, end_year = _parse_years(args.years)
        summary = collect_jquants_data(
            args.tickers,
            start_year,
            end_year,
            endpoints=args.endpoints,
            persist=args.persist,
            force_refresh=args.force_refresh,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        return

    if args.command == "collect-edinet":
        start_year, end_year = _parse_years(args.years)
        summary = collect_edinet_filings(
            args.tickers,
            start_year,
            end_year,
            download_types=args.download_types,
            doc_type_codes=args.doc_type_codes,
            persist=args.persist,
            force_refresh=args.force_refresh,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        return

    if args.command == "collect-sec-bulk":
        summary = collect_sec_bulk_archives(
            args.archives,
            persist=args.persist,
            force_refresh=args.force_refresh,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        return

    if args.command == "load-sec-bulk-warehouse":
        summary = load_sec_bulk_warehouse(
            companyfacts_zip=Path(args.companyfacts_zip) if args.companyfacts_zip else None,
            submissions_zip=Path(args.submissions_zip) if args.submissions_zip else None,
            tickers=args.tickers,
            persist=args.persist,
            max_companies=args.max_companies or None,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        return

    if args.command == "normalize-us":
        start_year, end_year = _parse_years(args.years)
        summary = normalize_us_ticker(
            args.ticker,
            start_year,
            end_year,
            policy=args.policy,
            persist=args.persist,
            force_refresh=args.force_refresh,
        )
        print(
            json.dumps(
                summary,
                indent=2,
                ensure_ascii=False,
                default=str,
            )
        )
        return

    if args.command == "normalize-us-batch":
        start_year, end_year = _parse_years(args.years)
        summary = normalize_us_batch_run(
            args.tickers,
            start_year,
            end_year,
            policy=args.policy,
            persist=args.persist,
            force_refresh=args.force_refresh,
            continue_on_error=args.continue_on_error,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        if summary["failed"] and not args.continue_on_error:
            raise SystemExit(1)
        return

    if args.command == "import-market-csv":
        summary = import_market_csv(Path(args.path), persist=args.persist)
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        return

    if args.command == "import-consensus-csv":
        try:
            summary = import_consensus_csv(Path(args.path), persist=args.persist)
        except ValueError as exc:
            print(
                json.dumps(
                    {
                        "status": "invalid_input",
                        "command": "import-consensus-csv",
                        "path": args.path,
                        "error": str(exc),
                    },
                    indent=2,
                    ensure_ascii=False,
                    default=str,
                )
            )
            raise SystemExit(1) from exc
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        return

    if args.command == "validate-consensus-csv":
        summary = validate_consensus_csv(
            Path(args.path),
            tickers=args.tickers,
            start_year=args.start_year,
            years=args.years,
            cases=args.cases,
            case_mode=args.case_mode,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        if args.strict and not summary["import_ready"]:
            raise SystemExit(1)
        return

    if args.command == "export-consensus-template":
        summary = export_consensus_template(
            tickers=args.tickers,
            start_year=args.start_year,
            years=args.years,
            cases=args.cases,
            snapshot_date=args.snapshot_date,
            out=Path(args.out),
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        return

    if args.command == "export-deterministic-forecast-csv":
        summary = export_deterministic_forecast_csv(
            tickers=args.tickers,
            start_year=args.start_year,
            years=args.years,
            cases=args.cases,
            snapshot_date=args.snapshot_date,
            metric_key=args.metric_key,
            cache_dir=Path(args.cache_dir),
            out=Path(args.out),
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        return

    if args.command == "consensus-workpaper":
        summary = build_consensus_workpaper(
            tickers=args.tickers,
            csv_path=Path(args.csv_path),
            start_year=args.start_year,
            years=args.years,
            template_cases=args.template_cases,
            validation_cases=args.validation_cases,
            case_mode=args.case_mode,
            out=Path(args.out),
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
        return

    if args.command == "import-fnguide-export":
        summary = import_fnguide_export(
            Path(args.path),
            sheet=args.sheet or None,
            persist=args.persist,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


def doctor(
    markets: str = "KR",
    check_db: bool = False,
    require_blob: bool = False,
    strict: bool = False,
) -> dict[str, Any]:
    requested_markets = {
        market.strip().upper()
        for market in markets.split(",")
        if market.strip()
    }
    checks: list[dict[str, Any]] = []

    def add_check(name: str, ok: bool, required: bool, detail: str) -> None:
        checks.append(
            {
                "name": name,
                "ok": ok,
                "required": required,
                "detail": detail,
            }
        )

    needs_db = check_db or strict
    add_check(
        "DATABASE_URL",
        bool(os.getenv("DATABASE_URL")),
        needs_db,
        "required for Neon/Postgres persistence",
    )
    add_check(
        "DATA_BACKEND=postgres",
        os.getenv("DATA_BACKEND", "").lower() == "postgres",
        needs_db,
        "required for API/worker Postgres read and write paths",
    )
    if "US" in requested_markets:
        add_check(
            "SEC_USER_AGENT",
            bool(os.getenv("SEC_USER_AGENT")),
            strict,
            "required for SEC EDGAR collection",
        )
    if "KR" in requested_markets:
        add_check(
            "OPENDART_API_KEY",
            _has_opendart_key(),
            strict,
            "required for OpenDART collection; DART_API_KEY is accepted as an alias",
        )
    if "JP" in requested_markets:
        has_jquants = bool(os.getenv("JQUANTS_REFRESH_TOKEN")) or (
            bool(os.getenv("JQUANTS_EMAIL")) and bool(os.getenv("JQUANTS_PASSWORD"))
        )
        add_check(
            "JQUANTS credentials",
            has_jquants,
            strict,
            "required for J-Quants collection",
        )
        add_check(
            "EDINET_API_KEY",
            bool(os.getenv("EDINET_API_KEY")),
            strict,
            "required for EDINET filing evidence collection",
        )
    add_check(
        "BLOB_READ_WRITE_TOKEN",
        bool(os.getenv("BLOB_READ_WRITE_TOKEN")),
        require_blob or strict,
        "required for Vercel Blob sync",
    )
    add_check(
        "FRED_API_KEY",
        bool(os.getenv("FRED_API_KEY")),
        strict,
        "required for FRED macro, rates, FX, and recession-band collection",
    )

    if check_db:
        add_check("database_connectivity", _database_connectivity_ok(), True, "SELECT 1")

    blocking = [check for check in checks if check["required"] and not check["ok"]]
    return {
        "status": "ok" if not blocking else "needs_configuration",
        "markets": sorted(requested_markets),
        **_local_env_status(),
        "checks": checks,
        "missing_required": [check["name"] for check in blocking],
    }


def _local_env_status() -> dict[str, Any]:
    return {
        "local_env_loaded": bool(LOCAL_ENV_KEYS),
        "local_env_loaded_keys": sorted(LOCAL_ENV_KEYS),
    }


def deployment_preflight(
    markets: str = "KR",
    require_blob: bool = False,
    strict: bool = False,
    root: Path | None = None,
) -> dict[str, Any]:
    project_root = root or Path.cwd()
    checks: list[dict[str, Any]] = []

    def add_check(name: str, ok: bool, required: bool, detail: str) -> None:
        checks.append(
            {
                "name": name,
                "ok": ok,
                "required": required,
                "detail": detail,
            }
        )

    env_example = project_root / ".env.example"
    env_keys = _env_example_keys(env_example)
    add_check(
        "env_example_present",
        env_example.exists(),
        True,
        ".env.example documents deployment configuration",
    )
    for key in sorted(STATIC_ENV_EXAMPLE_KEYS):
        add_check(
            f"env_example:{key}",
            key in env_keys,
            True,
            "required deployment variable should be documented",
        )

    vercel_config = project_root / "vercel.json"
    add_check("vercel_json_present", vercel_config.exists(), True, "Vercel project config")
    add_check(
        "vercel_api_route",
        _file_contains(vercel_config, '"/api/(.*)"')
        and _file_contains(vercel_config, '"/api/index.py"'),
        True,
        "FastAPI read API must route through api/index.py",
    )
    add_check(
        "vercel_next_route_fallback_absent",
        not _file_contains(vercel_config, '"/apps/web/$1"'),
        True,
        "Next App Router routes must be served by the filesystem handler, not an apps/web catch-all",
    )
    add_check(
        "vercel_web_build",
        _file_contains(vercel_config, "pnpm --filter @personal-fastgraphs/web build"),
        True,
        "Vercel build should compile the Next.js app",
    )
    add_check(
        "api_index_present",
        (project_root / "api/index.py").exists(),
        True,
        "Vercel Python function entrypoint",
    )
    add_check(
        "fastapi_app_present",
        (project_root / "services/api/main.py").exists(),
        True,
        "FastAPI app module",
    )
    worker_action = project_root / ".github/workflows/ingestion-worker.yml"
    package_json = project_root / "package.json"
    add_check(
        "ingestion_worker_action_present",
        worker_action.exists(),
        True,
        "manual GitHub Actions ingestion worker",
    )
    add_check(
        "blob_sync_dry_run_gate",
        _file_contains_in_order(worker_action, ["pnpm blob:sync:dry-run", "pnpm blob:sync"]),
        True,
        "GitHub Actions Blob upload should validate the queue before uploading",
    )
    add_check(
        "deploy_gate_action_present",
        _file_contains(worker_action, "deploy_gate")
        and _file_contains(worker_action, "deploy-gate"),
        True,
        "GitHub Actions should expose the combined preflight and source coverage gate",
    )
    add_check(
        "normalize_us_batch_action_present",
        _file_contains(worker_action, "normalize_us_batch")
        and _file_contains(worker_action, "normalize-us-batch")
        and _file_contains(worker_action, "--continue-on-error"),
        True,
        "GitHub Actions should expose one-run US MVP batch normalization",
    )
    add_check(
        "collect_sec_bulk_action_present",
        _file_contains(worker_action, "collect_sec_bulk")
        and _file_contains(worker_action, "collect-sec-bulk")
        and _file_contains(worker_action, "SEC_USER_AGENT"),
        True,
        "GitHub Actions should expose SEC companyfacts/submissions bulk archive collection",
    )
    add_check(
        "load_sec_bulk_warehouse_action_present",
        _file_contains(worker_action, "load_sec_bulk_warehouse")
        and _file_contains(worker_action, "load-sec-bulk-warehouse")
        and _file_contains(package_json, '"load:sec:bulk"'),
        True,
        "GitHub Actions should expose SEC bulk warehouse parsing",
    )
    add_check(
        "run_source_e2e_action_present",
        _file_contains(worker_action, "run_source_e2e")
        and _file_contains(worker_action, "run-source-e2e")
        and _file_contains(package_json, '"e2e:source:kr"')
        and _file_contains(package_json, '"e2e:source:kr:check"'),
        True,
        "GitHub Actions and package scripts should expose the source-backed KR E2E runner",
    )
    kr_e2e_action = project_root / ".github/workflows/kr-e2e.yml"
    add_check(
        "kr_e2e_action_present",
        kr_e2e_action.exists()
        and _file_contains(kr_e2e_action, "KR Top 10 E2E")
        and _file_contains(kr_e2e_action, "doctor --markets KR --strict")
        and _file_contains(kr_e2e_action, "run-source-e2e")
        and _file_contains(kr_e2e_action, "--market KR")
        and _file_contains(kr_e2e_action, "source-coverage")
        and _file_contains(kr_e2e_action, "--market KR")
        and _file_contains(kr_e2e_action, "005930.KS,000660.KS,402340.KS"),
        True,
        "GitHub Actions should expose a dedicated KR Top 10 E2E workflow",
    )
    add_check(
        "run_p1_e2e_action_present",
        _file_contains(worker_action, "run_p1_e2e")
        and _file_contains(worker_action, "run-p1-e2e")
        and _file_contains(package_json, '"e2e:p1"'),
        True,
        "GitHub Actions should expose the AAPL/005930/7203 cross-market P1 E2E runner",
    )
    add_check(
        "data_lake_plan_action_present",
        _file_contains(worker_action, "data_lake_plan")
        and _file_contains(worker_action, "data-lake-plan")
        and _file_contains(worker_action, "storage/ingestion_plans/data_lake_plan.json"),
        True,
        "GitHub Actions should expose data lake planning without requiring download",
    )
    add_check(
        "collect_fred_action_present",
        _file_contains(worker_action, "collect_fred")
        and _file_contains(worker_action, "collect-fred")
        and _file_contains(worker_action, "FRED_API_KEY"),
        True,
        "GitHub Actions should expose source-backed FRED macro collection",
    )
    add_check(
        "collect_ecos_action_present",
        _file_contains(worker_action, "collect_ecos")
        and _file_contains(worker_action, "collect-ecos")
        and _file_contains(worker_action, "ECOS_API_KEY"),
        True,
        "GitHub Actions should expose source-backed ECOS macro/industry collection",
    )
    add_check(
        "collect_kosis_action_present",
        _file_contains(worker_action, "collect_kosis")
        and _file_contains(worker_action, "collect-kosis")
        and _file_contains(worker_action, "KOSIS_API_KEY"),
        True,
        "GitHub Actions should expose source-backed KOSIS macro/industry collection",
    )
    add_check(
        "collect_estat_action_present",
        _file_contains(worker_action, "collect_estat")
        and _file_contains(worker_action, "collect-estat")
        and _file_contains(worker_action, "ESTAT_APP_ID"),
        True,
        "GitHub Actions should expose source-backed e-Stat macro/industry collection",
    )
    add_check(
        "collect_stooq_action_present",
        _file_contains(worker_action, "collect_stooq_prices")
        and _file_contains(worker_action, "collect-stooq-prices"),
        True,
        "GitHub Actions should expose source-backed Stooq price collection",
    )
    add_check(
        "collect_fdr_action_present",
        _file_contains(worker_action, "collect_fdr_prices")
        and _file_contains(worker_action, "collect-fdr-prices"),
        True,
        "GitHub Actions should expose FinanceDataReader price bootstrap collection",
    )
    add_check(
        "collect_pykrx_action_present",
        _file_contains(worker_action, "collect_pykrx_prices")
        and _file_contains(worker_action, "collect-pykrx-prices"),
        True,
        "GitHub Actions should expose source-backed pykrx KR price collection",
    )
    add_check(
        "collect_marcap_action_present",
        _file_contains(worker_action, "collect_marcap")
        and _file_contains(worker_action, "collect-marcap"),
        True,
        "GitHub Actions should expose FinanceData marcap KR market cap collection",
    )
    add_check(
        "collect_jquants_action_present",
        _file_contains(worker_action, "collect_jquants")
        and _file_contains(worker_action, "collect-jquants"),
        True,
        "GitHub Actions should expose source-backed J-Quants JP collection",
    )
    add_check(
        "collect_edinet_action_present",
        _file_contains(worker_action, "collect_edinet")
        and _file_contains(worker_action, "collect-edinet"),
        True,
        "GitHub Actions should expose source-backed EDINET JP filing collection",
    )
    add_check(
        "import_fnguide_action_present",
        _file_contains(worker_action, "import_fnguide_export")
        and _file_contains(worker_action, "import-fnguide-export"),
        True,
        "GitHub Actions should expose user-supplied FnGuide/DataGuide import",
    )
    add_check(
        "export_consensus_template_action_present",
        _file_contains(worker_action, "export_consensus_template")
        and _file_contains(worker_action, "export-consensus-template"),
        True,
        "GitHub Actions should expose source-backed forecast CSV template generation",
    )
    add_check(
        "consensus_workpaper_action_present",
        _file_contains(worker_action, "consensus_workpaper")
        and _file_contains(worker_action, "consensus-workpaper"),
        True,
        "GitHub Actions should expose source-backed forecast evidence workpaper generation",
    )
    add_check(
        "forecast_gate_required_for_deploy",
        _file_contains(worker_action, "require_consensus_forecast")
        and _file_contains(worker_action, "--require-consensus-forecast")
        and _file_contains(package_json, '"deploy:gate"')
        and _file_contains(package_json, "--require-consensus-forecast"),
        True,
        "deploy gates should require 1Y-5Y consensus forecast coverage",
    )
    add_check(
        "source_secret_audit_action_present",
        _file_contains(worker_action, "secret_audit")
        and _file_contains(worker_action, "secret-audit")
        and _file_contains(package_json, '"secret:audit"'),
        True,
        "GitHub Actions and package scripts should expose stored source metadata secret audit",
    )
    secret_audit = source_metadata_secret_audit(root=project_root)
    add_check(
        "source_metadata_secret_audit",
        secret_audit["status"] == "passed",
        True,
        (
            f"checked_files={secret_audit['checked_files']} "
            f"findings={len(secret_audit['findings'])} "
            f"skipped_files={len(secret_audit['skipped_files'])}"
        ),
    )

    heads = _alembic_heads(project_root / "db/versions")
    add_check(
        "alembic_single_head",
        len(heads) == 1,
        True,
        f"heads={heads or []}",
    )
    add_check(
        "production_fixture_fallback_documented_false",
        _env_file_value(env_example, "ALLOW_FIXTURE_FALLBACK") == "false",
        True,
        "production should not opt into fixture fallback",
    )

    runtime = doctor(
        markets=markets,
        check_db=False,
        require_blob=require_blob,
        strict=strict,
    )
    private_checks = _private_runtime_checks(strict)
    checks.extend(private_checks)

    blocking = [check for check in checks if check["required"] and not check["ok"]]
    missing_required = [
        *runtime["missing_required"],
        *[check["name"] for check in blocking],
    ]
    return {
        "status": "ok" if not missing_required else "needs_configuration",
        "mode": "strict" if strict else "static",
        "markets": runtime["markets"],
        "runtime": runtime,
        "checks": checks,
        "missing_required": missing_required,
    }


def source_coverage_report(
    tickers: str | list[str] | None = None,
    *,
    market: str = "KR",
    min_historical_years: int = 3,
    min_forecast_years: int = 5,
    require_consensus_forecast: bool = False,
) -> dict[str, Any]:
    expected_tickers = normalize_coverage_tickers(tickers, market=market)
    if min_historical_years < 1 or min_forecast_years < 1:
        raise ValueError("min_historical_years and min_forecast_years must be positive")
    coverage = source_coverage_from_postgres(
        expected_tickers,
        min_historical_years=min_historical_years,
        min_forecast_years=min_forecast_years,
        require_consensus_forecast=require_consensus_forecast,
    )
    if coverage is not None:
        return coverage
    if market.strip().upper() == "KR":
        warehouse_rows = source_coverage_rows_from_kr_warehouse(expected_tickers)
        if warehouse_rows:
            warehouse_rows = overlay_local_consensus_counts(
                warehouse_rows,
                expected_tickers,
                min_forecast_years=min_forecast_years,
            )
            report = build_source_coverage_report(
                warehouse_rows,
                expected_tickers,
                min_historical_years=min_historical_years,
                min_forecast_years=min_forecast_years,
                require_consensus_forecast=require_consensus_forecast,
                postgres_reachable=False,
                error="not_configured_local_warehouse",
            )
            report["data_backend"] = "kr_valuation_warehouse"
            report["data_mode"] = "local_source_backed_warehouse"
            report["local_warehouse"] = {
                "enabled": True,
                "source": "DuckDB/Parquet KR valuation warehouse",
                "note": (
                    "Development E2E proof uses local source-traced warehouse rows. "
                    "Protected deployment still requires Postgres/Neon coverage."
                ),
            }
            if any(row.get("local_consensus_overlay_ready") for row in warehouse_rows):
                report["local_overlays"] = {
                    "forecast_csv": "enabled",
                    "production_db_pending": True,
                }
            return report
    return build_source_coverage_report(
        [],
        expected_tickers,
        min_historical_years=min_historical_years,
        min_forecast_years=min_forecast_years,
        require_consensus_forecast=require_consensus_forecast,
        postgres_reachable=False,
        error="not_configured",
    )


def collect_market_documents(
    market: str,
    ticker: str,
    start_year: int,
    end_year: int,
    *,
    persist: bool = False,
    force_refresh: bool = False,
) -> dict[str, Any]:
    normalized_market = market.upper()
    normalized_ticker = ticker.upper()
    documents = _collect_connector_documents(
        normalized_market,
        normalized_ticker,
        start_year,
        end_year,
        force_refresh,
    )
    persisted = (
        _persist_connector_documents(normalized_market, normalized_ticker, documents)
        if persist
        else []
    )
    raw_documents = [] if persist else _cache_raw_documents(documents)
    return {
        "status": "ok",
        "ticker": normalized_ticker,
        "market": normalized_market,
        "documents": [_document_summary(document) for document in documents],
        "raw_documents": raw_documents,
        "persisted": persisted,
    }


def collect_opendart_dividends(
    tickers: str | list[str],
    start_year: int,
    end_year: int,
    *,
    persist: bool = False,
    force_refresh: bool = False,
    sleep_seconds: float = 0.5,
) -> dict[str, Any]:
    requested_tickers = _split_csv(tickers)
    documents: list[ConnectorDocument] = []
    connector = OpenDartConnector()
    for index, ticker in enumerate(requested_tickers):
        documents.extend(
            connector.collect_dividends(
                ConnectorRequest(
                    ticker=ticker,
                    market="KR",
                    start_year=start_year,
                    end_year=end_year,
                    force_refresh=force_refresh,
                )
            )
        )
        if sleep_seconds > 0 and index < len(requested_tickers) - 1:
            time.sleep(sleep_seconds)
    raw_documents = _cache_raw_documents(documents)
    return {
        "status": "ok",
        "market": "KR",
        "tickers": requested_tickers,
        "documents": [_document_summary(document) for document in documents],
        "raw_documents": raw_documents,
        "dividend_rows": sum(_opendart_dividend_row_count(document) for document in documents),
        "persisted": [] if not persist else [],
    }


def run_p1_e2e(
    *,
    us_ticker: str = "AAPL",
    kr_ticker: str = "005930.KS",
    jp_ticker: str = "7203.T",
    start_year: int = 2020,
    end_year: int = 2025,
    policy: str = "street_comparable",
    persist: bool = False,
    force_refresh: bool = False,
    continue_on_error: bool = False,
    require_consensus_forecast: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    us = us_ticker.upper()
    kr = kr_ticker.upper()
    jp = jp_ticker.upper()
    requested_tickers = normalize_coverage_tickers([us, kr, jp])
    years = f"{start_year}:{end_year}"
    steps = _p1_e2e_steps(
        us,
        kr,
        jp,
        years,
        persist=persist,
        force_refresh=force_refresh,
        policy=policy,
        continue_on_error=continue_on_error,
        require_consensus_forecast=require_consensus_forecast,
    )
    prerequisites = _p1_e2e_prerequisites(
        persist=persist,
        require_consensus_forecast=require_consensus_forecast,
    )
    missing_required = [
        item["name"]
        for item in prerequisites
        if item["required"] and not bool(item["ok"])
    ]
    base_summary: dict[str, Any] = {
        "markets": ["US", "KR", "JP"],
        "tickers": requested_tickers,
        "years": years,
        "policy": policy,
        "persist": persist,
        "force_refresh": force_refresh,
        "continue_on_error": continue_on_error,
        "require_consensus_forecast": require_consensus_forecast,
        "dry_run": dry_run,
        "prerequisites": prerequisites,
        "missing_required": missing_required,
        "steps": steps,
    }
    if dry_run:
        return {
            "status": "needs_configuration" if missing_required else "planned",
            **base_summary,
            "executed_steps": [],
            "results": [],
            "coverage": None,
        }
    if missing_required:
        return {
            "status": "needs_configuration",
            **base_summary,
            "executed_steps": [],
            "results": [],
            "coverage": None,
        }

    results: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    def run_step(step_id: str, callback) -> bool:
        try:
            payload = callback()
            payload_status = (
                str(payload.get("status", "ok"))
                if isinstance(payload, dict)
                else "ok"
            )
            results.append({"id": step_id, "status": payload_status, "result": payload})
            if payload_status in {"failed", "partial"}:
                failed.append(
                    {
                        "id": step_id,
                        "status": payload_status,
                        "error_type": "StepStatus",
                        "error": f"step returned status={payload_status}",
                    }
                )
                return continue_on_error
            return True
        except Exception as exc:
            failure = {
                "id": step_id,
                "status": "failed",
                "error_type": exc.__class__.__name__,
                "error": str(exc),
            }
            results.append(failure)
            failed.append(failure)
            return continue_on_error

    if not run_step(
        "run_source_e2e_us",
        lambda: run_source_e2e(
            market="US",
            tickers=us,
            start_year=start_year,
            end_year=end_year,
            policy=policy,
            persist=persist,
            force_refresh=force_refresh,
            continue_on_error=continue_on_error,
            require_consensus_forecast=require_consensus_forecast,
        ),
    ):
        return {**base_summary, "status": "failed", "results": results, "failed": failed}
    if not run_step(
        "collect_opendart_kr",
        lambda: collect_market_documents(
            "KR",
            kr,
            start_year,
            end_year,
            persist=persist,
            force_refresh=force_refresh,
        ),
    ):
        return {**base_summary, "status": "failed", "results": results, "failed": failed}
    if not run_step(
        "collect_pykrx_prices_kr",
        lambda: collect_pykrx_prices(
            kr,
            start_year,
            end_year,
            persist=persist,
            force_refresh=force_refresh,
            sleep_seconds=0,
        ),
    ):
        return {**base_summary, "status": "failed", "results": results, "failed": failed}
    if not run_step(
        "collect_marcap_kr",
        lambda: collect_marcap_data(
            kr,
            start_year,
            end_year,
            persist=persist,
            force_refresh=force_refresh,
        ),
    ):
        return {**base_summary, "status": "failed", "results": results, "failed": failed}
    if not run_step(
        "collect_jquants_jp",
        lambda: collect_jquants_data(
            jp,
            start_year,
            end_year,
            persist=persist,
            force_refresh=force_refresh,
        ),
    ):
        return {**base_summary, "status": "failed", "results": results, "failed": failed}
    if not run_step(
        "collect_edinet_jp",
        lambda: collect_edinet_filings(
            jp,
            start_year,
            end_year,
            persist=persist,
            force_refresh=force_refresh,
        ),
    ):
        return {**base_summary, "status": "failed", "results": results, "failed": failed}
    if not run_step(
        "collect_stooq_prices_jp",
        lambda: collect_stooq_prices(
            jp,
            "JP",
            start_year,
            end_year,
            persist=persist,
            force_refresh=force_refresh,
        ),
    ):
        return {**base_summary, "status": "failed", "results": results, "failed": failed}

    coverage: dict[str, Any] | None = None

    def capture_coverage() -> dict[str, Any]:
        nonlocal coverage
        coverage = source_coverage_report(
            tickers=requested_tickers,
            require_consensus_forecast=require_consensus_forecast,
        )
        return coverage

    if not run_step("source_coverage", capture_coverage):
        return {**base_summary, "status": "failed", "results": results, "failed": failed}

    coverage_ready = bool(coverage and coverage["status"] == "ready")
    return {
        **base_summary,
        "status": "ok" if coverage_ready and not failed else "needs_source_data",
        "executed_steps": [row["id"] for row in results],
        "results": results,
        "failed": failed,
        "coverage": coverage,
    }


def run_source_e2e(
    *,
    market: str = "KR",
    tickers: str | list[str] | None = None,
    start_year: int = 2020,
    end_year: int = 2025,
    policy: str = "street_comparable",
    persist: bool = False,
    force_refresh: bool = False,
    continue_on_error: bool = False,
    require_consensus_forecast: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    normalized_market = market.upper()
    if normalized_market not in {"US", "KR", "JP"}:
        raise ValueError("run_source_e2e currently supports market=US, market=KR, or market=JP")

    requested_tickers = normalize_coverage_tickers(tickers, market=normalized_market)
    years = f"{start_year}:{end_year}"
    steps = _source_e2e_steps(
        normalized_market,
        requested_tickers,
        years,
        persist=persist,
        force_refresh=force_refresh,
        policy=policy,
        continue_on_error=continue_on_error,
        require_consensus_forecast=require_consensus_forecast,
    )
    prerequisites = _source_e2e_prerequisites(
        normalized_market,
        persist=persist,
        require_consensus_forecast=require_consensus_forecast,
    )
    missing_required = [
        item["name"]
        for item in prerequisites
        if item["required"] and not bool(item["ok"])
    ]
    base_summary: dict[str, Any] = {
        "market": normalized_market,
        "tickers": requested_tickers,
        "years": years,
        "policy": policy,
        "persist": persist,
        "force_refresh": force_refresh,
        "continue_on_error": continue_on_error,
        "require_consensus_forecast": require_consensus_forecast,
        "dry_run": dry_run,
        **_local_env_status(),
        "prerequisites": prerequisites,
        "missing_required": missing_required,
        "steps": steps,
    }
    if dry_run:
        local_raw_evidence = _source_e2e_local_raw_evidence(
            normalized_market,
            requested_tickers,
            start_year,
            end_year,
            persist=persist,
        )
        local_warehouse_coverage = (
            _source_e2e_local_warehouse_coverage(
                normalized_market,
                requested_tickers,
                persist=persist,
                require_consensus_forecast=require_consensus_forecast,
            )
            if not missing_required
            else None
        )
        dry_run_status = "needs_configuration" if missing_required else "planned"
        if local_warehouse_coverage and local_warehouse_coverage.get("status") == "ready":
            dry_run_status = "local_warehouse_ready"
        elif local_raw_evidence and local_raw_evidence["status"] == "ok":
            dry_run_status = "local_raw_ready"
        elif local_raw_evidence and not missing_required:
            dry_run_status = "needs_source_data"
        completion_gate = _source_e2e_completion_gate(
            normalized_market,
            requested_tickers,
            years,
            missing_required,
            require_consensus_forecast=require_consensus_forecast,
            dry_run=True,
            local_raw_evidence=local_raw_evidence,
            coverage=local_warehouse_coverage,
        )
        return {
            "status": dry_run_status,
            **base_summary,
            "executed_steps": [],
            "results": [],
            "coverage": local_warehouse_coverage,
            "local_raw_evidence": local_raw_evidence,
            "local_warehouse_coverage": local_warehouse_coverage,
            "completion_gate": completion_gate,
        }
    if missing_required:
        return {
            "status": "needs_configuration",
            **base_summary,
            "executed_steps": [],
            "results": [],
            "coverage": None,
            "completion_gate": _source_e2e_completion_gate(
                normalized_market,
                requested_tickers,
                years,
                missing_required,
                require_consensus_forecast=require_consensus_forecast,
                dry_run=False,
                local_raw_evidence=None,
                coverage=None,
            ),
        }

    results: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    def run_step(step_id: str, callback) -> bool:
        try:
            payload = callback()
            payload_status = (
                str(payload.get("status", "ok"))
                if isinstance(payload, dict)
                else "ok"
            )
            results.append({"id": step_id, "status": payload_status, "result": payload})
            if payload_status in {"failed", "partial"}:
                failed.append(
                    {
                        "id": step_id,
                        "status": payload_status,
                        "error_type": "StepStatus",
                        "error": f"step returned status={payload_status}",
                    }
                )
                return continue_on_error
            return True
        except Exception as exc:
            failure = {
                "id": step_id,
                "status": "failed",
                "error_type": exc.__class__.__name__,
                "error": str(exc),
            }
            results.append(failure)
            failed.append(failure)
            return continue_on_error

    joined = ",".join(requested_tickers)
    if normalized_market == "US":
        if not run_step(
            "collect_sec_bulk",
            lambda: collect_sec_bulk_archives(
                "companyfacts,submissions",
                persist=persist,
                force_refresh=force_refresh,
            ),
        ):
            return {**base_summary, "status": "failed", "results": results, "failed": failed}
        if not run_step(
            "load_sec_bulk_warehouse",
            lambda: load_sec_bulk_warehouse(tickers=joined, persist=persist),
        ):
            return {**base_summary, "status": "failed", "results": results, "failed": failed}
        if not run_step(
            "normalize_us_batch",
            lambda: normalize_us_batch_run(
                joined,
                start_year,
                end_year,
                policy=policy,
                persist=persist,
                force_refresh=force_refresh,
                continue_on_error=continue_on_error,
            ),
        ):
            return {**base_summary, "status": "failed", "results": results, "failed": failed}
        if not run_step(
            "collect_stooq_prices_us",
            lambda: collect_stooq_prices(
                joined,
                "US",
                start_year,
                end_year,
                persist=persist,
                force_refresh=force_refresh,
            ),
        ):
            return {**base_summary, "status": "failed", "results": results, "failed": failed}
    elif normalized_market == "KR":
        def collect_opendart_batch() -> dict[str, Any]:
            collected: list[dict[str, Any]] = []
            batch_failed: list[dict[str, Any]] = []
            for ticker in requested_tickers:
                try:
                    collected.append(
                        collect_market_documents(
                            "KR",
                            ticker,
                            start_year,
                            end_year,
                            persist=persist,
                            force_refresh=force_refresh,
                        )
                    )
                except Exception as exc:
                    failure = {
                        "ticker": ticker,
                        "status": "failed",
                        "error_type": exc.__class__.__name__,
                        "error": str(exc),
                    }
                    batch_failed.append(failure)
                    if not continue_on_error:
                        raise
            return {
                "status": "partial" if batch_failed else "ok",
                "market": "KR",
                "tickers": requested_tickers,
                "results": collected,
                "failed": batch_failed,
            }

        if not run_step("collect_opendart_kr", collect_opendart_batch):
            return {**base_summary, "status": "failed", "results": results, "failed": failed}
        if not run_step(
            "collect_pykrx_prices_kr",
            lambda: collect_pykrx_prices(
                joined,
                start_year,
                end_year,
                persist=persist,
                force_refresh=force_refresh,
                sleep_seconds=0,
            ),
        ):
            return {**base_summary, "status": "failed", "results": results, "failed": failed}
        if not run_step(
            "collect_marcap_kr",
            lambda: collect_marcap_data(
                joined,
                start_year,
                end_year,
                persist=persist,
                force_refresh=force_refresh,
            ),
        ):
            return {**base_summary, "status": "failed", "results": results, "failed": failed}
        if not run_step(
            "build_kr_valuation_inputs",
            lambda: build_kr_valuation_inputs(
                joined,
                start_year,
                end_year,
            ),
        ):
            return {**base_summary, "status": "failed", "results": results, "failed": failed}
        if not run_step(
            "load_kr_valuation_postgres",
            lambda: load_kr_valuation_cache_to_postgres(
                joined,
                dry_run=not persist,
                strict=True,
            ),
        ):
            return {**base_summary, "status": "failed", "results": results, "failed": failed}
    else:
        if not run_step(
            "collect_jquants_jp",
            lambda: collect_jquants_data(
                joined,
                start_year,
                end_year,
                persist=persist,
                force_refresh=force_refresh,
            ),
        ):
            return {**base_summary, "status": "failed", "results": results, "failed": failed}
        if not run_step(
            "collect_edinet_jp",
            lambda: collect_edinet_filings(
                joined,
                start_year,
                end_year,
                persist=persist,
                force_refresh=force_refresh,
            ),
        ):
            return {**base_summary, "status": "failed", "results": results, "failed": failed}
        if not run_step(
            "collect_stooq_prices_jp",
            lambda: collect_stooq_prices(
                joined,
                "JP",
                start_year,
                end_year,
                persist=persist,
                force_refresh=force_refresh,
            ),
        ):
            return {**base_summary, "status": "failed", "results": results, "failed": failed}

    coverage: dict[str, Any] | None = None

    def capture_coverage() -> dict[str, Any]:
        nonlocal coverage
        coverage = source_coverage_report(
            tickers=joined,
            market=normalized_market,
            require_consensus_forecast=require_consensus_forecast,
        )
        return coverage

    if not run_step("source_coverage", capture_coverage):
        return {**base_summary, "status": "failed", "results": results, "failed": failed}

    coverage_ready = bool(coverage and coverage["status"] == "ready")
    return {
        **base_summary,
        "status": "ok" if coverage_ready and not failed else "needs_source_data",
        "executed_steps": [row["id"] for row in results],
        "results": results,
        "failed": failed,
        "coverage": coverage,
        "completion_gate": _source_e2e_completion_gate(
            normalized_market,
            requested_tickers,
            years,
            missing_required,
            require_consensus_forecast=require_consensus_forecast,
            dry_run=False,
            local_raw_evidence=None,
            coverage=coverage,
        ),
    }


def _source_e2e_local_raw_evidence(
    market: str,
    tickers: list[str],
    start_year: int,
    end_year: int,
    *,
    persist: bool,
) -> dict[str, Any] | None:
    """Report offline KR source evidence during local dry-runs.

    This intentionally does not write normalized rows. It gives the operator a
    fast answer to: "Can the already append-only raw files support the next
    valuation-map build?"
    """
    if market != "KR" or persist:
        return None

    raw_summary = inspect_raw_kr_evidence(
        tickers,
        start_year,
        end_year,
        require_opendart=True,
    )
    summary = raw_summary.get("summary", {})
    expected = int(summary.get("tickers_expected") or 0)
    valuation_ready = int(summary.get("valuation_ready") or 0)
    local_status = "ok" if expected > 0 and valuation_ready == expected and raw_summary.get("status") == "ok" else raw_summary.get("status", "missing")
    return {
        "status": local_status,
        "mode": "offline_raw_evidence_check",
        "note": "Dry-run did not collect from network or write DB rows; it inspected local append-only KR raw evidence.",
        "raw_evidence": raw_summary,
        "next_actions": raw_summary.get("next_actions", []),
    }


def _source_e2e_local_warehouse_coverage(
    market: str,
    tickers: list[str],
    *,
    persist: bool,
    require_consensus_forecast: bool,
) -> dict[str, Any] | None:
    if market != "KR" or persist:
        return None
    coverage = source_coverage_report(
        tickers=tickers,
        market=market,
        require_consensus_forecast=require_consensus_forecast,
    )
    if coverage.get("data_backend") != "kr_valuation_warehouse":
        return None
    return coverage


def _source_e2e_completion_gate(
    market: str,
    tickers: list[str],
    years: str,
    missing_required: list[str],
    *,
    require_consensus_forecast: bool,
    dry_run: bool,
    local_raw_evidence: dict[str, Any] | None,
    coverage: dict[str, Any] | None,
) -> dict[str, Any]:
    """Summarize the proof path from source collection to UI-ready valuation rows."""
    coverage_status = coverage.get("status") if isinstance(coverage, dict) else None
    local_raw_ready = bool(local_raw_evidence and local_raw_evidence.get("status") == "ok")
    if coverage_status == "ready":
        status = "complete"
    elif missing_required and not local_raw_ready:
        status = "blocked_by_configuration"
    elif market == "KR" and local_raw_ready:
        status = "ready_for_valuation_cache_build"
    elif dry_run:
        status = "planned"
    else:
        status = "needs_source_data"

    return {
        "status": status,
        "market": market,
        "tickers": tickers,
        "years": years,
        "coverage_status": coverage_status,
        "local_raw_ready": local_raw_ready,
        "missing_required": missing_required,
        "required_proofs": _source_e2e_required_proofs(market, require_consensus_forecast),
        "next_commands": _source_e2e_completion_commands(
            market,
            tickers,
            years,
            require_consensus_forecast=require_consensus_forecast,
        ),
        "deployment_commands": _source_e2e_deployment_commands(
            market,
            tickers,
            require_consensus_forecast=require_consensus_forecast,
        ),
    }


def _source_e2e_required_proofs(market: str, require_consensus_forecast: bool) -> list[str]:
    proofs = [
        "raw source files are append-only and source_trace-ready",
        "normalized valuation inputs build without rejected source_trace rows",
        "warehouse load succeeds and rejects non-production rows",
        "Postgres valuation load succeeds with storage-ready source_trace rows",
        "local valuation-map API proof prefers source-backed warehouse rows",
    ]
    if require_consensus_forecast:
        proofs.append("1Y-5Y consensus forecast snapshots are imported from traceable sources")
    if market == "KR":
        return [
            "OpenDART financial facts, pykrx prices, and marcap evidence are present",
            *proofs,
        ]
    return proofs


def _source_e2e_completion_commands(
    market: str,
    tickers: list[str],
    years: str,
    *,
    require_consensus_forecast: bool,
) -> list[dict[str, str]]:
    joined = ",".join(tickers)
    consensus_flag = " --require-consensus-forecast" if require_consensus_forecast else ""
    if market != "KR":
        return [
            {
                "id": "source_coverage",
                "command": (
                    "python -m services.ingestion_worker.cli source-coverage "
                    f"--market {market} --tickers {joined}{consensus_flag} --strict"
                ),
                "proves": "source-backed coverage is ready for the selected market",
            }
        ]
    return [
        {
            "id": "build_kr_valuation_inputs",
            "command": (
                "python -m services.ingestion_worker.cli build-kr-valuation-inputs "
                f"--tickers {joined} --years {years} --strict"
            ),
            "proves": "raw OpenDART/pykrx/marcap evidence can produce valuation-map input cache",
        },
        {
            "id": "load_kr_valuation_warehouse",
            "command": (
                "python -m services.ingestion_worker.cli load-kr-valuation-warehouse "
                f"--tickers {joined} --strict"
            ),
            "proves": "source-traced valuation inputs are available through DuckDB/Parquet warehouse",
        },
        {
            "id": "load_kr_valuation_postgres",
            "command": (
                "python -m services.ingestion_worker.cli load-kr-valuation-postgres "
                f"--tickers {joined} --strict"
            ),
            "proves": "source-traced valuation inputs are persisted to Neon/Postgres API tables",
        },
        {
            "id": "api_valuation_map_probe",
            "command": (
                "python -m pytest "
                "tests/api/test_api.py::test_kr_priority_valuation_map_uses_warehouse_before_cache -q"
            ),
            "proves": "valuation-map API prefers source-backed warehouse rows before cache fallback",
        },
    ]


def _source_e2e_deployment_commands(
    market: str,
    tickers: list[str],
    *,
    require_consensus_forecast: bool,
) -> list[dict[str, str]]:
    joined = ",".join(tickers)
    consensus_flag = " --require-consensus-forecast" if require_consensus_forecast else ""
    return [
        {
            "id": "load_kr_valuation_postgres",
            "command": (
                "python -m services.ingestion_worker.cli load-kr-valuation-postgres "
                f"--tickers {joined} --strict"
            ),
            "requires": "DATA_BACKEND=postgres and DATABASE_URL",
            "proves": "source-backed KR valuation cache is promoted into Postgres API tables",
        },
        {
            "id": "source_coverage_postgres",
            "command": (
                "python -m services.ingestion_worker.cli source-coverage "
                f"--market {market} --tickers {joined}{consensus_flag} --strict"
            ),
            "requires": "DATA_BACKEND=postgres and DATABASE_URL",
            "proves": "persisted Neon/Postgres source coverage is ready for deployment",
        }
    ]


def run_priority_e2e(
    *,
    markets: str = "KR,US,JP",
    start_year: int = 2020,
    end_year: int = 2025,
    policy: str = "street_comparable",
    persist: bool = False,
    force_refresh: bool = False,
    continue_on_error: bool = False,
    require_consensus_forecast: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    market_order = _parse_priority_e2e_markets(markets)
    years = f"{start_year}:{end_year}"
    planned_steps = [
        {
            "id": f"run_source_e2e_{market.lower()}",
            "market": market,
            "description": f"Run {market} Top 10 source-backed E2E in KR-US-JP priority order.",
            "command": _priority_e2e_market_command(
                market,
                years,
                policy=policy,
                persist=persist,
                force_refresh=force_refresh,
                continue_on_error=continue_on_error,
                require_consensus_forecast=require_consensus_forecast,
            ),
        }
        for market in market_order
    ]
    planned_steps.append(
        {
            "id": "source_coverage_all",
            "market": "ALL",
            "description": "Verify source-backed coverage across the selected priority universes.",
            "command": (
                "python -m services.ingestion_worker.cli source-coverage "
                "--market ALL"
                f"{' --require-consensus-forecast' if require_consensus_forecast else ''} "
                "--strict"
            ),
        }
    )
    base_summary: dict[str, Any] = {
        "scope": "top_market_cap_priority",
        "market_order": market_order,
        "years": years,
        "policy": policy,
        "persist": persist,
        "force_refresh": force_refresh,
        "continue_on_error": continue_on_error,
        "require_consensus_forecast": require_consensus_forecast,
        "dry_run": dry_run,
        **_local_env_status(),
        "steps": planned_steps,
    }

    market_results: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    missing_required: list[str] = []
    all_tickers: list[str] = []

    for market in market_order:
        summary = run_source_e2e(
            market=market,
            tickers="",
            start_year=start_year,
            end_year=end_year,
            policy=policy,
            persist=persist,
            force_refresh=force_refresh,
            continue_on_error=continue_on_error,
            require_consensus_forecast=require_consensus_forecast,
            dry_run=dry_run,
        )
        market_results.append(summary)
        for ticker in summary.get("tickers", []):
            if ticker not in all_tickers:
                all_tickers.append(ticker)
        for name in summary.get("missing_required", []):
            qualified = f"{market}:{name}"
            if qualified not in missing_required:
                missing_required.append(qualified)
        if summary["status"] in {"failed", "needs_configuration"}:
            failed.append(
                {
                    "id": f"run_source_e2e_{market.lower()}",
                    "market": market,
                    "status": summary["status"],
                    "missing_required": summary.get("missing_required", []),
                }
            )
            if not dry_run and not continue_on_error:
                break

    if dry_run:
        return {
            **base_summary,
            "status": "needs_configuration" if missing_required else "planned",
            "tickers": all_tickers,
            "missing_required": missing_required,
            "executed_markets": [],
            "market_results": market_results,
            "failed": failed,
            "coverage": None,
        }

    coverage: dict[str, Any] | None = None
    if all_tickers and not any(row["status"] == "needs_configuration" for row in failed):
        coverage = source_coverage_report(
            tickers=",".join(all_tickers),
            market="ALL",
            require_consensus_forecast=require_consensus_forecast,
        )

    coverage_ready = bool(coverage and coverage["status"] == "ready")
    if any(row["status"] == "failed" for row in failed):
        status = "failed"
    elif missing_required:
        status = "needs_configuration"
    elif coverage_ready:
        status = "ok"
    else:
        status = "needs_source_data"

    return {
        **base_summary,
        "status": status,
        "tickers": all_tickers,
        "missing_required": missing_required,
        "executed_markets": [
            row["market"]
            for row in market_results
            if row["status"] not in {"planned", "needs_configuration"}
        ],
        "market_results": market_results,
        "failed": failed,
        "coverage": coverage,
    }


def _source_e2e_steps(
    market: str,
    tickers: list[str],
    years: str,
    *,
    persist: bool,
    force_refresh: bool,
    policy: str,
    continue_on_error: bool,
    require_consensus_forecast: bool,
) -> list[dict[str, Any]]:
    joined = ",".join(tickers)
    persist_flag = " --persist" if persist else ""
    force_flag = " --force-refresh" if force_refresh else ""
    consensus_flag = " --require-consensus-forecast" if require_consensus_forecast else ""
    continue_flag = " --continue-on-error" if continue_on_error else ""
    postgres_load_mode = "" if persist else " --dry-run"
    if market == "KR":
        return [
            {
                "id": "collect_opendart_kr",
                "description": "Collect KR financial statement facts from OpenDART.",
                "command": (
                    "for each ticker in "
                    f"{joined}: python -m services.ingestion_worker.cli collect "
                    f"--market KR --ticker <ticker> --years {years}{persist_flag}{force_flag}"
                ),
            },
            {
                "id": "collect_pykrx_prices_kr",
                "description": "Collect KR source-backed daily OHLCV price bars from pykrx.",
                "command": (
                    "python -m services.ingestion_worker.cli collect-pykrx-prices "
                    f"--tickers {joined} --years {years}{persist_flag}{force_flag}"
                ),
            },
            {
                "id": "collect_marcap_kr",
                "description": "Collect KR market cap, listed shares, rank, and close-price evidence from FinanceData marcap.",
                "command": (
                    "python -m services.ingestion_worker.cli collect-marcap "
                    f"--tickers {joined} --years {years}{persist_flag}{force_flag}"
                ),
            },
            {
                "id": "build_kr_valuation_inputs",
                "description": "Build source-backed KR valuation input cache from collected raw evidence.",
                "command": (
                    "python -m services.ingestion_worker.cli build-kr-valuation-inputs "
                    f"--tickers {joined} --years {years} --strict"
                ),
            },
            {
                "id": "load_kr_valuation_postgres",
                "description": "Promote source-backed KR valuation cache into Postgres API tables.",
                "command": (
                    "python -m services.ingestion_worker.cli load-kr-valuation-postgres "
                    f"--tickers {joined}{postgres_load_mode} --strict"
                ),
            },
            {
                "id": "source_coverage",
                "description": "Verify KR source-backed Postgres coverage after ingestion.",
                "command": (
                    "python -m services.ingestion_worker.cli source-coverage "
                    f"--market KR --tickers {joined}{consensus_flag} --strict"
                ),
            },
        ]
    if market == "JP":
        return [
            {
                "id": "collect_jquants_jp",
                "description": "Collect JP daily quotes, statements, and dividends from J-Quants.",
                "command": (
                    "python -m services.ingestion_worker.cli collect-jquants "
                    f"--tickers {joined} --years {years}{persist_flag}{force_flag}"
                ),
            },
            {
                "id": "collect_edinet_jp",
                "description": "Collect JP EDINET filing metadata and XBRL-to-CSV packages.",
                "command": (
                    "python -m services.ingestion_worker.cli collect-edinet "
                    f"--tickers {joined} --years {years}{persist_flag}{force_flag}"
                ),
            },
            {
                "id": "collect_stooq_prices_jp",
                "description": "Collect JP source-backed daily price bars from Stooq.",
                "command": (
                    "python -m services.ingestion_worker.cli collect-stooq-prices "
                    f"--market JP --tickers {joined} --years {years}{persist_flag}{force_flag}"
                ),
            },
            {
                "id": "source_coverage",
                "description": "Verify JP source-backed coverage after ingestion.",
                "command": (
                    "python -m services.ingestion_worker.cli source-coverage "
                    f"--market JP --tickers {joined}{consensus_flag} --strict"
                ),
            },
        ]

    return [
        {
            "id": "collect_sec_bulk",
            "description": "Download SEC companyfacts/submissions raw archives.",
            "command": (
                "python -m services.ingestion_worker.cli collect-sec-bulk "
                f"--archives companyfacts,submissions{persist_flag}{force_flag}"
            ),
        },
        {
            "id": "load_sec_bulk_warehouse",
            "description": "Parse SEC bulk archives into financial_facts and metric_values.",
            "command": (
                "python -m services.ingestion_worker.cli load-sec-bulk-warehouse "
                f"--tickers {joined}{persist_flag}"
            ),
        },
        {
            "id": "normalize_us_batch",
            "description": "Run S1/S2/S4 adjusted operating EPS normalization.",
            "command": (
                "python -m services.ingestion_worker.cli normalize-us-batch "
                f"--tickers {joined} --years {years} --policy {policy}"
                f"{persist_flag}{force_flag}{continue_flag}"
            ),
        },
        {
            "id": "collect_stooq_prices_us",
            "description": "Collect source-backed daily US price bars from Stooq.",
            "command": (
                "python -m services.ingestion_worker.cli collect-stooq-prices "
                f"--market {market} --tickers {joined} --years {years}{persist_flag}{force_flag}"
            ),
        },
        {
            "id": "source_coverage",
            "description": "Verify source-backed coverage after ingestion.",
            "command": (
                "python -m services.ingestion_worker.cli source-coverage "
                f"--tickers {joined}{consensus_flag} --strict"
            ),
        },
    ]


def _source_e2e_prerequisites(
    market: str,
    *,
    persist: bool,
    require_consensus_forecast: bool,
) -> list[dict[str, Any]]:
    prerequisites: list[dict[str, Any]] = []

    def add(name: str, ok: bool, required: bool, detail: str) -> None:
        prerequisites.append({"name": name, "ok": ok, "required": required, "detail": detail})

    add(
        "DATA_BACKEND=postgres",
        os.getenv("DATA_BACKEND", "").lower() == "postgres",
        persist,
        "required when persisting source-backed rows to Neon/Postgres",
    )
    add(
        "DATABASE_URL",
        bool(os.getenv("DATABASE_URL")),
        persist,
        "required when persisting source-backed rows and verifying source coverage",
    )
    if market == "US":
        add(
            "SEC_USER_AGENT",
            bool(os.getenv("SEC_USER_AGENT")),
            True,
            "required for SEC EDGAR archive collection and US normalization",
        )
    if market == "KR":
        add(
            "OPENDART_API_KEY",
            _has_opendart_key(),
            True,
            "required for OpenDART financial statement collection; DART_API_KEY is accepted",
        )
    if market == "JP":
        add(
            "JQUANTS credentials",
            bool(os.getenv("JQUANTS_REFRESH_TOKEN"))
            or (bool(os.getenv("JQUANTS_EMAIL")) and bool(os.getenv("JQUANTS_PASSWORD"))),
            True,
            "required for J-Quants statements, dividends, and daily quotes",
        )
        add(
            "EDINET_API_KEY",
            bool(os.getenv("EDINET_API_KEY")),
            True,
            "required for EDINET filing metadata and XBRL-to-CSV collection",
        )
    if require_consensus_forecast:
        add(
            "traceable forecast source CSV",
            False,
            False,
            "import consensus snapshots separately; the E2E runner does not synthesize estimates",
        )
    return prerequisites


def _priority_e2e_market_command(
    market: str,
    years: str,
    *,
    policy: str,
    persist: bool,
    force_refresh: bool,
    continue_on_error: bool,
    require_consensus_forecast: bool,
) -> str:
    flags = [
        f"--market {market}",
        f"--years {years}",
        f"--policy {policy}",
    ]
    if persist:
        flags.append("--persist")
    if force_refresh:
        flags.append("--force-refresh")
    if continue_on_error:
        flags.append("--continue-on-error")
    if require_consensus_forecast:
        flags.append("--require-consensus-forecast")
    flags.append("--strict")
    return "python -m services.ingestion_worker.cli run-source-e2e " + " ".join(flags)


def _parse_priority_e2e_markets(markets: str) -> list[str]:
    requested = {
        market.strip().upper()
        for market in markets.split(",")
        if market.strip()
    }
    if not requested or "ALL" in requested:
        requested = set(PRIORITY_E2E_MARKET_ORDER)
    allowed = {*PRIORITY_E2E_MARKET_ORDER}
    unknown = sorted(requested - allowed)
    if unknown:
        raise ValueError(
            "run-priority-e2e markets must be a subset of KR,US,JP or ALL; "
            f"received: {','.join(unknown)}"
        )
    return [market for market in PRIORITY_E2E_MARKET_ORDER if market in requested]


def _p1_e2e_steps(
    us_ticker: str,
    kr_ticker: str,
    jp_ticker: str,
    years: str,
    *,
    persist: bool,
    force_refresh: bool,
    policy: str,
    continue_on_error: bool,
    require_consensus_forecast: bool,
) -> list[dict[str, Any]]:
    persist_flag = " --persist" if persist else ""
    force_flag = " --force-refresh" if force_refresh else ""
    continue_flag = " --continue-on-error" if continue_on_error else ""
    consensus_flag = " --require-consensus-forecast" if require_consensus_forecast else ""
    coverage_tickers = ",".join([us_ticker, kr_ticker, jp_ticker])
    return [
        {
            "id": "run_source_e2e_us",
            "description": (
                "Run US AAPL through SEC, adjusted EPS, price, and coverage."
            ),
            "command": (
                "python -m services.ingestion_worker.cli run-source-e2e "
                f"--market US --tickers {us_ticker} --years {years} --policy {policy}"
                f"{persist_flag}{force_flag}{continue_flag}{consensus_flag}"
            ),
        },
        {
            "id": "collect_opendart_kr",
            "description": "Collect Samsung Electronics source filings/facts from OpenDART.",
            "command": (
                "python -m services.ingestion_worker.cli collect "
                f"--market KR --ticker {kr_ticker} --years {years}{persist_flag}{force_flag}"
            ),
        },
        {
            "id": "collect_pykrx_prices_kr",
            "description": "Collect KR price bars from pykrx.",
            "command": (
                "python -m services.ingestion_worker.cli collect-pykrx-prices "
                f"--tickers {kr_ticker} --years {years}{persist_flag}{force_flag}"
            ),
        },
        {
            "id": "collect_marcap_kr",
            "description": "Collect KR market cap and price rows from marcap.",
            "command": (
                "python -m services.ingestion_worker.cli collect-marcap "
                f"--tickers {kr_ticker} --years {years}{persist_flag}{force_flag}"
            ),
        },
        {
            "id": "collect_jquants_jp",
            "description": "Collect Toyota daily quotes, statements, and dividends from J-Quants.",
            "command": (
                "python -m services.ingestion_worker.cli collect-jquants "
                f"--tickers {jp_ticker} --years {years}{persist_flag}{force_flag}"
            ),
        },
        {
            "id": "collect_edinet_jp",
            "description": "Collect Toyota EDINET filing metadata and XBRL-to-CSV packages.",
            "command": (
                "python -m services.ingestion_worker.cli collect-edinet "
                f"--tickers {jp_ticker} --years {years}{persist_flag}{force_flag}"
            ),
        },
        {
            "id": "collect_stooq_prices_jp",
            "description": "Collect JP price bars from Stooq as a secondary public price source.",
            "command": (
                "python -m services.ingestion_worker.cli collect-stooq-prices "
                f"--market JP --tickers {jp_ticker} --years {years}{persist_flag}{force_flag}"
            ),
        },
        {
            "id": "source_coverage",
            "description": "Verify source-backed coverage for the P1 AAPL/005930/7203 path.",
            "command": (
                "python -m services.ingestion_worker.cli source-coverage "
                f"--tickers {coverage_tickers}{consensus_flag} --strict"
            ),
        },
    ]


def _p1_e2e_prerequisites(
    *,
    persist: bool,
    require_consensus_forecast: bool,
) -> list[dict[str, Any]]:
    prerequisites: list[dict[str, Any]] = []

    def add(name: str, ok: bool, required: bool, detail: str) -> None:
        prerequisites.append({"name": name, "ok": ok, "required": required, "detail": detail})

    add(
        "DATA_BACKEND=postgres",
        os.getenv("DATA_BACKEND", "").lower() == "postgres",
        persist,
        "required when persisting source-backed rows to Neon/Postgres",
    )
    add(
        "DATABASE_URL",
        bool(os.getenv("DATABASE_URL")),
        persist,
        "required when persisting source-backed rows and verifying source coverage",
    )
    add(
        "SEC_USER_AGENT",
        bool(os.getenv("SEC_USER_AGENT")),
        True,
        "required for SEC EDGAR archive collection and US normalization",
    )
    add(
        "OPENDART_API_KEY",
        _has_opendart_key(),
        True,
        "required for Samsung Electronics OpenDART collection; DART_API_KEY is accepted",
    )
    add(
        "JQUANTS credentials",
        bool(os.getenv("JQUANTS_REFRESH_TOKEN"))
        or (bool(os.getenv("JQUANTS_EMAIL")) and bool(os.getenv("JQUANTS_PASSWORD"))),
        True,
        "required for Toyota J-Quants statements, dividends, and quotes",
    )
    add(
        "EDINET_API_KEY",
        bool(os.getenv("EDINET_API_KEY")),
        True,
        "required for Toyota EDINET filing collection in the P1 worker",
    )
    if require_consensus_forecast:
        add(
            "traceable forecast source CSV",
            False,
            False,
            "import consensus snapshots separately; the P1 runner does not synthesize estimates",
        )
    return prerequisites


def normalize_us_ticker(
    ticker: str,
    start_year: int,
    end_year: int,
    *,
    policy: str = "street_comparable",
    persist: bool = False,
    force_refresh: bool = False,
) -> dict[str, Any]:
    normalized_ticker = ticker.upper()
    service = NormalizationService()
    source_documents = service.collect_sec(
        normalized_ticker,
        start_year,
        end_year,
        force_refresh,
    )
    result = NormalizationService(source_documents=source_documents).normalize(
        normalized_ticker,
        NormalizationPolicy(base_policy=policy),
        start_year,
        end_year,
    )
    persisted = (
        _persist_normalization(normalized_ticker, source_documents, result.series)
        if persist
        else {}
    )
    return {
        "ticker": normalized_ticker,
        "status": "ok",
        "source_documents": len(source_documents),
        "series_count": len(result.series),
        "failed_strategies": result.failed_strategies,
        "warnings": result.warnings,
        "persisted": persisted,
    }


def normalize_us_batch_run(
    tickers: str | list[str],
    start_year: int,
    end_year: int,
    *,
    policy: str = "street_comparable",
    persist: bool = False,
    force_refresh: bool = False,
    continue_on_error: bool = False,
) -> dict[str, Any]:
    requested_tickers = normalize_coverage_tickers(tickers)
    results: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for ticker in requested_tickers:
        try:
            results.append(
                normalize_us_ticker(
                    ticker,
                    start_year,
                    end_year,
                    policy=policy,
                    persist=persist,
                    force_refresh=force_refresh,
                )
            )
        except Exception as exc:
            failure = {
                "ticker": ticker,
                "status": "failed",
                "error_type": exc.__class__.__name__,
                "error": str(exc),
            }
            failed.append(failure)
            results.append(failure)
            if not continue_on_error:
                break
    return {
        "status": "ok" if not failed else "partial" if continue_on_error else "failed",
        "tickers_requested": requested_tickers,
        "tickers_completed": [row["ticker"] for row in results if row["status"] == "ok"],
        "failed": failed,
        "results": results,
    }


def kr_production_readiness(
    tickers: str | list[str] | None = None,
    *,
    years: str = "2020:2025",
    min_historical_years: int = 3,
    min_forecast_years: int = 5,
    require_consensus_forecast: bool = False,
) -> dict[str, Any]:
    expected_tickers = normalize_coverage_tickers(tickers, market="KR")
    source_coverage = source_coverage_report(
        tickers=expected_tickers,
        market="KR",
        min_historical_years=min_historical_years,
        min_forecast_years=min_forecast_years,
        require_consensus_forecast=require_consensus_forecast,
    )
    cache_coverage = kr_valuation_cache_universe_coverage(tuple(expected_tickers))

    cache_summary = cache_coverage["summary"]
    valuation_ready_count = int(cache_summary["valuation_ready"])
    expected_count = int(cache_summary["tickers_expected"])
    local_cache_ready = expected_count > 0 and valuation_ready_count == expected_count
    local_cache_complete = expected_count > 0 and int(cache_summary["complete"]) == expected_count
    source_coverage_ready = source_coverage["status"] == "ready"
    local_warehouse_ready = (
        source_coverage_ready
        and source_coverage.get("data_mode") == "local_source_backed_warehouse"
    )
    production_ready = source_coverage_ready and not local_warehouse_ready

    if production_ready:
        status = "production_ready"
    elif local_warehouse_ready:
        status = "local_warehouse_ready"
    elif local_cache_ready:
        status = "ready_for_protected_smoke"
    else:
        status = "needs_local_source_data"

    missing_required = _kr_readiness_missing_required(
        cache_coverage,
        source_coverage,
        require_consensus_forecast=require_consensus_forecast,
    )
    return {
        "status": status,
        "market": "KR",
        "years": years,
        "tickers": expected_tickers,
        "requirements": {
            "local_cache_valuation_ready": True,
            "production_postgres_source_coverage": True,
            "consensus_forecast_required": require_consensus_forecast,
            "min_historical_years": min_historical_years,
            "min_forecast_years": min_forecast_years,
        },
        "summary": {
            "tickers_expected": expected_count,
            "local_cache_valuation_ready": valuation_ready_count,
            "local_cache_complete_count": int(cache_summary["complete"]),
            "local_cache_partial_source_backed": int(cache_summary["partial_source_backed"]),
            "local_cache_missing": int(cache_summary["missing"]),
            "production_core_ready": int(source_coverage["summary"]["core_ready"]),
            "production_consensus_forecast_ready": int(
                source_coverage["summary"]["consensus_forecast_ready"]
            ),
            "local_cache_ready": local_cache_ready,
            "local_cache_complete": local_cache_complete,
            "local_warehouse_ready": local_warehouse_ready,
            "production_ready": production_ready,
            "source_coverage_mode": source_coverage.get("data_mode"),
        },
        "local_cache_coverage": cache_coverage,
        "production_source_coverage": source_coverage,
        "missing_required": missing_required,
        "next_commands": _kr_readiness_next_commands(
            expected_tickers,
            years=years,
            require_consensus_forecast=require_consensus_forecast,
            status=status,
        ),
    }


def _kr_readiness_missing_required(
    cache_coverage: dict[str, Any],
    source_coverage: dict[str, Any],
    *,
    require_consensus_forecast: bool,
) -> list[str]:
    missing: list[str] = []
    for row in cache_coverage.get("rows", []):
        if not row.get("valuation_ready"):
            missing.append(f"local_cache:{row['ticker']}")
    if source_coverage["status"] != "ready":
        missing.extend(
            f"source_coverage:{ticker}"
            for ticker in source_coverage["summary"]["missing_core"]
        )
    if require_consensus_forecast:
        missing.extend(
            f"consensus_forecast:{ticker}"
            for ticker in source_coverage["summary"]["missing_consensus_forecast"]
        )
    return missing


def _kr_readiness_next_commands(
    tickers: list[str],
    *,
    years: str,
    require_consensus_forecast: bool,
    status: str,
) -> list[dict[str, str]]:
    ticker_csv = comma_join(tickers)
    commands = [
        {
            "id": "local_raw_dry_run",
            "command": (
                "python -m services.ingestion_worker.cli run-source-e2e "
                f"--market KR --tickers {ticker_csv} --years {years} "
                "--continue-on-error --dry-run --summary-only"
            ),
        },
        {
            "id": "build_kr_valuation_inputs",
            "command": (
                "python -m services.ingestion_worker.cli build-kr-valuation-inputs "
                f"--tickers {ticker_csv} --years {years} --strict"
            ),
        },
        {
            "id": "load_kr_valuation_warehouse",
            "command": (
                "python -m services.ingestion_worker.cli load-kr-valuation-warehouse "
                f"--tickers {ticker_csv} --strict"
            ),
        },
        {
            "id": "load_kr_valuation_postgres",
            "command": (
                "python -m services.ingestion_worker.cli load-kr-valuation-postgres "
                f"--tickers {ticker_csv} --strict"
            ),
        },
        {
            "id": "source_coverage",
            "command": (
                "python -m services.ingestion_worker.cli source-coverage "
                f"--market KR --tickers {ticker_csv}"
                + (" --require-consensus-forecast" if require_consensus_forecast else "")
                + " --strict"
            ),
        },
    ]
    if status in {"ready_for_protected_smoke", "local_warehouse_ready", "production_ready"}:
        commands.extend(
            [
                {
                    "id": "protected_partial_smoke",
                    "command": (
                        "pnpm workflow:kr:smoke -- -BaseUrl "
                        "https://your-private-preview.vercel.app "
                        "-PartialAudit -PartialTickers 005930.KS -Watch"
                    ),
                },
                {
                    "id": "full_production_gate",
                    "command": (
                        "python -m services.ingestion_worker.cli deploy-gate "
                        f"--markets KR --tickers {ticker_csv} --require-blob"
                        + (
                            " --require-consensus-forecast"
                            if require_consensus_forecast
                            else ""
                        )
                        + " --strict"
                    ),
                },
            ]
        )
    return commands


def _kr_readiness_output_summary(summary: dict[str, Any]) -> dict[str, Any]:
    local_rows = summary["local_cache_coverage"].get("rows", [])
    production = summary["production_source_coverage"]
    local_warehouse_ready = bool(summary["summary"]["local_warehouse_ready"])
    production_ready = bool(summary["summary"]["production_ready"])
    production_status = (
        "production_ready"
        if production_ready
        else "local_warehouse_only"
        if local_warehouse_ready
        else production["status"]
    )
    return {
        "status": summary["status"],
        "market": summary["market"],
        "years": summary["years"],
        "tickers": summary["tickers"],
        "summary": summary["summary"],
        "partial_source_backed_tickers": [
            row["ticker"]
            for row in local_rows
            if row.get("coverage_status") == "partial_source_backed"
        ],
        "local_quality_flags": summary["local_cache_coverage"].get("quality_flags", []),
        "source_coverage_status": production["status"],
        "production_status": production_status,
        "production_postgres": production.get("postgres", {}),
        "production_missing_core": production["summary"]["missing_core"],
        "production_missing_consensus_forecast": production["summary"][
            "missing_consensus_forecast"
        ],
        "missing_required": summary["missing_required"],
        "next_commands": summary["next_commands"],
    }


def _deployment_gate_output_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Return a compact deployment gate report for operators and CI logs."""
    preflight = summary.get("preflight", {})
    coverage = summary.get("source_coverage", {})
    coverage_summary = coverage.get("summary", {})
    return {
        "status": summary.get("status"),
        "mode": summary.get("mode"),
        "preflight_status": preflight.get("status"),
        "source_coverage_status": coverage.get("status"),
        "source_coverage_mode": coverage.get("data_mode"),
        "source_coverage_backend": coverage.get("data_backend"),
        "postgres": coverage.get("postgres", {}),
        "tickers_expected": coverage_summary.get("tickers_expected"),
        "core_ready": coverage_summary.get("core_ready"),
        "consensus_forecast_ready": coverage_summary.get("consensus_forecast_ready"),
        "missing_core": coverage_summary.get("missing_core", []),
        "missing_consensus_forecast": coverage_summary.get("missing_consensus_forecast", []),
        "missing_required_count": len(summary.get("missing_required", [])),
        "missing_required": summary.get("missing_required", []),
        "preflight_missing_required": preflight.get("missing_required", []),
        "next_actions": coverage.get("remediation", {}).get("next_actions", []),
    }


def deployment_gate(
    markets: str = "US,KR,JP",
    tickers: str | list[str] | None = None,
    *,
    require_blob: bool = False,
    require_consensus_forecast: bool = False,
    strict: bool = False,
    root: Path | None = None,
) -> dict[str, Any]:
    preflight = deployment_preflight(
        markets=markets,
        require_blob=require_blob,
        strict=strict,
        root=root,
    )
    coverage = source_coverage_report(
        tickers=tickers,
        require_consensus_forecast=require_consensus_forecast,
    )
    preflight_ok = preflight["status"] == "ok"
    coverage_ok = coverage["status"] == "ready"
    status = (
        "ok"
        if preflight_ok and coverage_ok
        else "needs_configuration"
        if not preflight_ok
        else "needs_source_data"
    )
    return {
        "status": status,
        "mode": "strict" if strict else "report",
        "preflight": preflight,
        "source_coverage": coverage,
        "missing_required": [
            *preflight.get("missing_required", []),
            *(
                [f"source_coverage:{ticker}" for ticker in coverage["summary"]["missing_core"]]
                if not coverage_ok
                else []
            ),
            *(
                [
                    f"consensus_forecast:{ticker}"
                    for ticker in coverage["summary"]["missing_consensus_forecast"]
                ]
                if require_consensus_forecast and not coverage_ok
                else []
            ),
        ],
    }


def _private_runtime_checks(strict: bool) -> list[dict[str, Any]]:
    def check(name: str, ok: bool, detail: str) -> dict[str, Any]:
        return {
            "name": name,
            "ok": ok,
            "required": strict,
            "detail": detail,
        }

    return [
        check(
            "AUTH_REQUIRED=true",
            _env_truthy("AUTH_REQUIRED"),
            "private Vercel deployment should require web auth",
        ),
        check(
            "API_AUTH_REQUIRED=true",
            _env_truthy("API_AUTH_REQUIRED") and not _env_truthy("API_AUTH_DISABLED"),
            "FastAPI routes should require the signed pf_session cookie",
        ),
        check(
            "AUTH_SECRET",
            bool(os.getenv("AUTH_SECRET")),
            "required for NextAuth and signed session cookies",
        ),
        check(
            "AUTH_GITHUB_ID",
            bool(os.getenv("AUTH_GITHUB_ID")),
            "required for GitHub OAuth private access",
        ),
        check(
            "AUTH_GITHUB_SECRET",
            bool(os.getenv("AUTH_GITHUB_SECRET")),
            "required for GitHub OAuth private access",
        ),
        check(
            "AUTH_ALLOWED_EMAILS",
            bool(os.getenv("AUTH_ALLOWED_EMAILS")),
            "required to constrain the private terminal allowlist",
        ),
        check(
            "API_CORS_ORIGINS",
            bool(os.getenv("API_CORS_ORIGINS")) and "*" not in os.getenv("API_CORS_ORIGINS", ""),
            "required to restrict browser origins for cookie-authenticated API access",
        ),
        check(
            "API_RATE_LIMIT_ENABLED=true",
            _env_truthy("API_RATE_LIMIT_ENABLED"),
            "private API should have a per-client request throttle enabled",
        ),
    ]


def _env_example_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        keys.add(stripped.split("=", 1)[0].strip())
    return keys


def _env_file_value(path: Path, key: str) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if name.strip() == key:
            return value.strip().strip('"').strip("'").lower()
    return None


def _file_contains(path: Path, needle: str) -> bool:
    return path.exists() and needle in path.read_text(encoding="utf-8")


def _file_contains_in_order(path: Path, needles: list[str]) -> bool:
    if not path.exists():
        return False
    content = path.read_text(encoding="utf-8")
    position = -1
    for needle in needles:
        position = content.find(needle, position + 1)
        if position < 0:
            return False
    return True


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").lower() in {"1", "true", "yes", "on"}


def _alembic_heads(path: Path) -> list[str]:
    if not path.exists():
        return []
    revisions: set[str] = set()
    dependencies: set[str] = set()
    for migration in path.glob("*.py"):
        revision, down_revision = _migration_revisions(migration)
        if revision:
            revisions.add(revision)
        dependencies.update(down_revision)
    return sorted(revisions - dependencies)


def _migration_revisions(path: Path) -> tuple[str | None, set[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    revision: str | None = None
    down_revisions: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
        if "revision" in targets:
            value = ast.literal_eval(node.value)
            revision = str(value) if value else None
        if "down_revision" in targets:
            down_revisions.update(_literal_revision_values(node.value))
    return revision, down_revisions


def _literal_revision_values(node: ast.AST) -> set[str]:
    value = ast.literal_eval(node)
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    return {str(item) for item in value if item}


def _database_connectivity_ok() -> bool:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def import_market_csv(path: Path, persist: bool = False) -> dict[str, Any]:
    reader = csv.DictReader(path.open(encoding="utf-8-sig"))
    rows = list(reader)
    required = {"ticker", "fiscal_year", "trade_date", "close_price", "currency", "source"}
    fieldnames = set(reader.fieldnames or [])
    if not required <= fieldnames:
        missing = sorted(required - fieldnames)
        raise ValueError(f"missing required CSV columns: {', '.join(missing)}")
    validated_rows = _validated_market_rows(rows)
    summary = _market_import_summary(validated_rows, persist=False)
    if not persist:
        return summary

    repo = IngestionRepository()
    stored_prices = 0
    stored_dividends = 0
    for row in validated_rows:
        ticker = row["ticker"].upper()
        meta = _security_meta(ticker, row["currency"])
        security = repo.ensure_security(
            ticker,
            meta.name,
            meta.country,
            meta.currency,
            meta.exchange,
        )
        trace = {
            "source_type": row["source"],
            "source_url": row.get("source_url") or None,
            "period": f"FY{row['fiscal_year']}",
            "unit": "per_share",
            "currency": row["currency"],
            "formula": (
                "CSV close_price and dividend imported from source-backed market data export"
            ),
            "quality_status": "source_backed_csv",
        }
        if row.get("market_cap") is not None:
            trace.update(
                {
                    "market_cap": str(row["market_cap"]),
                    "market_cap_unit": row["currency"],
                    "market_cap_formula": "CSV market_cap imported from source-backed market data export",
                }
            )
        if row.get("listed_shares") is not None:
            trace.update(
                {
                    "listed_shares": str(row["listed_shares"]),
                    "listed_shares_unit": "shares",
                    "listed_shares_formula": "CSV listed_shares imported from source-backed market data export",
                }
            )
        repo.store_price_bar(
            security.id,
            row["fiscal_year"],
            row["trade_date"],
            row["close_price"],
            row["currency"],
            row["source"],
            trace,
        )
        stored_prices += 1
        dividend = row.get("dividend")
        if dividend is not None and dividend != 0:
            repo.store_dividend(
                security.id,
                row["fiscal_year"],
                row.get("dividend_date") or row["trade_date"],
                dividend,
                row["currency"],
                row["source"],
                {**trace, "formula": "CSV dividend imported from source-backed market data export"},
            )
            stored_dividends += 1
    return _market_import_summary(validated_rows, persist=True) | {
        "price_bars": stored_prices,
        "dividends": stored_dividends,
    }


def _validated_market_rows(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    validated: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=2):
        ticker = (row.get("ticker") or "").strip().upper()
        if not ticker:
            raise ValueError(f"row {index}: ticker is required")
        fiscal_year = _required_int(row.get("fiscal_year"), "fiscal_year", index)
        if fiscal_year < 1900 or fiscal_year > 2200:
            raise ValueError(f"row {index}: fiscal_year must be between 1900 and 2200")
        trade_date = _required_date(row.get("trade_date"), "trade_date", index)
        close_price = _required_decimal(row.get("close_price"), "close_price", index)
        if close_price <= 0:
            raise ValueError(f"row {index}: close_price must be positive")
        dividend = (
            _required_decimal(row.get("dividend"), "dividend", index)
            if row.get("dividend") not in {None, ""}
            else None
        )
        if dividend is not None and dividend < 0:
            raise ValueError(f"row {index}: dividend must be non-negative")
        market_cap = (
            _required_decimal(row.get("market_cap"), "market_cap", index)
            if row.get("market_cap") not in {None, ""}
            else None
        )
        if market_cap is not None and market_cap <= 0:
            raise ValueError(f"row {index}: market_cap must be positive")
        listed_shares = (
            _required_decimal(row.get("listed_shares"), "listed_shares", index)
            if row.get("listed_shares") not in {None, ""}
            else None
        )
        if listed_shares is not None and listed_shares <= 0:
            raise ValueError(f"row {index}: listed_shares must be positive")
        dividend_date = (
            _required_date(row.get("dividend_date"), "dividend_date", index)
            if row.get("dividend_date")
            else None
        )
        currency = (row.get("currency") or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError(f"row {index}: currency must be a 3-letter ISO code")
        source = (row.get("source") or "").strip()
        if not source:
            raise ValueError(f"row {index}: source is required")
        source_url = (row.get("source_url") or "").strip()
        if source_url and not source_url.startswith(("https://", "http://")):
            raise ValueError(f"row {index}: source_url must start with http:// or https://")
        validated.append(
            {
                **row,
                "ticker": ticker,
                "fiscal_year": fiscal_year,
                "trade_date": trade_date,
                "close_price": close_price,
                "dividend": dividend,
                "dividend_date": dividend_date,
                "market_cap": market_cap,
                "listed_shares": listed_shares,
                "currency": currency,
                "source": source,
                "source_url": source_url,
            }
        )
    return validated


def _market_import_summary(rows: list[dict[str, Any]], persist: bool) -> dict[str, Any]:
    dates = [row["trade_date"] for row in rows]
    dividend_rows = [
        row for row in rows if row.get("dividend") is not None and row["dividend"] != 0
    ]
    market_cap_rows = [row for row in rows if row.get("market_cap") is not None]
    listed_shares_rows = [row for row in rows if row.get("listed_shares") is not None]
    return {
        "rows": len(rows),
        "persisted": persist,
        "tickers": sorted({row["ticker"] for row in rows}),
        "fiscal_years": sorted({row["fiscal_year"] for row in rows}),
        "date_range": {
            "start": min(dates).isoformat() if dates else None,
            "end": max(dates).isoformat() if dates else None,
        },
        "price_rows": len(rows),
        "dividend_rows": len(dividend_rows),
        "market_cap_rows": len(market_cap_rows),
        "listed_shares_rows": len(listed_shares_rows),
        "source_types": sorted({row["source"] for row in rows}),
        "currencies": sorted({row["currency"] for row in rows}),
    }


def _read_consensus_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            fieldnames = set(reader.fieldnames or [])
    except FileNotFoundError as exc:
        raise ValueError(f"CSV file not found: {path}") from exc
    if not REQUIRED_CONSENSUS_COLUMNS <= fieldnames:
        missing = sorted(REQUIRED_CONSENSUS_COLUMNS - fieldnames)
        raise ValueError(f"missing required CSV columns: {', '.join(missing)}")
    return rows


def import_consensus_csv(path: Path, persist: bool = False) -> dict[str, Any]:
    try:
        payload = path.read_bytes()
    except FileNotFoundError as exc:
        raise ValueError(f"CSV file not found: {path}") from exc
    digest = hashlib.sha256(payload).hexdigest()
    rows = _read_consensus_csv(path)
    validated_rows = _validated_consensus_rows(rows)
    summary = _consensus_import_summary(
        validated_rows,
        persist=False,
        content_hash=digest,
        source_file=str(path),
    )
    if not persist:
        return summary

    repo = IngestionRepository()
    market = _consensus_import_market(validated_rows)
    document = ConnectorDocument(
        source="user_consensus_csv",
        market=market,
        ticker="BATCH",
        identifier=f"{path.stem}-{digest[:12]}",
        url=None,
        payload=payload,
        content_type=_content_type_for_path(path),
        metadata={
            "file_name": path.name,
            "content_hash": digest,
            "source_type": "user_consensus_csv",
            "policy": "source_backed_consensus_import",
            "row_count": len(validated_rows),
            "tickers": sorted({row["ticker"] for row in validated_rows}),
            "fiscal_years": sorted({row["fiscal_year"] for row in validated_rows}),
            "estimate_cases": sorted({row["estimate_case"] for row in validated_rows}),
        },
    )
    run_id = repo.start_run(
        market=market,
        source="user_consensus_csv",
        ticker="BATCH",
        metadata=document.metadata,
    )
    queue = BlobUploadQueue()
    stored = 0
    try:
        local_path, _ = _write_raw_document(document)
        source_document = SourceDocument(
            id=digest,
            ticker=None,
            accession_number=document.identifier,
            form_type="USER_CONSENSUS_CSV",
            filing_url=None,
            source_url=None,
            content=payload.decode("utf-8-sig", errors="ignore"),
            local_path=str(local_path),
            content_hash=digest,
            metadata=document.metadata,
        )
        source_document_id = repo.store_source_document(
            None,
            source_document,
            "user_consensus_csv",
        )
        blob_key = _blob_key(document, digest)
        queue.enqueue(
            BlobQueueItem(
                local_path=str(local_path),
                blob_key=blob_key,
                content_type=document.content_type,
                metadata=document.metadata,
            )
        )
        repo.store_raw_object(
            ingestion_run_id=run_id,
            source_document_id=source_document_id,
            market=market,
            source="user_consensus_csv",
            ticker="BATCH",
            identifier=document.identifier,
            source_url=None,
            local_path=str(local_path),
            content_hash=digest,
            content_type=document.content_type,
            metadata=document.metadata,
            blob_key=blob_key,
        )

        for row in validated_rows:
            ticker = row["ticker"].upper()
            meta = _security_meta(ticker, row["currency"])
            security = repo.ensure_security(
                ticker,
                meta.name,
                meta.country,
                meta.currency,
                meta.exchange,
            )
            metric_key = row.get("metric_key") or "adjusted_operating_eps"
            fiscal_year = row["fiscal_year"]
            snapshot = row["snapshot_date"]
            source = row["source"]
            quality_status = row.get("quality_status") or "user_provided_consensus_snapshot"
            row_filing_id = row.get("filing_id") or (
                f"{document.identifier}:{ticker}:{fiscal_year}:"
                f"{snapshot.isoformat()}:{row['estimate_case']}"
            )
            assumption_type = _consensus_assumption_type(row)
            trace = {
                "source_type": source,
                "source_url": row.get("source_url") or None,
                "source_document_id": str(source_document_id),
                "upstream_source_document_id": row.get("source_document_id") or None,
                "filing_id": row_filing_id,
                "period": f"FY{fiscal_year}E",
                "available_at": datetime.combine(snapshot, datetime.min.time(), tzinfo=UTC),
                "unit": "per_share",
                "currency": row["currency"],
                "method": (
                    "explicit_manual_assumption"
                    if assumption_type == "manual_assumption"
                    else "point_in_time_consensus_snapshot"
                ),
                "assumption_type": assumption_type,
                "formula": _consensus_trace_formula(assumption_type),
                "llm_generated_numbers": False,
                "ai_role": "commentary_only",
                "quality_status": quality_status,
                "source_file": path.name,
                "source_file_content_hash": digest,
                "source_file_row_number": row.get("row_number"),
                "notes": row.get("notes") or None,
            }
            repo.store_consensus_snapshot(
                security.id,
                metric_key,
                fiscal_year,
                snapshot,
                row["estimate_case"],
                row["estimate_eps"],
                "per_share",
                row["currency"],
                source,
                quality_status,
                trace,
                growth_rate_pct=row.get("growth_rate_pct"),
                analyst_count=row.get("analyst_count"),
                source_url=row.get("source_url") or None,
                period_end=row.get("period_end"),
                source_document_id=source_document_id,
            )
            stored += 1
        repo.finish_run(run_id)
    except Exception as exc:
        repo.finish_run(run_id, status="failed", error_summary=str(exc))
        raise
    return _consensus_import_summary(
        validated_rows,
        persist=True,
        content_hash=digest,
        source_file=str(path),
    ) | {
        "consensus_snapshots": stored,
        "source_document_id": str(source_document_id),
        "raw_object_content_hash": digest,
    }


def validate_consensus_csv(
    path: Path,
    *,
    tickers: str,
    start_year: int,
    years: int,
    cases: str = DEFAULT_CONSENSUS_VALIDATION_CASES,
    case_mode: str = "any",
) -> dict[str, Any]:
    if years < 1 or years > 10:
        raise ValueError("years must be between 1 and 10")
    if start_year < 1900 or start_year > 2200:
        raise ValueError("start_year must be between 1900 and 2200")
    if case_mode not in {"any", "all"}:
        raise ValueError("case_mode must be any or all")

    normalized_tickers = normalize_coverage_tickers(tickers)
    if not normalized_tickers:
        raise ValueError("at least one ticker is required")
    required_years = list(range(start_year, start_year + years))
    required_cases = _normalize_consensus_template_cases(cases)

    try:
        rows = _read_consensus_csv(path)
    except ValueError as exc:
        return {
            "status": "invalid_input",
            "path": str(path),
            "import_ready": False,
            "error": str(exc),
            "rows": 0,
            "valid_rows": 0,
            "invalid_row_count": 0,
            "missing_required_count": len(normalized_tickers) * len(required_years),
            "expected": _consensus_validation_expected(
                normalized_tickers,
                required_years,
                required_cases,
                case_mode,
            ),
            "next_commands": _consensus_validation_next_commands(
                path,
                normalized_tickers,
                start_year,
                years,
                required_cases,
                case_mode,
            ),
        }

    validated_rows: list[dict[str, Any]] = []
    invalid_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=2):
        try:
            validated_rows.append(_validated_consensus_row(row, index))
        except ValueError as exc:
            invalid_rows.append({"row_number": index, "error": str(exc)})

    expected_ticker_set = set(normalized_tickers)
    expected_year_set = set(required_years)
    expected_case_set = set(required_cases)
    present_cases_by_period: dict[tuple[str, int], set[str]] = {}
    key_counts: Counter[tuple[str, int, str]] = Counter()
    unexpected_rows: list[dict[str, Any]] = []
    for row in validated_rows:
        ticker = row["ticker"]
        fiscal_year = row["fiscal_year"]
        estimate_case = row["estimate_case"]
        present_cases_by_period.setdefault((ticker, fiscal_year), set()).add(estimate_case)
        key_counts[(ticker, fiscal_year, estimate_case)] += 1
        if (
            ticker not in expected_ticker_set
            or fiscal_year not in expected_year_set
            or estimate_case not in expected_case_set
        ):
            unexpected_rows.append(
                {
                    "ticker": ticker,
                    "fiscal_year": fiscal_year,
                    "estimate_case": estimate_case,
                }
            )

    missing_required = _missing_consensus_required_rows(
        normalized_tickers,
        required_years,
        required_cases,
        case_mode,
        present_cases_by_period,
    )
    duplicate_rows = [
        {
            "ticker": ticker,
            "fiscal_year": fiscal_year,
            "estimate_case": estimate_case,
            "rows": count,
        }
        for (ticker, fiscal_year, estimate_case), count in sorted(key_counts.items())
        if count > 1
    ]

    if invalid_rows:
        status = "invalid_input"
    elif missing_required:
        status = "missing_required_rows"
    elif duplicate_rows:
        status = "duplicate_rows"
    else:
        status = "ready"

    return {
        "status": status,
        "path": str(path),
        "import_ready": status == "ready",
        "rows": len(rows),
        "valid_rows": len(validated_rows),
        "invalid_row_count": len(invalid_rows),
        "invalid_rows_sample": invalid_rows[:10],
        "missing_required_count": len(missing_required),
        "missing_required_sample": missing_required[:20],
        "duplicate_count": len(duplicate_rows),
        "duplicate_rows_sample": duplicate_rows[:10],
        "unexpected_row_count": len(unexpected_rows),
        "unexpected_rows_sample": unexpected_rows[:10],
        "coverage": _consensus_validation_coverage(
            validated_rows,
            normalized_tickers,
            required_years,
            required_cases,
            case_mode,
        ),
        "expected": _consensus_validation_expected(
            normalized_tickers,
            required_years,
            required_cases,
            case_mode,
        ),
        "import_summary": _consensus_import_summary(validated_rows, persist=False)
        if validated_rows
        else None,
        "next_commands": _consensus_validation_next_commands(
            path,
            normalized_tickers,
            start_year,
            years,
            required_cases,
            case_mode,
        ),
    }


def export_consensus_template(
    *,
    tickers: str,
    start_year: int,
    years: int,
    cases: str,
    snapshot_date: str,
    out: Path,
) -> dict[str, Any]:
    if years < 1 or years > 10:
        raise ValueError("years must be between 1 and 10")
    if start_year < 1900 or start_year > 2200:
        raise ValueError("start_year must be between 1900 and 2200")
    snapshot = date.fromisoformat(snapshot_date)
    normalized_tickers = normalize_coverage_tickers(tickers)
    if not normalized_tickers:
        raise ValueError("at least one ticker is required")
    normalized_cases = _normalize_consensus_template_cases(cases)
    out.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, str]] = []
    for ticker in normalized_tickers:
        meta = _security_meta(ticker, _currency_for_ticker(ticker))
        for offset in range(years):
            fiscal_year = start_year + offset
            for estimate_case in normalized_cases:
                rows.append(
                    {
                        "ticker": ticker,
                        "fiscal_year": str(fiscal_year),
                        "snapshot_date": snapshot.isoformat(),
                        "estimate_case": estimate_case,
                        "estimate_eps": "",
                        "growth_rate_pct": "",
                        "analyst_count": "",
                        "currency": meta.currency,
                        "source": "",
                        "source_url": "",
                        "metric_key": "adjusted_operating_eps",
                        "period_end": "",
                        "quality_status": "template_pending_source_value",
                        "source_document_id": "",
                        "filing_id": "",
                        "notes": (
                            "Fill estimate_eps and source from a traceable consensus, "
                            "company guidance, or explicit manual assumption before import. "
                            "For manual assumptions set source or quality_status to "
                            "manual_forecast_assumption and describe the basis in notes."
                        ),
                    }
                )

    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CONSENSUS_TEMPLATE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    return {
        "status": "template_created",
        "path": str(out),
        "rows": len(rows),
        "tickers": normalized_tickers,
        "fiscal_years": list(range(start_year, start_year + years)),
        "estimate_cases": normalized_cases,
        "snapshot_date": snapshot.isoformat(),
        "import_ready": False,
        "required_before_import": [
            "estimate_eps",
            "source",
            "source_url_or_source_document_id_or_filing_id",
        ],
        "policy": "template only; no generated financial estimates",
    }


def export_deterministic_forecast_csv(
    *,
    tickers: str,
    start_year: int,
    years: int,
    cases: str,
    snapshot_date: str,
    metric_key: str,
    cache_dir: Path,
    out: Path,
) -> dict[str, Any]:
    if years < 1 or years > 10:
        raise ValueError("years must be between 1 and 10")
    if start_year < 1900 or start_year > 2200:
        raise ValueError("start_year must be between 1900 and 2200")
    snapshot = date.fromisoformat(snapshot_date)
    normalized_tickers = normalize_coverage_tickers(tickers)
    if not normalized_tickers:
        raise ValueError("at least one ticker is required")
    normalized_cases = _normalize_consensus_template_cases(cases)
    if metric_key != "adjusted_operating_eps":
        raise ValueError("only adjusted_operating_eps deterministic forecasts are supported")

    out.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []
    ticker_summaries: list[dict[str, Any]] = []
    fiscal_years = list(range(start_year, start_year + years))
    for ticker in normalized_tickers:
        payload, cache_path = _latest_kr_valuation_cache_payload(ticker, cache_dir)
        if payload is None or cache_path is None:
            raise ValueError(f"no KR valuation input cache found for {ticker} in {cache_dir}")
        forecast = _deterministic_forecast_from_kr_cache(
            ticker=ticker,
            payload=payload,
            metric_key=metric_key,
            start_year=start_year,
            years=years,
        )
        source_document_id = (
            f"manual-forecast-assumption:{ticker}:{start_year}:"
            f"{start_year + years - 1}:{forecast['basis_hash'][:12]}"
        )
        filing_id = (
            f"MANUAL_FORECAST_ASSUMPTION_{ticker}_{start_year}_"
            f"{start_year + years - 1}_{forecast['basis_hash'][:12]}"
        )
        notes = (
            "Deterministic manual forecast assumption from source-backed KR valuation "
            f"cache {cache_path.name}; base FY{forecast['base_year']} "
            f"{metric_key}={forecast['base_metric_value']} {forecast['currency']}/share; "
            f"historical CAGR={forecast['growth_rate_pct']}%; "
            "no LLM-generated numbers."
        )
        for offset, fiscal_year in enumerate(fiscal_years, start=1):
            estimate_eps = forecast["forecast_values"][fiscal_year]
            for estimate_case in normalized_cases:
                rows.append(
                    {
                        "ticker": ticker,
                        "fiscal_year": str(fiscal_year),
                        "snapshot_date": snapshot.isoformat(),
                        "estimate_case": estimate_case,
                        "estimate_eps": _decimal_storage_string(estimate_eps),
                        "growth_rate_pct": forecast["growth_rate_pct"],
                        "analyst_count": "0",
                        "currency": forecast["currency"],
                        "source": "manual_forecast_assumption",
                        "source_url": "",
                        "metric_key": metric_key,
                        "period_end": date(fiscal_year, 12, 31).isoformat(),
                        "quality_status": "manual_forecast_assumption",
                        "source_document_id": source_document_id,
                        "filing_id": f"{filing_id}_FY{fiscal_year}_{estimate_case.upper()}",
                        "notes": notes,
                    }
                )
        ticker_summaries.append(
            {
                "ticker": ticker,
                "cache_path": str(cache_path),
                "base_year": forecast["base_year"],
                "first_year": forecast["first_year"],
                "basis_years": forecast["basis_years"],
                "growth_rate_pct": forecast["growth_rate_pct"],
                "currency": forecast["currency"],
                "source_document_id": source_document_id,
                "input_source_document_ids": forecast["input_source_document_ids"],
            }
        )

    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CONSENSUS_TEMPLATE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    validation = validate_consensus_csv(
        out,
        tickers=",".join(normalized_tickers),
        start_year=start_year,
        years=years,
        cases=",".join(normalized_cases),
        case_mode="any",
    )
    return {
        "status": "deterministic_forecast_csv_created",
        "path": str(out),
        "rows": len(rows),
        "tickers": normalized_tickers,
        "fiscal_years": fiscal_years,
        "estimate_cases": normalized_cases,
        "snapshot_date": snapshot.isoformat(),
        "metric_key": metric_key,
        "source": "manual_forecast_assumption",
        "quality_status": "manual_forecast_assumption",
        "policy": (
            "deterministic historical CAGR manual assumption; "
            "external consensus is not claimed; no LLM-generated numbers"
        ),
        "validation_status": validation["status"],
        "import_ready_candidate": validation["import_ready"],
        "ticker_summaries": ticker_summaries,
    }


def _latest_kr_valuation_cache_payload(
    ticker: str,
    cache_dir: Path,
) -> tuple[dict[str, Any] | None, Path | None]:
    normalized = ticker.upper().replace(".", "_")
    paths = sorted(
        Path(cache_dir).glob(f"{normalized}-*-valuation-inputs.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(payload.get("ticker") or "").strip().upper() == ticker.upper():
            return payload, path
    return None, None


def _deterministic_forecast_from_kr_cache(
    *,
    ticker: str,
    payload: dict[str, Any],
    metric_key: str,
    start_year: int,
    years: int,
) -> dict[str, Any]:
    points = [
        _validated_forecast_basis_point(point, metric_key)
        for point in payload.get("valuation_points") or []
        if str(point.get("metric") or "") == metric_key
    ]
    points = [point for point in points if point is not None]
    points.sort(key=lambda point: point["fiscal_year"])
    positive_points = [point for point in points if point["metric_value"] > 0]
    if len(positive_points) < 2:
        raise ValueError(
            f"{ticker} requires at least two positive source-backed valuation points"
        )

    first = positive_points[0]
    latest = positive_points[-1]
    year_span = int(latest["fiscal_year"]) - int(first["fiscal_year"])
    if year_span < 1:
        raise ValueError(f"{ticker} positive valuation points must span at least one year")
    cagr = _decimal_cagr(first["metric_value"], latest["metric_value"], year_span)
    growth_rate_pct = (cagr * Decimal("100")).quantize(Decimal("0.01"))
    annual_multiplier = Decimal("1") + cagr
    forecast_values: dict[int, Decimal] = {}
    for offset in range(1, years + 1):
        fiscal_year = start_year + offset - 1
        estimate = latest["metric_value"] * (annual_multiplier ** offset)
        forecast_values[fiscal_year] = estimate.quantize(Decimal("0.01"))

    input_source_document_ids = [
        str(point["source_trace"].get("source_document_id"))
        for point in positive_points
        if point["source_trace"].get("source_document_id")
    ]
    basis_hash = _stable_hash(
        {
            "ticker": ticker,
            "metric_key": metric_key,
            "first": first,
            "latest": latest,
            "start_year": start_year,
            "years": years,
        }
    )
    return {
        "first_year": first["fiscal_year"],
        "base_year": latest["fiscal_year"],
        "basis_years": [point["fiscal_year"] for point in positive_points],
        "base_metric_value": _decimal_storage_string(latest["metric_value"]),
        "growth_rate_pct": _decimal_storage_string(growth_rate_pct),
        "currency": latest["currency"],
        "forecast_values": forecast_values,
        "basis_hash": basis_hash,
        "input_source_document_ids": input_source_document_ids,
    }


def _validated_forecast_basis_point(
    point: dict[str, Any],
    metric_key: str,
) -> dict[str, Any] | None:
    try:
        fiscal_year = int(point["fiscal_year"])
        metric_value = Decimal(str(point["metric_value"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError("KR valuation cache contains an invalid valuation point") from exc
    trace = point.get("source_trace")
    if not isinstance(trace, dict):
        raise ValueError("KR valuation cache point is missing source_trace")
    storage_trace = SourceTrace(**trace)
    storage_trace.assert_storage_ready()
    if storage_trace.quality_status != "source_backed":
        raise ValueError("KR valuation cache point is not source-backed")
    if metric_value <= 0:
        return None
    return {
        "fiscal_year": fiscal_year,
        "metric": metric_key,
        "metric_value": metric_value,
        "currency": str(point.get("currency") or storage_trace.currency or "KRW").upper(),
        "source_trace": storage_trace.model_dump(mode="json"),
    }


def _decimal_cagr(first_value: Decimal, latest_value: Decimal, year_span: int) -> Decimal:
    if first_value <= 0 or latest_value <= 0:
        raise ValueError("CAGR inputs must be positive")
    if year_span < 1:
        raise ValueError("CAGR year span must be positive")
    cagr_float = (float(latest_value / first_value) ** (1 / year_span)) - 1
    return Decimal(str(cagr_float))


def build_consensus_workpaper(
    *,
    tickers: str,
    csv_path: Path,
    start_year: int,
    years: int,
    template_cases: str,
    validation_cases: str,
    case_mode: str,
    out: Path,
) -> dict[str, Any]:
    normalized_tickers = normalize_coverage_tickers(tickers)
    if not normalized_tickers:
        raise ValueError("at least one ticker is required")
    if years < 1 or years > 10:
        raise ValueError("years must be between 1 and 10")
    if case_mode not in {"any", "all"}:
        raise ValueError("case_mode must be any or all")

    template_case_list = _normalize_consensus_template_cases(template_cases)
    validation_case_list = _normalize_consensus_template_cases(validation_cases)
    fiscal_years = list(range(start_year, start_year + years))
    validation = validate_consensus_csv(
        csv_path,
        tickers=",".join(normalized_tickers),
        start_year=start_year,
        years=years,
        cases=",".join(validation_case_list),
        case_mode=case_mode,
    )
    markdown = _render_consensus_workpaper_markdown(
        tickers=normalized_tickers,
        csv_path=csv_path,
        fiscal_years=fiscal_years,
        template_cases=template_case_list,
        validation_cases=validation_case_list,
        case_mode=case_mode,
        validation=validation,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown, encoding="utf-8")
    return {
        "status": "workpaper_created",
        "path": str(out),
        "csv_path": str(csv_path),
        "tickers": normalized_tickers,
        "fiscal_years": fiscal_years,
        "template_cases": template_case_list,
        "validation_cases": validation_case_list,
        "case_mode": case_mode,
        "csv_validation_status": validation["status"],
        "csv_import_ready": validation["import_ready"],
        "policy": "operator workpaper only; no generated financial estimates",
    }


def _render_consensus_workpaper_markdown(
    *,
    tickers: list[str],
    csv_path: Path,
    fiscal_years: list[int],
    template_cases: list[str],
    validation_cases: list[str],
    case_mode: str,
    validation: dict[str, Any],
) -> str:
    joined = ",".join(tickers)
    template_cases_text = ",".join(template_cases)
    validation_cases_text = ",".join(validation_cases)
    csv_path_text = csv_path.as_posix()
    lines = [
        "# Consensus Forecast Evidence Workpaper",
        "",
        "Purpose: prepare source-backed 1Y-5Y forecast evidence for the LUXON valuation gate.",
        "",
        "## Scope",
        "",
        f"- Tickers: `{joined}`",
        f"- Fiscal years: `{fiscal_years[0]}` to `{fiscal_years[-1]}`",
        f"- CSV path: `{csv_path_text}`",
        f"- Template cases: `{template_cases_text}`",
        f"- Validation cases: `{validation_cases_text}` with `{case_mode}` coverage",
        "- Policy: no AI-generated or fixture forecast numbers are allowed.",
        "",
        "## Required CSV Fields",
        "",
        "- `ticker`",
        "- `fiscal_year`",
        "- `snapshot_date`",
        "- `estimate_case`",
        "- `estimate_eps`",
        "- `currency`",
        "- `source`",
        "- one trace anchor: `source_url`, `source_document_id`, or `filing_id`",
        "",
        "Optional fields: `growth_rate_pct`, `analyst_count`, `metric_key`, `period_end`, `quality_status`, `notes`.",
        "",
        "Manual assumption rows must use `source` or `quality_status` = `manual_forecast_assumption` and must include `notes`.",
        "",
        "## Allowed Evidence",
        "",
        "- Traceable consensus export or research database export that the operator is allowed to use.",
        "- Company guidance, filing, presentation, or transcript with explicit forecast metric evidence.",
        "- Explicit manual assumption only when tagged as manual, dated, and anchored to a source URL, source document id, or filing id.",
        "",
        "## Blocked Evidence",
        "",
        "- LLM-generated numbers.",
        "- Fixture, demo, placeholder, or template rows.",
        "- FAST Graphs screenshots or application pages as numeric forecast sources.",
        "- Rows without `source_url`, `source_document_id`, or `filing_id`.",
        "- Values copied from screenshots without a traceable underlying source.",
        "",
        "## Required Rows",
        "",
        "| Ticker | Fiscal year | Accepted case(s) | estimate_eps | source | trace anchor | Status |",
        "|---|---:|---|---|---|---|---|",
    ]
    for ticker in tickers:
        for fiscal_year in fiscal_years:
            lines.append(
                f"| `{ticker}` | {fiscal_year} | `{validation_cases_text}` | TODO | TODO | TODO | pending |"
            )
    lines.extend(
        [
            "",
            "## Commands",
            "",
            "```powershell",
            (
                "python -m services.ingestion_worker.cli export-consensus-template "
                f"--tickers {joined} --start-year {fiscal_years[0]} --years {len(fiscal_years)} "
                f"--cases {template_cases_text} --out {csv_path_text}"
            ),
            (
                "python -m services.ingestion_worker.cli validate-consensus-csv "
                f"--path {csv_path_text} --tickers {joined} --start-year {fiscal_years[0]} "
                f"--years {len(fiscal_years)} --cases {validation_cases_text} "
                f"--case-mode {case_mode} --strict"
            ),
            (
                "python -m services.ingestion_worker.cli import-consensus-csv "
                f"--path {csv_path_text} --persist"
            ),
            "```",
            "",
            "## Current CSV Validation",
            "",
            f"- Status: `{validation['status']}`",
            f"- Import ready: `{validation['import_ready']}`",
            f"- Valid rows: `{validation.get('valid_rows', 0)}`",
            f"- Missing required rows: `{validation.get('missing_required_count', 0)}`",
            f"- Invalid rows: `{validation.get('invalid_row_count', 0)}`",
            "",
        ]
    )
    if validation.get("error"):
        lines.extend(["### CSV Read Error", "", f"- `{validation['error']}`", ""])
    invalid_rows = validation.get("invalid_rows_sample") or []
    if invalid_rows:
        lines.extend(
            [
                "### Invalid Rows Sample",
                "",
                "| CSV row | Error |",
                "|---:|---|",
            ]
        )
        for row in invalid_rows:
            lines.append(
                f"| {row.get('row_number')} | {_markdown_table_cell(row.get('error'))} |"
            )
        lines.append("")
    missing_required = validation.get("missing_required_sample") or []
    if missing_required:
        lines.extend(
            [
                "### Missing Required Rows Sample",
                "",
                "| Ticker | Fiscal year | Accepted case(s) |",
                "|---|---:|---|",
            ]
        )
        for row in missing_required:
            accepted_cases = row.get("estimate_cases_allowed") or [row.get("estimate_case")]
            accepted_cases_text = ",".join(str(case) for case in accepted_cases if case)
            lines.append(
                "| "
                f"`{_markdown_table_cell(row.get('ticker'))}` | "
                f"{row.get('fiscal_year')} | "
                f"`{_markdown_table_cell(accepted_cases_text)}` |"
            )
        lines.append("")
    duplicate_rows = validation.get("duplicate_rows_sample") or []
    if duplicate_rows:
        lines.extend(
            [
                "### Duplicate Rows Sample",
                "",
                "| Ticker | Fiscal year | Case | Rows |",
                "|---|---:|---|---:|",
            ]
        )
        for row in duplicate_rows:
            lines.append(
                "| "
                f"`{_markdown_table_cell(row.get('ticker'))}` | "
                f"{row.get('fiscal_year')} | "
                f"`{_markdown_table_cell(row.get('estimate_case'))}` | "
                f"{row.get('rows')} |"
            )
        lines.append("")
    unexpected_rows = validation.get("unexpected_rows_sample") or []
    if unexpected_rows:
        lines.extend(
            [
                "### Unexpected Rows Sample",
                "",
                "| Ticker | Fiscal year | Case |",
                "|---|---:|---|",
            ]
        )
        for row in unexpected_rows:
            lines.append(
                "| "
                f"`{_markdown_table_cell(row.get('ticker'))}` | "
                f"{row.get('fiscal_year')} | "
                f"`{_markdown_table_cell(row.get('estimate_case'))}` |"
            )
        lines.append("")
    lines.extend(
        [
            "## Operator Checklist",
            "",
            "- Create or refresh the CSV template.",
            "- Fill only source-backed `estimate_eps` values.",
            "- Preserve the original source label in `source`.",
            "- Add at least one trace anchor for every row.",
            "- Run validation before import.",
            "- Import only when `import_ready=true`.",
            "",
        ]
    )
    return "\n".join(lines)


def _markdown_table_cell(value: object) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def _validated_consensus_rows(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    validated: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=2):
        validated.append(_validated_consensus_row(row, index))
    return validated


def _validated_consensus_row(row: dict[str, str], index: int) -> dict[str, Any]:
    ticker = (row.get("ticker") or "").strip().upper()
    if not ticker:
        raise ValueError(f"row {index}: ticker is required")
    fiscal_year = _required_int(row.get("fiscal_year"), "fiscal_year", index)
    if fiscal_year < 1900 or fiscal_year > 2200:
        raise ValueError(f"row {index}: fiscal_year must be between 1900 and 2200")
    snapshot_date = _required_date(row.get("snapshot_date"), "snapshot_date", index)
    period_end = (
        _required_date(row.get("period_end"), "period_end", index)
        if row.get("period_end")
        else None
    )
    estimate_case = _normalize_consensus_import_case(row.get("estimate_case") or "", index)
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
    raw_quality_status = (row.get("quality_status") or "").strip()
    quality_status = raw_quality_status or (
        "manual_forecast_assumption"
        if _is_manual_forecast_source(source)
        else "user_provided_consensus_snapshot"
    )
    if quality_status.lower() in BLOCKED_CONSENSUS_QUALITY_STATUSES:
        raise ValueError(
            f"row {index}: quality_status '{quality_status}' is not import-ready"
        )
    blocked_reason = _blocked_consensus_evidence_reason(
        {
            "source": source,
            "source_url": source_url,
            "source_document_id": source_document_id,
            "filing_id": filing_id,
            "quality_status": quality_status,
        }
    )
    if blocked_reason:
        raise ValueError(f"row {index}: blocked consensus evidence source: {blocked_reason}")
    notes = (row.get("notes") or "").strip()
    assumption_type = _consensus_assumption_type(
        {"source": source, "quality_status": quality_status}
    )
    if assumption_type == "manual_assumption" and not notes:
        raise ValueError(
            f"row {index}: manual forecast assumptions require notes with the assumption basis"
        )
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
        "assumption_type": assumption_type,
    }


def _is_manual_forecast_source(source: str) -> bool:
    normalized = source.strip().lower()
    return normalized in MANUAL_FORECAST_SOURCE_ALIASES or "manual" in normalized


def _blocked_consensus_evidence_reason(row: dict[str, Any]) -> str | None:
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
    for token in sorted(BLOCKED_CONSENSUS_SOURCE_TOKENS):
        if token in evidence_text:
            return token
    return None


def _consensus_assumption_type(row: dict[str, Any]) -> str:
    source = str(row.get("source") or "")
    quality_status = str(row.get("quality_status") or "").strip().lower()
    if _is_manual_forecast_source(source) or quality_status in MANUAL_FORECAST_QUALITY_STATUSES:
        return "manual_assumption"
    return "external_consensus"


def _consensus_trace_formula(assumption_type: str) -> str:
    if assumption_type == "manual_assumption":
        return (
            "explicit user forecast assumption imported from a source-traced CSV; "
            "no LLM-generated numbers"
        )
    return (
        "source-backed point-in-time consensus estimate snapshot imported from "
        "a source-traced CSV; no LLM-generated numbers"
    )


def _missing_consensus_required_rows(
    tickers: list[str],
    fiscal_years: list[int],
    estimate_cases: list[str],
    case_mode: str,
    present_cases_by_period: dict[tuple[str, int], set[str]],
) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    for ticker in tickers:
        for fiscal_year in fiscal_years:
            present_cases = present_cases_by_period.get((ticker, fiscal_year), set())
            if case_mode == "any":
                if not present_cases.intersection(estimate_cases):
                    missing.append(
                        {
                            "ticker": ticker,
                            "fiscal_year": fiscal_year,
                            "estimate_cases_allowed": estimate_cases,
                        }
                    )
                continue
            for estimate_case in estimate_cases:
                if estimate_case not in present_cases:
                    missing.append(
                        {
                            "ticker": ticker,
                            "fiscal_year": fiscal_year,
                            "estimate_case": estimate_case,
                        }
                    )
    return missing


def _consensus_validation_expected(
    tickers: list[str],
    fiscal_years: list[int],
    estimate_cases: list[str],
    case_mode: str,
) -> dict[str, Any]:
    periods = len(tickers) * len(fiscal_years)
    required_rows = periods if case_mode == "any" else periods * len(estimate_cases)
    return {
        "tickers": tickers,
        "fiscal_years": fiscal_years,
        "estimate_cases": estimate_cases,
        "case_mode": case_mode,
        "required_periods": periods,
        "required_rows": required_rows,
    }


def _consensus_validation_coverage(
    rows: list[dict[str, Any]],
    tickers: list[str],
    fiscal_years: list[int],
    estimate_cases: list[str],
    case_mode: str,
) -> dict[str, Any]:
    present_cases_by_period: dict[tuple[str, int], set[str]] = {}
    for row in rows:
        present_cases_by_period.setdefault((row["ticker"], row["fiscal_year"]), set()).add(
            row["estimate_case"]
        )
    covered_periods = 0
    for ticker in tickers:
        for fiscal_year in fiscal_years:
            present = present_cases_by_period.get((ticker, fiscal_year), set())
            if case_mode == "any":
                covered_periods += int(bool(present.intersection(estimate_cases)))
            else:
                covered_periods += int(all(case in present for case in estimate_cases))
    return {
        "covered_periods": covered_periods,
        "required_periods": len(tickers) * len(fiscal_years),
        "tickers_with_rows": sorted({row["ticker"] for row in rows}),
        "fiscal_years_with_rows": sorted({row["fiscal_year"] for row in rows}),
        "estimate_cases_with_rows": sorted({row["estimate_case"] for row in rows}),
    }


def _consensus_validation_next_commands(
    path: Path,
    tickers: list[str],
    start_year: int,
    years: int,
    estimate_cases: list[str],
    case_mode: str,
) -> list[str]:
    joined = ",".join(tickers)
    cases = ",".join(estimate_cases)
    return [
        (
            "python -m services.ingestion_worker.cli export-consensus-template "
            f"--tickers {joined} --start-year {start_year} --years {years} "
            f"--cases {cases} --out {path.as_posix()}"
        ),
        (
            "python -m services.ingestion_worker.cli validate-consensus-csv "
            f"--path {path.as_posix()} --tickers {joined} --start-year {start_year} "
            f"--years {years} --cases {cases} --case-mode {case_mode} --strict"
        ),
        (
            "python -m services.ingestion_worker.cli import-consensus-csv "
            f"--path {path.as_posix()} --persist"
        ),
    ]


def _consensus_import_summary(
    rows: list[dict[str, Any]],
    persist: bool,
    *,
    content_hash: str | None = None,
    source_file: str | None = None,
) -> dict[str, Any]:
    case_counts = Counter(row["estimate_case"] for row in rows)
    assumption_counts = Counter(
        row.get("assumption_type") or _consensus_assumption_type(row)
        for row in rows
    )
    summary = {
        "rows": len(rows),
        "persisted": persist,
        "tickers": sorted({row["ticker"] for row in rows}),
        "fiscal_years": sorted({row["fiscal_year"] for row in rows}),
        "snapshot_dates": sorted({row["snapshot_date"].isoformat() for row in rows}),
        "estimate_cases": dict(sorted(case_counts.items())),
        "source_types": sorted({row["source"] for row in rows}),
        "quality_statuses": sorted(
            {
                (row.get("quality_status") or "user_provided_consensus_snapshot")
                for row in rows
            }
        ),
        "assumption_types": dict(sorted(assumption_counts.items())),
        "manual_assumption_rows": assumption_counts.get("manual_assumption", 0),
        "external_consensus_rows": assumption_counts.get("external_consensus", 0),
    }
    if content_hash:
        summary["source_file_content_hash"] = content_hash
        summary["source_evidence_status"] = (
            "file_hashed_and_raw_object_ready" if persist else "file_hashed"
        )
    if source_file:
        summary["source_file"] = source_file
    return summary


def _consensus_import_market(rows: list[dict[str, Any]]) -> str:
    country_to_market = {
        "United States": "US",
        "South Korea": "KR",
        "Japan": "JP",
        "US": "US",
        "KR": "KR",
        "JP": "JP",
    }
    markets = {
        country_to_market.get(_security_meta(row["ticker"], row["currency"]).country, "GLOBAL")
        for row in rows
    }
    if len(markets) == 1:
        return next(iter(markets))
    return "MULTI"


def import_fnguide_export(
    path: Path,
    *,
    sheet: str | None = None,
    persist: bool = False,
) -> dict[str, Any]:
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    records = _read_tabular_export(path, sheet=sheet)
    metric_rows, skipped_rows = _fnguide_metric_rows(records, sheet=sheet)
    if records and not metric_rows:
        raise ValueError("FnGuide/DataGuide export produced no metric rows")
    summary = _fnguide_import_summary(
        records,
        metric_rows,
        skipped_rows,
        persist=False,
        content_hash=digest,
    )
    if not persist:
        return summary

    repo = IngestionRepository()
    run_id = repo.start_run(
        market="KR",
        source="fnguide_user_export",
        ticker="BATCH",
        metadata={
            "file_name": path.name,
            "sheet": sheet,
            "content_hash": digest,
            "policy": "user_supplied_premium_import",
        },
    )
    queue = BlobUploadQueue()
    stored_metrics = 0
    try:
        document = ConnectorDocument(
            source="fnguide",
            market="KR",
            ticker="BATCH",
            identifier=f"{path.stem}-{digest[:12]}",
            url="https://dataguide.fnguide.com/",
            payload=payload,
            content_type=_content_type_for_path(path),
            metadata={
                "file_name": path.name,
                "sheet": sheet,
                "content_hash": digest,
                "source_type": "fnguide_user_export",
                "license_policy": "user_supplied_premium_import",
            },
        )
        local_path, _ = _write_raw_document(document)
        source_document = SourceDocument(
            id=digest,
            ticker=None,
            accession_number=document.identifier,
            form_type="FNGUIDE_USER_EXPORT",
            filing_url=None,
            source_url=document.url,
            content=(
                None
                if _is_binary_tabular(path)
                else payload.decode("utf-8-sig", errors="ignore")
            ),
            local_path=str(local_path),
            content_hash=digest,
            metadata=document.metadata
            | {"row_count": len(records), "metric_rows": len(metric_rows)},
        )
        source_document_id = repo.store_source_document(
            None,
            source_document,
            "fnguide_user_export",
        )
        blob_key = _blob_key(document, digest)
        queue.enqueue(
            BlobQueueItem(
                local_path=str(local_path),
                blob_key=blob_key,
                content_type=document.content_type,
                metadata=document.metadata,
            )
        )
        repo.store_raw_object(
            ingestion_run_id=run_id,
            source_document_id=source_document_id,
            market="KR",
            source="fnguide_user_export",
            ticker="BATCH",
            identifier=document.identifier,
            source_url=document.url,
            local_path=str(local_path),
            content_hash=digest,
            content_type=document.content_type,
            metadata=document.metadata,
            blob_key=blob_key,
        )
        for metric in metric_rows:
            meta = _security_meta(metric.ticker, metric.currency)
            security = repo.ensure_security(
                metric.ticker,
                metric.name or meta.name,
                "KR",
                metric.currency,
                meta.exchange or "KRX",
            )
            formula = (
                "user-supplied FnGuide/DataGuide export canonical row imported as "
                "metric_values.value"
            )
            source_trace = {
                "source_type": "fnguide_user_export",
                "source_document_id": str(source_document_id),
                "source_url": document.url,
                "filing_id": document.identifier,
                "period": f"{metric.fiscal_period}{metric.fiscal_year}",
                "unit": metric.unit,
                "currency": metric.currency,
                "formula": formula,
                "method": "FNGUIDE_USER_EXPORT",
                "quality_status": "user_supplied_premium_export",
                "content_hash": digest,
                "source_file": path.name,
                "source_sheet": metric.source_sheet,
                "source_row_number": metric.row_number,
                "source_metric_label": metric.metric_label,
                "raw_value": metric.raw_value,
            }
            repo.store_metric_value(
                security_id=security.id,
                source_document_id=source_document_id,
                metric_key=metric.metric_key,
                fiscal_year=metric.fiscal_year,
                value=metric.value,
                unit=metric.unit,
                currency=metric.currency,
                formula=formula,
                method="FNGUIDE_USER_EXPORT",
                quality_status="user_supplied_premium_export",
                source_trace=source_trace,
            )
            stored_metrics += 1
        repo.finish_run(run_id)
    except Exception as exc:
        repo.finish_run(run_id, status="failed", error_summary=str(exc))
        raise
    return summary | {
        "persisted": True,
        "metric_values": stored_metrics,
    }


def _fnguide_import_summary(
    records: list[dict[str, Any]],
    metric_rows: list[FnguideMetricRow],
    skipped_rows: int,
    *,
    persist: bool,
    content_hash: str,
) -> dict[str, Any]:
    return {
        "rows": len(records),
        "metric_rows": len(metric_rows),
        "skipped_rows": skipped_rows,
        "persisted": persist,
        "content_hash": content_hash,
        "source_type": "fnguide_user_export",
        "tickers": sorted({row.ticker for row in metric_rows}),
        "fiscal_years": sorted({row.fiscal_year for row in metric_rows}),
        "metric_keys": sorted({row.metric_key for row in metric_rows}),
        "units": sorted({row.unit for row in metric_rows}),
        "currencies": sorted({row.currency for row in metric_rows}),
    }


def collect_sec_bulk_archives(
    archives: str | list[str],
    *,
    persist: bool = False,
    force_refresh: bool = False,
) -> dict[str, Any]:
    requested_archives = [archive.lower() for archive in _split_csv(archives)]
    documents = SecBulkConnector().collect_bulk(
        requested_archives,
        force_refresh=force_refresh,
    )
    persisted = _persist_sec_bulk_documents(documents) if persist else []
    return {
        "status": "ok",
        "market": "US",
        "archives": requested_archives,
        "documents": [_document_summary(document) for document in documents],
        "zip_archives": len(documents),
        "bytes": sum(len(document.payload) for document in documents),
        "persisted": persisted,
    }


def load_sec_bulk_warehouse(
    *,
    companyfacts_zip: Path | None = None,
    submissions_zip: Path | None = None,
    tickers: str | list[str] = "AAPL,NVDA,CRM,O,JPM",
    persist: bool = False,
    max_companies: int | None = None,
) -> dict[str, Any]:
    companyfacts_path = companyfacts_zip or _latest_sec_bulk_zip("companyfacts")
    submissions_path = submissions_zip or _latest_sec_bulk_zip("submissions")
    requested_tickers = _split_csv(tickers)
    submissions = parse_submissions_zip(submissions_path) if submissions_path else {}
    fact_rows = parse_companyfacts_zip(
        companyfacts_path,
        submissions=submissions,
        tickers=requested_tickers,
        max_companies=max_companies,
    )
    primary_rows = primary_metric_rows(fact_rows)
    metric_rows = [*primary_rows, *derived_metric_rows(primary_rows)]
    persisted = (
        _persist_sec_bulk_warehouse(
            companyfacts_path,
            submissions_path,
            fact_rows,
            metric_rows,
        )
        if persist
        else {"financial_facts": 0, "metric_values": 0, "source_documents": 0}
    )
    return {
        "status": "ok",
        "market": "US",
        "tickers": requested_tickers,
        "companyfacts_zip": str(companyfacts_path),
        "submissions_zip": str(submissions_path) if submissions_path else None,
        "companies": sorted({row.ticker for row in fact_rows}),
        "financial_facts": len(fact_rows),
        "metric_values": len(metric_rows),
        "persisted": persisted,
    }


def collect_fred_series(
    series: str | list[str],
    start_year: int,
    end_year: int,
    *,
    persist: bool = False,
    force_refresh: bool = False,
) -> dict[str, Any]:
    series_ids = _split_csv(series)
    documents: list[ConnectorDocument] = []
    connector = FredConnector()
    for series_id in series_ids:
        documents.extend(
            connector.collect(
                ConnectorRequest(
                    ticker=series_id,
                    market="GLOBAL",
                    start_year=start_year,
                    end_year=end_year,
                    force_refresh=force_refresh,
                )
            )
        )
    persisted = _persist_fred_documents(documents) if persist else []
    return {
        "status": "ok",
        "series": series_ids,
        "documents": [_document_summary(document) for document in documents],
        "observation_count": sum(_fred_observation_count(document) for document in documents),
        "persisted": persisted,
    }


def collect_ecos_series(
    series: str | list[str],
    start_year: int,
    end_year: int,
    *,
    persist: bool = False,
    force_refresh: bool = False,
) -> dict[str, Any]:
    series_ids = _split_identifiers(series)
    if not series_ids:
        raise ValueError("at least one ECOS series spec is required")
    documents: list[ConnectorDocument] = []
    connector = EcosConnector()
    for series_id in series_ids:
        documents.extend(
            connector.collect(
                ConnectorRequest(
                    ticker=series_id,
                    market="KR",
                    start_year=start_year,
                    end_year=end_year,
                    force_refresh=force_refresh,
                )
            )
        )
    persisted = (
        _persist_raw_stat_documents(
            "KR",
            "ecos",
            "ECOS_BATCH",
            "ECOS_STATISTIC_SEARCH",
            documents,
            normalize_macro=True,
        )
        if persist
        else []
    )
    normalized_count = sum(
        len(normalize_official_stat_document(document))
        for document in documents
    )
    return {
        "status": "ok",
        "market": "KR",
        "series": series_ids,
        "documents": [_document_summary(document) for document in documents],
        "observation_count": sum(_json_payload_row_count(document) for document in documents),
        "normalized_observation_count": normalized_count,
        "persisted": persisted,
    }


def collect_kosis_tables(
    tables: str | list[str],
    start_year: int,
    end_year: int,
    *,
    persist: bool = False,
    force_refresh: bool = False,
) -> dict[str, Any]:
    table_ids = _split_identifiers(tables)
    if not table_ids:
        raise ValueError("at least one KOSIS table spec is required")
    documents: list[ConnectorDocument] = []
    connector = KosisConnector()
    for table_id in table_ids:
        documents.extend(
            connector.collect(
                ConnectorRequest(
                    ticker=table_id,
                    market="KR",
                    start_year=start_year,
                    end_year=end_year,
                    force_refresh=force_refresh,
                )
            )
        )
    persisted = (
        _persist_raw_stat_documents(
            "KR",
            "kosis",
            "KOSIS_BATCH",
            "KOSIS_STATISTICS_DATA",
            documents,
            normalize_macro=True,
        )
        if persist
        else []
    )
    normalized_count = sum(
        len(normalize_official_stat_document(document))
        for document in documents
    )
    return {
        "status": "ok",
        "market": "KR",
        "tables": table_ids,
        "documents": [_document_summary(document) for document in documents],
        "observation_count": sum(_json_payload_row_count(document) for document in documents),
        "normalized_observation_count": normalized_count,
        "persisted": persisted,
    }


def collect_estat_tables(
    stats_data_ids: str | list[str],
    start_year: int,
    end_year: int,
    *,
    persist: bool = False,
    force_refresh: bool = False,
) -> dict[str, Any]:
    table_ids = _split_identifiers(stats_data_ids)
    if not table_ids:
        raise ValueError("at least one e-Stat statsDataId is required")
    documents: list[ConnectorDocument] = []
    connector = EStatConnector()
    for table_id in table_ids:
        documents.extend(
            connector.collect(
                ConnectorRequest(
                    ticker=table_id,
                    market="JP",
                    start_year=start_year,
                    end_year=end_year,
                    force_refresh=force_refresh,
                )
            )
        )
    persisted = (
        _persist_raw_stat_documents(
            "JP",
            "estat",
            "ESTAT_BATCH",
            "ESTAT_GET_STATS_DATA",
            documents,
            normalize_macro=True,
        )
        if persist
        else []
    )
    normalized_count = sum(
        len(normalize_official_stat_document(document))
        for document in documents
    )
    return {
        "status": "ok",
        "market": "JP",
        "stats_data_ids": table_ids,
        "documents": [_document_summary(document) for document in documents],
        "observation_count": sum(_json_payload_row_count(document) for document in documents),
        "normalized_observation_count": normalized_count,
        "persisted": persisted,
    }


def collect_stooq_prices(
    tickers: str | list[str],
    market: str,
    start_year: int,
    end_year: int,
    *,
    persist: bool = False,
    force_refresh: bool = False,
) -> dict[str, Any]:
    requested_tickers = _split_csv(tickers)
    documents: list[ConnectorDocument] = []
    connector = StooqConnector()
    for ticker in requested_tickers:
        documents.extend(
            connector.collect(
                ConnectorRequest(
                    ticker=ticker,
                    market=market,
                    start_year=start_year,
                    end_year=end_year,
                    force_refresh=force_refresh,
                )
            )
        )
    persisted = _persist_stooq_documents(market, documents) if persist else []
    return {
        "status": "ok",
        "market": market.upper(),
        "tickers": requested_tickers,
        "documents": [_document_summary(document) for document in documents],
        "price_rows": sum(_stooq_price_row_count(document) for document in documents),
        "persisted": persisted,
    }


def collect_fdr_prices(
    tickers: str | list[str],
    market: str,
    start_year: int,
    end_year: int,
    *,
    persist: bool = False,
    force_refresh: bool = False,
) -> dict[str, Any]:
    requested_tickers = _split_csv(tickers)
    documents: list[ConnectorDocument] = []
    connector = FinanceDataReaderConnector()
    for ticker in requested_tickers:
        documents.extend(
            connector.collect(
                ConnectorRequest(
                    ticker=ticker,
                    market=market,
                    start_year=start_year,
                    end_year=end_year,
                    force_refresh=force_refresh,
                )
            )
        )
    persisted = _persist_fdr_documents(market, documents) if persist else []
    return {
        "status": "ok",
        "market": market.upper(),
        "tickers": requested_tickers,
        "documents": [_document_summary(document) for document in documents],
        "price_rows": sum(_fdr_price_row_count(document) for document in documents),
        "persisted": persisted,
    }


def collect_pykrx_prices(
    tickers: str | list[str],
    start_year: int,
    end_year: int,
    *,
    persist: bool = False,
    force_refresh: bool = False,
    sleep_seconds: float = 0.5,
) -> dict[str, Any]:
    requested_tickers = _split_csv(tickers)
    documents: list[ConnectorDocument] = []
    connector = PyKrxConnector()
    for index, ticker in enumerate(requested_tickers):
        documents.extend(
            connector.collect(
                ConnectorRequest(
                    ticker=ticker,
                    market="KR",
                    start_year=start_year,
                    end_year=end_year,
                    force_refresh=force_refresh,
                )
            )
        )
        if sleep_seconds > 0 and index < len(requested_tickers) - 1:
            time.sleep(sleep_seconds)
    raw_documents = [] if persist else _cache_raw_documents(documents)
    persisted = _persist_pykrx_documents(documents) if persist else []
    return {
        "status": "ok",
        "market": "KR",
        "tickers": requested_tickers,
        "documents": [_document_summary(document) for document in documents],
        "raw_documents": raw_documents,
        "price_rows": sum(_pykrx_price_row_count(document) for document in documents),
        "persisted": persisted,
    }


def collect_pykrx_fundamentals(
    tickers: str | list[str],
    start_year: int,
    end_year: int,
    *,
    persist: bool = False,
    force_refresh: bool = False,
    sleep_seconds: float = 0.5,
) -> dict[str, Any]:
    requested_tickers = _split_csv(tickers)
    documents: list[ConnectorDocument] = []
    connector = PyKrxConnector()
    for index, ticker in enumerate(requested_tickers):
        documents.extend(
            connector.collect_fundamentals(
                ConnectorRequest(
                    ticker=ticker,
                    market="KR",
                    start_year=start_year,
                    end_year=end_year,
                    force_refresh=force_refresh,
                )
            )
        )
        if sleep_seconds > 0 and index < len(requested_tickers) - 1:
            time.sleep(sleep_seconds)
    raw_documents = [] if persist else _cache_raw_documents(documents)
    persisted = _persist_pykrx_documents(documents) if persist else []
    return {
        "status": "ok",
        "market": "KR",
        "tickers": requested_tickers,
        "documents": [_document_summary(document) for document in documents],
        "raw_documents": raw_documents,
        "fundamental_rows": sum(_pykrx_fundamental_row_count(document) for document in documents),
        "persisted": persisted,
    }


def collect_marcap_data(
    tickers: str | list[str],
    start_year: int,
    end_year: int,
    *,
    persist: bool = False,
    force_refresh: bool = False,
) -> dict[str, Any]:
    requested_tickers = _split_csv(tickers) if tickers else []
    documents = MarcapConnector().collect(
        ConnectorRequest(
            ticker="KR_MARKET",
            market="KR",
            start_year=start_year,
            end_year=end_year,
            force_refresh=force_refresh,
        )
    )
    raw_documents = [] if persist else _cache_raw_documents(documents)
    persisted = _persist_marcap_documents(documents, requested_tickers) if persist else []
    return {
        "status": "ok",
        "market": "KR",
        "tickers": requested_tickers or ["ALL"],
        "documents": [_document_summary(document) for document in documents],
        "raw_documents": raw_documents,
        "price_rows": sum(
            _marcap_price_row_count(document, requested_tickers) for document in documents
        ),
        "persisted": persisted,
    }


def inspect_raw_kr_evidence(
    tickers: str | list[str],
    start_year: int,
    end_year: int,
    *,
    raw_root: Path = Path("storage/raw"),
    require_opendart: bool = False,
) -> dict[str, Any]:
    requested_tickers = _split_csv(tickers)
    ticker_reports = [
        _inspect_raw_kr_ticker(ticker, start_year, end_year, raw_root, require_opendart=require_opendart)
        for ticker in requested_tickers
    ]
    ok_count = sum(1 for report in ticker_reports if report["status"] == "ok")
    valuation_ready_count = sum(1 for report in ticker_reports if report["valuation_ready"])
    partial_count = sum(
        1
        for report in ticker_reports
        if report.get("coverage_status") == "partial_source_backed"
    )
    missing = [
        report["ticker"]
        for report in ticker_reports
        if report["status"] != "ok"
    ]
    return {
        "status": "ok" if ok_count == len(ticker_reports) else "partial" if ok_count else "missing",
        "market": "KR",
        "data_mode": "raw_source_evidence_only",
        "years": {"start": start_year, "end": end_year},
        "raw_root": str(raw_root),
        "require_opendart": require_opendart,
        "summary": {
            "tickers_expected": len(ticker_reports),
            "tickers_ok": ok_count,
            "valuation_ready": valuation_ready_count,
            "partial_source_backed": partial_count,
            "missing": missing,
        },
        "next_actions": _kr_raw_evidence_next_actions(
            ticker_reports,
            start_year,
            end_year,
            require_opendart=require_opendart,
        ),
        "tickers": ticker_reports,
    }


def build_kr_valuation_inputs(
    tickers: str | list[str],
    start_year: int,
    end_year: int,
    *,
    raw_root: Path = Path("storage/raw"),
    out_dir: Path = Path("storage/cache/kr-valuation-inputs"),
) -> dict[str, Any]:
    requested_tickers = _split_csv(tickers)
    out_dir.mkdir(parents=True, exist_ok=True)
    ticker_reports = [
        _build_kr_valuation_ticker_inputs(ticker, start_year, end_year, raw_root, out_dir)
        for ticker in requested_tickers
    ]
    ok_count = sum(1 for report in ticker_reports if report["status"] == "ok")
    valuation_ready_count = sum(1 for report in ticker_reports if report["valuation_ready"])
    partial_count = sum(
        1
        for report in ticker_reports
        if report.get("coverage_status") == "partial_source_backed"
    )
    missing = [
        report["ticker"]
        for report in ticker_reports
        if report["status"] != "ok"
    ]
    return {
        "status": "ok" if ok_count == len(ticker_reports) else "partial" if ok_count else "missing",
        "market": "KR",
        "data_mode": "source_backed_raw_valuation_inputs",
        "years": {"start": start_year, "end": end_year},
        "raw_root": str(raw_root),
        "output_dir": str(out_dir),
        "summary": {
            "tickers_expected": len(ticker_reports),
            "tickers_ok": ok_count,
            "valuation_ready": valuation_ready_count,
            "partial_source_backed": partial_count,
            "missing": missing,
        },
        "next_actions": _kr_valuation_input_next_actions(
            ticker_reports,
            start_year,
            end_year,
        ),
        "tickers": ticker_reports,
    }


def _kr_raw_evidence_next_actions(
    ticker_reports: list[dict[str, Any]],
    start_year: int,
    end_year: int,
    *,
    require_opendart: bool,
) -> list[dict[str, Any]]:
    tickers = [str(report["ticker"]) for report in ticker_reports]
    missing_checks = {
        str(check["name"])
        for report in ticker_reports
        if not report.get("valuation_ready")
        for check in report.get("checks", [])
        if not check.get("ok")
    }
    actions: list[dict[str, Any]] = []

    if {
        "opendart_raw_file",
        "opendart_metric_rows",
        "opendart_metric_year_coverage",
        "opendart_adjusted_operating_eps",
        "opendart_eps_year_coverage",
    } & missing_checks:
        actions.extend(_kr_opendart_next_actions(tickers, start_year, end_year))
    if {"pykrx_raw_file", "pykrx_rows", "pykrx_year_coverage"} & missing_checks:
        actions.append(
            _operator_action(
                "collect_pykrx_kr",
                f"python -m services.ingestion_worker.cli collect-pykrx-prices --tickers {','.join(tickers)} --years {start_year}:{end_year}",
                "Collect source-backed KR OHLCV raw CSV before valuation inputs are built.",
            )
        )
    if {
        "marcap_raw_file",
        "marcap_rows",
        "marcap_year_coverage",
        "market_cap_evidence",
        "market_cap_year_coverage",
        "listed_shares_evidence",
        "listed_shares_year_coverage",
    } & missing_checks:
        actions.append(
            _operator_action(
                "collect_marcap_kr",
                f"python -m services.ingestion_worker.cli collect-marcap --tickers {','.join(tickers)} --years {start_year}:{end_year}",
                "Collect source-backed KR market cap and listed shares raw parquet.",
            )
        )

    inspect_flags = " --require-opendart" if require_opendart else ""
    actions.append(
        _operator_action(
            "inspect_raw_kr",
            f"python -m services.ingestion_worker.cli inspect-raw-kr --tickers {','.join(tickers)} --years {start_year}:{end_year}{inspect_flags} --strict",
            "Re-run the raw evidence gate after collection.",
        )
    )
    actions.append(
        _operator_action(
            "build_kr_valuation_inputs",
            f"python -m services.ingestion_worker.cli build-kr-valuation-inputs --tickers {','.join(tickers)} --years {start_year}:{end_year} --strict",
            "Build source-backed valuation-map input cache once raw evidence is complete.",
        )
    )
    return actions


def _kr_valuation_input_next_actions(
    ticker_reports: list[dict[str, Any]],
    start_year: int,
    end_year: int,
) -> list[dict[str, Any]]:
    tickers = [str(report["ticker"]) for report in ticker_reports]
    missing_raw_metric_tickers: list[str] = []
    parser_gap_tickers: list[str] = []
    unknown_metric_tickers: list[str] = []
    source_no_data_tickers: list[str] = []
    for report in ticker_reports:
        ticker = str(report["ticker"])
        if report.get("metric_status", {}).get("status") == "ok":
            continue
        gap_statuses = {
            str(gap.get("status"))
            for gap in report.get("financial_gap_diagnostics", [])
        }
        if "missing_raw" in gap_statuses:
            missing_raw_metric_tickers.append(ticker)
        if "parser_metric_gap" in gap_statuses:
            parser_gap_tickers.append(ticker)
        if "source_no_data" in gap_statuses:
            source_no_data_tickers.append(ticker)
        if not gap_statuses or gap_statuses - {"missing_raw", "parser_metric_gap", "source_no_data"}:
            unknown_metric_tickers.append(ticker)
    market_missing = any(
        str(flag).startswith("missing_market_input_")
        for report in ticker_reports
        for flag in report.get("quality_flags", [])
    )
    dividend_missing_tickers = _dedupe_preserve_order(
        [
            str(report["ticker"])
            for report in ticker_reports
            if report.get("dividend_status", {}).get("status") in {"blocked", "partial"}
        ]
    )
    market_gap_statuses = {
        str(gap.get("status"))
        for report in ticker_reports
        for gap in report.get("market_gap_diagnostics", [])
    }
    actions: list[dict[str, Any]] = []
    collect_metric_tickers = _dedupe_preserve_order(missing_raw_metric_tickers + unknown_metric_tickers)
    if collect_metric_tickers:
        actions.extend(_kr_opendart_next_actions(collect_metric_tickers, start_year, end_year))
    if parser_gap_tickers:
        parser_tickers = _dedupe_preserve_order(parser_gap_tickers)
        actions.append(
            _operator_action(
                "inspect_opendart_metric_mapping",
                f"python -m services.ingestion_worker.cli inspect-raw-kr --tickers {','.join(parser_tickers)} --years {start_year}:{end_year} --require-opendart --strict",
                "OpenDART raw files exist but EPS did not normalize; inspect account_id/account_nm mapping before recollecting.",
            )
        )
    if source_no_data_tickers:
        no_data_tickers = _dedupe_preserve_order(source_no_data_tickers)
        actions.append(
            _operator_action(
                "document_opendart_no_data_years",
                "no-op",
                (
                    "OpenDART returned no-data or empty annual facts for some requested years; "
                    f"keep partial coverage for {','.join(no_data_tickers)} or add an alternate source."
                ),
            )
        )
    if market_missing and market_gap_statuses <= {"source_no_rows_before_first_trade"}:
        market_history_tickers = _dedupe_preserve_order(
            [
                str(report["ticker"])
                for report in ticker_reports
                if report.get("market_gap_diagnostics")
            ]
        )
        actions.append(
            _operator_action(
                "document_market_history_start",
                "no-op",
                (
                    "KR market rows begin after the requested range for some tickers; "
                    f"keep partial coverage for {','.join(market_history_tickers)} or shorten the requested period."
                ),
            )
        )
    elif market_missing:
        actions.append(
            _operator_action(
                "collect_kr_market_raw",
                f"python -m services.ingestion_worker.cli collect-pykrx-prices --tickers {','.join(tickers)} --years {start_year}:{end_year}",
                "Refresh KR price raw files for missing valuation input years.",
            )
        )
        actions.append(
            _operator_action(
                "collect_kr_market_structure_raw",
                f"python -m services.ingestion_worker.cli collect-marcap --tickers {','.join(tickers)} --years {start_year}:{end_year}",
                "Refresh KR market cap and listed shares raw files for missing valuation input years.",
            )
        )
    if dividend_missing_tickers:
        actions.append(
            _operator_action(
                "collect_kr_dividend_raw",
                (
                    "python -m services.ingestion_worker.cli collect-opendart-dividends "
                    f"--tickers {','.join(dividend_missing_tickers)} --years {start_year}:{end_year}"
                ),
                "Collect source-backed KR cash dividend-per-share raw JSON from OpenDART alotMatter.",
            )
        )
    actions.append(
        _operator_action(
            "build_kr_valuation_inputs",
            f"python -m services.ingestion_worker.cli build-kr-valuation-inputs --tickers {','.join(tickers)} --years {start_year}:{end_year} --strict",
            "Rebuild source-backed valuation-map input cache.",
        )
    )
    return actions


def _kr_opendart_next_actions(tickers: list[str], start_year: int, end_year: int) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if not _has_opendart_key():
        actions.append(
            _operator_action(
                "load_local_secrets",
                "pnpm secrets:local",
                "OpenDART collection requires OPENDART_API_KEY or DART_API_KEY in .env.local or process environment.",
            )
        )
    for ticker in tickers:
        actions.append(
            _operator_action(
                "collect_opendart_kr",
                f"python -m services.ingestion_worker.cli collect --market KR --ticker {ticker} --years {start_year}:{end_year}",
                "Collect append-only OpenDART financial statement raw JSON for EPS and financial metrics.",
                ticker=ticker,
            )
        )
    return actions


def _operator_action(
    action_id: str,
    command: str,
    reason: str,
    *,
    ticker: str | None = None,
) -> dict[str, Any]:
    action: dict[str, Any] = {
        "id": action_id,
        "command": command,
        "reason": reason,
        "secrets_redacted": True,
    }
    if ticker:
        action["ticker"] = ticker
    return action


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _build_kr_valuation_ticker_inputs(
    ticker: str,
    start_year: int,
    end_year: int,
    raw_root: Path,
    out_dir: Path,
) -> dict[str, Any]:
    normalized_ticker = ticker.upper()
    krx_code = _marcap_code_from_ticker(normalized_ticker)
    entity_id = f"kr:{normalized_ticker}"
    pykrx_price_files = _raw_files(raw_root / "pykrx" / normalized_ticker, "*ohlcv*.csv")
    pykrx_fundamental_files = _raw_files(
        raw_root / "pykrx" / normalized_ticker,
        "*fundamental*.csv",
    )
    marcap_files = _latest_raw_files_by_year(
        raw_root / "marcap" / "KR_MARKET",
        "marcap-*.parquet",
        start_year,
        end_year,
    )
    pykrx_documents = [
        _raw_connector_document(path, "pykrx", normalized_ticker, "text/csv")
        for path in pykrx_price_files
    ]
    pykrx_fundamental_documents = [
        _raw_connector_document(path, "pykrx", normalized_ticker, "text/csv")
        for path in pykrx_fundamental_files
    ]
    marcap_documents = [
        _raw_connector_document(path, "marcap", "KR_MARKET", "application/vnd.apache.parquet")
        for path in marcap_files
    ]
    opendart_documents = _raw_opendart_documents(
        raw_root,
        normalized_ticker,
        start_year,
        end_year,
    )
    opendart_dividend_documents = _raw_opendart_dividend_documents(
        raw_root,
        normalized_ticker,
        start_year,
        end_year,
    )
    opendart_dividend_documents = _raw_opendart_dividend_documents(
        raw_root,
        normalized_ticker,
        start_year,
        end_year,
    )

    pykrx_rows_by_year: dict[int, tuple[dict[str, Any], ConnectorDocument]] = {}
    for document in pykrx_documents:
        for row in _pykrx_csv_rows(document):
            row_date = _date_or_none(row["date"])
            if row_date and start_year <= row_date.year <= end_year:
                current = pykrx_rows_by_year.get(row_date.year)
                if current is None or row_date > date.fromisoformat(current[0]["date"]):
                    pykrx_rows_by_year[row_date.year] = (row, document)

    pykrx_fundamental_rows_by_year: dict[int, tuple[dict[str, Any], ConnectorDocument]] = {}
    for document in pykrx_fundamental_documents:
        for row in _pykrx_fundamental_csv_rows(document):
            row_date = _date_or_none(row["date"])
            if row_date and start_year <= row_date.year <= end_year:
                current = pykrx_fundamental_rows_by_year.get(row_date.year)
                if current is None or row_date > date.fromisoformat(current[0]["date"]):
                    pykrx_fundamental_rows_by_year[row_date.year] = (row, document)

    opendart_dividend_rows_by_year: dict[int, tuple[Decimal, dict[str, Any], ConnectorDocument]] = {}
    for document in opendart_dividend_documents:
        dividend_per_share, evidence = _opendart_dividend_per_share(document)
        fiscal_year = _int_or_none(document.metadata.get("bsns_year"))
        if (
            dividend_per_share is not None
            and evidence is not None
            and fiscal_year is not None
            and start_year <= fiscal_year <= end_year
        ):
            opendart_dividend_rows_by_year[fiscal_year] = (
                dividend_per_share,
                evidence,
                document,
            )

    marcap_rows_by_year: dict[int, tuple[dict[str, Any], ConnectorDocument]] = {}
    for document in marcap_documents:
        rows = _marcap_rows(document, {krx_code})
        for row in rows:
            row_date = _date_or_none(_first_present(row, "Date", "date"))
            if row_date and start_year <= row_date.year <= end_year:
                current = marcap_rows_by_year.get(row_date.year)
                current_date = (
                    _date_or_none(_first_present(current[0], "Date", "date"))
                    if current
                    else None
                )
                if current_date is None or row_date > current_date:
                    marcap_rows_by_year[row_date.year] = (row, document)

    normalized_facts: list[dict[str, Any]] = []
    price_facts_by_year: dict[int, dict[str, Any]] = {}
    dividend_facts_by_year: dict[int, dict[str, Any]] = {}
    eps_facts_by_year: dict[int, dict[str, Any]] = {}
    price_years: list[int] = []
    market_structure_years: list[int] = []
    for fiscal_year in range(start_year, end_year + 1):
        price_tuple = pykrx_rows_by_year.get(fiscal_year)
        price_source = "pykrx"
        if price_tuple is None:
            price_tuple = marcap_rows_by_year.get(fiscal_year)
            price_source = "marcap"
        if price_tuple is not None:
            row, document = price_tuple
            trade_date = _date_or_none(_first_present(row, "date", "Date"))
            close_price = _decimal_from_any(_first_present(row, "close", "Close"))
            if trade_date and close_price is not None:
                price_trace = _kr_market_fact_source_trace(
                    document,
                    ticker=normalized_ticker,
                    metric="price_close",
                    fiscal_year=fiscal_year,
                    trade_date=trade_date,
                    unit="KRW/share",
                    method=f"{price_source.upper()}_RAW_YEAR_END_CLOSE",
                    formula=(
                        "Select the latest source-backed close price inside the fiscal year "
                        f"from {price_source} raw market data"
                    ),
                    row=row,
                    confidence=Decimal("0.95") if price_source == "pykrx" else Decimal("0.90"),
                    quality_flags=["source_backed_price"],
                )
                price_fact = _kr_normalized_fact(
                    entity_id,
                    normalized_ticker,
                    fiscal_year,
                    "price_close",
                    close_price,
                    "KRW/share",
                    price_trace,
                )
                normalized_facts.append(price_fact)
                price_facts_by_year[fiscal_year] = price_fact
                price_years.append(fiscal_year)

        dividend_tuple = pykrx_fundamental_rows_by_year.get(fiscal_year)
        if dividend_tuple is not None:
            row, document = dividend_tuple
            trade_date = _date_or_none(_first_present(row, "date", "Date"))
            dividend_per_share = _decimal_from_any(_first_present(row, "dps", "DPS"))
            if trade_date and dividend_per_share is not None:
                dividend_trace = _kr_market_fact_source_trace(
                    document,
                    ticker=normalized_ticker,
                    metric="dividend_per_share",
                    fiscal_year=fiscal_year,
                    trade_date=trade_date,
                    unit="KRW/share",
                    method="PYKRX_RAW_YEAR_END_DPS",
                    formula=(
                        "Select the latest source-backed DPS inside the fiscal year "
                        "from pykrx fundamental raw data"
                    ),
                    row=row,
                    confidence=Decimal("0.90"),
                    quality_flags=["source_backed_dividend"],
                )
                dividend_fact = _kr_normalized_fact(
                    entity_id,
                    normalized_ticker,
                    fiscal_year,
                    "dividend_per_share",
                    dividend_per_share,
                    "KRW/share",
                    dividend_trace,
                )
                normalized_facts.append(dividend_fact)
                dividend_facts_by_year[fiscal_year] = dividend_fact

        if fiscal_year not in dividend_facts_by_year:
            opendart_dividend_tuple = opendart_dividend_rows_by_year.get(fiscal_year)
            if opendart_dividend_tuple is not None:
                dividend_per_share, evidence, document = opendart_dividend_tuple
                dividend_quality_flags = ["source_backed_dividend", "opendart_dividend_fallback"]
                if evidence.get("dividend_zero_assumption") == "opendart_dash_no_cash_dividend":
                    dividend_quality_flags.append("opendart_dash_no_cash_dividend_assumed_zero")
                dividend_trace = _kr_dividend_fact_source_trace(
                    document,
                    ticker=normalized_ticker,
                    fiscal_year=fiscal_year,
                    unit="KRW/share",
                    method="OPENDART_ALOT_MATTER_DPS",
                    formula=(
                        "OpenDART alotMatter current-period cash dividend per share "
                        "row normalized as dividend_per_share"
                    ),
                    evidence=evidence,
                    confidence=Decimal("0.88"),
                    quality_flags=dividend_quality_flags,
                )
                dividend_fact = _kr_normalized_fact(
                    entity_id,
                    normalized_ticker,
                    fiscal_year,
                    "dividend_per_share",
                    dividend_per_share,
                    "KRW/share",
                    dividend_trace,
                )
                normalized_facts.append(dividend_fact)
                dividend_facts_by_year[fiscal_year] = dividend_fact

        market_tuple = marcap_rows_by_year.get(fiscal_year)
        if market_tuple is None:
            continue
        row, document = market_tuple
        trade_date = _date_or_none(_first_present(row, "Date", "date"))
        if trade_date is None:
            continue
        market_cap = _normalized_marcap_market_cap(row)
        listed_shares = _decimal_from_any(_first_present(row, "Stocks", "stocks"))
        if market_cap["value"] is not None:
            market_cap_trace = _kr_market_fact_source_trace(
                document,
                ticker=normalized_ticker,
                metric="market_cap",
                fiscal_year=fiscal_year,
                trade_date=trade_date,
                unit="KRW",
                method="MARCAP_RAW_MARKET_STRUCTURE",
                formula=str(market_cap["formula"]),
                row=row,
                confidence=Decimal("0.90"),
                quality_flags=["source_backed_market_cap", *market_cap["quality_flags"]],
                extra_fields={
                    "market_cap_raw": market_cap["raw_value"],
                    "market_cap_raw_unit_detected": market_cap["raw_unit_detected"],
                    "market_cap_normalized_unit": "KRW",
                },
            )
            normalized_facts.append(
                _kr_normalized_fact(
                    entity_id,
                    normalized_ticker,
                    fiscal_year,
                    "market_cap",
                    market_cap["value"],
                    "KRW",
                    market_cap_trace,
                )
            )
        if listed_shares is not None:
            shares_trace = _kr_market_fact_source_trace(
                document,
                ticker=normalized_ticker,
                metric="listed_shares",
                fiscal_year=fiscal_year,
                trade_date=trade_date,
                unit="shares",
                method="MARCAP_RAW_MARKET_STRUCTURE",
                formula="FinanceData marcap raw Stocks value",
                row=row,
                confidence=Decimal("0.90"),
                quality_flags=["source_backed_listed_shares"],
            )
            normalized_facts.append(
                _kr_normalized_fact(
                    entity_id,
                    normalized_ticker,
                    fiscal_year,
                    "listed_shares",
                    listed_shares,
                    "shares",
                    shares_trace,
                )
            )
        if market_cap["value"] is not None and listed_shares is not None:
            market_structure_years.append(fiscal_year)

    financial_metric_years: set[int] = set()
    financial_source_count = 0
    for document in opendart_documents:
        result = normalize_market_standard_document(document, entity_id, "KRW")
        if result.adjusted_record is not None and result.adjusted_record.adjusted_eps is not None:
            record = result.adjusted_record
            fiscal_year = record.fiscal_year
            method = str(record.method)
            adjusted_trace = _kr_financial_fact_source_trace(
                document,
                ticker=normalized_ticker,
                metric="adjusted_operating_eps",
                fiscal_year=fiscal_year,
                unit="KRW/share",
                method=method,
                formula=record.formula
                or "OpenDART reported EPS mapped as KR market-standard adjusted operating EPS",
                confidence=record.confidence,
                quality_flags=["source_backed_financial_metric", "s3_market_standard_kr"],
                evidence=record.source_trace.model_dump(mode="json"),
            )
            adjusted_fact = _kr_normalized_fact(
                entity_id,
                normalized_ticker,
                fiscal_year,
                "adjusted_operating_eps",
                record.adjusted_eps,
                "KRW/share",
                adjusted_trace,
            )
            normalized_facts.append(adjusted_fact)
            eps_facts_by_year[fiscal_year] = adjusted_fact
            financial_metric_years.add(fiscal_year)
            financial_source_count += 1

            if record.gaap_eps_diluted is not None:
                gaap_trace = _kr_financial_fact_source_trace(
                    document,
                    ticker=normalized_ticker,
                    metric="gaap_diluted_eps",
                    fiscal_year=fiscal_year,
                    unit="KRW/share",
                    method=method,
                    formula="OpenDART reported diluted EPS; market-standard S3 KR mapping",
                    confidence=record.confidence,
                    quality_flags=["source_backed_financial_metric", "s3_market_standard_kr"],
                    evidence=record.source_trace.model_dump(mode="json") | {"metric_key": "gaap_diluted_eps"},
                )
                normalized_facts.append(
                    _kr_normalized_fact(
                        entity_id,
                        normalized_ticker,
                        fiscal_year,
                        "gaap_diluted_eps",
                        record.gaap_eps_diluted,
                        "KRW/share",
                        gaap_trace,
                    )
                )

        for metric in result.metrics:
            if not (start_year <= metric.fiscal_year <= end_year):
                continue
            trace = _kr_financial_fact_source_trace(
                document,
                ticker=normalized_ticker,
                metric=metric.metric_key,
                fiscal_year=metric.fiscal_year,
                unit=metric.unit,
                method=metric.method,
                formula=metric.formula,
                confidence=Decimal("0.85"),
                quality_flags=["source_backed_financial_metric", "s3_market_standard_kr"],
                evidence=metric.source_trace,
            )
            normalized_facts.append(
                _kr_normalized_fact(
                    entity_id,
                    normalized_ticker,
                    metric.fiscal_year,
                    metric.metric_key,
                    metric.value,
                    metric.unit,
                    trace,
                )
            )
            financial_metric_years.add(metric.fiscal_year)
            financial_source_count += 1

    missing_years = [
        fiscal_year
        for fiscal_year in range(start_year, end_year + 1)
        if fiscal_year not in price_years or fiscal_year not in market_structure_years
    ]
    missing_metric_years = [
        fiscal_year
        for fiscal_year in range(start_year, end_year + 1)
        if fiscal_year not in eps_facts_by_year
    ]
    market_gap_diagnostics = _kr_market_gap_diagnostics(
        start_year,
        end_year,
        pykrx_rows_by_year,
        marcap_rows_by_year,
        price_years,
        market_structure_years,
    )
    financial_gap_diagnostics = _kr_financial_gap_diagnostics(
        opendart_documents,
        start_year,
        end_year,
        eps_facts_by_year,
    )
    valuation_points = _kr_source_backed_valuation_points(
        normalized_ticker,
        entity_id,
        price_facts_by_year,
        eps_facts_by_year,
        dividend_facts_by_year,
    )
    dividend_required_years = sorted(
        {int(point["fiscal_year"]) for point in valuation_points}
        or set(range(start_year, end_year + 1))
    )
    dividend_missing_years = [
        fiscal_year
        for fiscal_year in dividend_required_years
        if fiscal_year not in dividend_facts_by_year
    ]
    dividend_methods = sorted(
        {
            SourceTrace(**fact["source_trace"]).method
            for fact in dividend_facts_by_year.values()
        }
    )
    dividend_method = "+".join(dividend_methods) if dividend_methods else None
    if dividend_facts_by_year and not dividend_missing_years:
        dividend_status = {
            "status": "ok",
            "method": dividend_method,
            "dividend_years": sorted(dividend_facts_by_year),
            "quality_flags": ["source_backed_dividend"],
        }
    elif dividend_facts_by_year:
        dividend_status = {
            "status": "partial",
            "method": dividend_method,
            "reason": "missing_dividend_per_share_years",
            "dividend_years": sorted(dividend_facts_by_year),
            "missing_years": dividend_missing_years,
            "quality_flags": [
                "source_backed_dividend",
                *[f"missing_dividend_source_{year}" for year in dividend_missing_years],
            ],
        }
    else:
        dividend_status = {
            "status": "blocked",
            "reason": "missing_source_backed_dividend_per_share",
            "quality_flags": ["missing_dividend_source"],
        }
    full_coverage_ready = bool(valuation_points) and not missing_years and not missing_metric_years
    coverage_status = (
        "complete"
        if full_coverage_ready
        else "partial_source_backed"
        if valuation_points
        else "blocked"
    )
    status = "ok" if valuation_points else "missing"
    if eps_facts_by_year and not missing_metric_years:
        metric_status = {
            "status": "ok",
            "method": "S3_MARKET_STANDARD_KR",
            "financial_source_count": financial_source_count,
            "financial_years": sorted(financial_metric_years),
            "quality_flags": ["source_backed_financial_metric"],
        }
    elif eps_facts_by_year:
        metric_status = {
            "status": "partial",
            "method": "S3_MARKET_STANDARD_KR",
            "reason": "missing_open_dart_metric_years",
            "missing_years": missing_metric_years,
            "financial_source_count": financial_source_count,
            "financial_years": sorted(financial_metric_years),
            "quality_flags": [
                "source_backed_financial_metric",
                *[f"missing_financial_metric_{year}" for year in missing_metric_years],
            ],
        }
    else:
        metric_status = {
            "status": "blocked",
            "reason": "missing_open_dart_metric_values",
            "required_metrics": [
                "adjusted_operating_eps",
                "diluted_eps",
                "revenue_per_share",
                "free_cash_flow_per_share",
            ],
            "quality_flags": ["missing_metric_source"],
        }
    valuation_ready = bool(valuation_points)
    coverage_quality_flags = []
    if valuation_points and not full_coverage_ready:
        coverage_quality_flags.append("partial_valuation_coverage")
    coverage_quality_flags.extend(f"missing_market_input_{year}" for year in missing_years)
    coverage_quality_flags.extend(
        f"missing_financial_metric_{year}" for year in missing_metric_years
    )
    output_payload = {
        "ticker": normalized_ticker,
        "entity_id": entity_id,
        "market": "KR",
        "status": status,
        "valuation_ready": valuation_ready,
        "full_coverage_ready": full_coverage_ready,
        "coverage_status": coverage_status,
        "data_mode": "source_backed_raw_valuation_inputs",
        "years": {"start": start_year, "end": end_year},
        "coverage_years": {
            "price": price_years,
            "dividend": sorted(dividend_facts_by_year),
            "market_structure": market_structure_years,
            "financial_metric": sorted(financial_metric_years),
            "valuation_points": [point["fiscal_year"] for point in valuation_points],
        },
        "missing_years": {
            "market_input": missing_years,
            "financial_metric": missing_metric_years,
        },
        "market_gap_diagnostics": market_gap_diagnostics,
        "financial_gap_diagnostics": financial_gap_diagnostics,
        "metric_status": metric_status,
        "normalized_facts": normalized_facts,
        "valuation_points": valuation_points,
        "dividend_status": dividend_status,
        "quality_flags": list(
            dict.fromkeys(
                metric_status["quality_flags"]
                + dividend_status["quality_flags"]
                + coverage_quality_flags
            )
        ),
        "generated_at": datetime.now(UTC).isoformat(),
    }
    output_path = out_dir / f"{normalized_ticker.replace('.', '_')}-{start_year}-{end_year}-valuation-inputs.json"
    output_path.write_text(json.dumps(output_payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return {
        "ticker": normalized_ticker,
        "status": status,
        "valuation_ready": valuation_ready,
        "full_coverage_ready": full_coverage_ready,
        "coverage_status": coverage_status,
        "output_path": str(output_path),
        "normalized_fact_count": len(normalized_facts),
        "price_years": price_years,
        "dividend_years": sorted(dividend_facts_by_year),
        "market_structure_years": market_structure_years,
        "valuation_years": [point["fiscal_year"] for point in valuation_points],
        "missing_years": output_payload["missing_years"],
        "market_gap_diagnostics": market_gap_diagnostics,
        "financial_gap_diagnostics": financial_gap_diagnostics,
        "valuation_point_count": len(valuation_points),
        "metric_status": metric_status,
        "dividend_status": dividend_status,
        "quality_flags": output_payload["quality_flags"],
    }


def _raw_opendart_documents(
    raw_root: Path,
    normalized_ticker: str,
    start_year: int,
    end_year: int,
) -> list[ConnectorDocument]:
    documents: list[ConnectorDocument] = []
    for path in _raw_files(raw_root / "opendart" / normalized_ticker, "*.json"):
        document = _raw_connector_document(path, "opendart", normalized_ticker, "application/json")
        payload = _json_payload_from_bytes(document.payload)
        fiscal_year = _opendart_year_from_payload_or_path(payload, path)
        if fiscal_year is None or not (start_year <= fiscal_year <= end_year):
            continue
        metadata = dict(document.metadata) | {
            "bsns_year": fiscal_year,
            "status": payload.get("status"),
            "message": payload.get("message"),
        }
        documents.append(
            ConnectorDocument(
                source=document.source,
                market=document.market,
                ticker=document.ticker,
                identifier=document.identifier,
                url=document.url,
                payload=document.payload,
                content_type=document.content_type,
                metadata=metadata,
            )
        )
    return documents


def _raw_opendart_dividend_documents(
    raw_root: Path,
    normalized_ticker: str,
    start_year: int,
    end_year: int,
) -> list[ConnectorDocument]:
    documents: list[ConnectorDocument] = []
    for path in _raw_files(raw_root / "opendart_dividends" / normalized_ticker, "*.json"):
        document = _raw_connector_document(
            path,
            "opendart_dividends",
            normalized_ticker,
            "application/json",
        )
        payload = _json_payload_from_bytes(document.payload)
        fiscal_year = _opendart_year_from_payload_or_path(payload, path)
        if fiscal_year is None or not (start_year <= fiscal_year <= end_year):
            continue
        metadata = dict(document.metadata) | {
            "bsns_year": fiscal_year,
            "status": payload.get("status"),
            "message": payload.get("message"),
            "endpoint": "alotMatter",
        }
        documents.append(
            ConnectorDocument(
                source=document.source,
                market=document.market,
                ticker=document.ticker,
                identifier=document.identifier,
                url=document.url,
                payload=document.payload,
                content_type=document.content_type,
                metadata=metadata,
            )
        )
    return documents


def _kr_market_gap_diagnostics(
    start_year: int,
    end_year: int,
    pykrx_rows_by_year: dict[int, tuple[dict[str, Any], ConnectorDocument]],
    marcap_rows_by_year: dict[int, tuple[dict[str, Any], ConnectorDocument]],
    price_years: list[int],
    market_structure_years: list[int],
) -> list[dict[str, Any]]:
    price_year_set = set(price_years)
    market_structure_year_set = set(market_structure_years)
    first_available_date = _first_market_row_date(pykrx_rows_by_year, marcap_rows_by_year)
    diagnostics: list[dict[str, Any]] = []

    for fiscal_year in range(start_year, end_year + 1):
        missing_price = fiscal_year not in price_year_set
        missing_market_structure = fiscal_year not in market_structure_year_set
        if not missing_price and not missing_market_structure:
            continue

        pykrx_tuple = pykrx_rows_by_year.get(fiscal_year)
        marcap_tuple = marcap_rows_by_year.get(fiscal_year)
        pykrx_document = pykrx_tuple[1] if pykrx_tuple else None
        marcap_document = marcap_tuple[1] if marcap_tuple else None

        if (
            missing_price
            and missing_market_structure
            and pykrx_tuple is None
            and marcap_tuple is None
            and first_available_date is not None
            and fiscal_year < first_available_date.year
        ):
            status = "source_no_rows_before_first_trade"
            reason = (
                "No pykrx or marcap rows exist for this ticker before the first cached market row "
                f"{first_available_date.isoformat()}."
            )
            next_action = "keep_partial_market_history_start"
        elif pykrx_tuple is None and marcap_tuple is None:
            status = "missing_raw"
            reason = "No pykrx or marcap raw rows were found for this fiscal year."
            next_action = "collect_kr_market_raw"
        elif missing_market_structure and marcap_tuple is None:
            status = "missing_market_structure_raw"
            reason = "Price evidence exists, but FinanceData marcap market structure row is missing."
            next_action = "collect_kr_market_structure_raw"
        elif missing_market_structure:
            status = "market_structure_value_gap"
            reason = "FinanceData marcap row exists, but market cap or listed shares could not be normalized."
            next_action = "inspect_marcap_market_structure"
        elif missing_price:
            status = "missing_price_raw"
            reason = "Market structure evidence exists, but no year-end close price was normalized."
            next_action = "collect_kr_market_raw"
        else:
            status = "unknown"
            reason = "Market input gap could not be classified."
            next_action = "inspect_raw_kr"

        diagnostics.append(
            {
                "fiscal_year": fiscal_year,
                "status": status,
                "reason": reason,
                "next_action": next_action,
                "missing_price": missing_price,
                "missing_market_structure": missing_market_structure,
                "first_available_market_date": first_available_date.isoformat()
                if first_available_date
                else None,
                "pykrx_source_document_id": _raw_source_document_id(pykrx_document),
                "marcap_source_document_id": _raw_source_document_id(marcap_document),
            }
        )
    return diagnostics


def _first_market_row_date(
    pykrx_rows_by_year: dict[int, tuple[dict[str, Any], ConnectorDocument]],
    marcap_rows_by_year: dict[int, tuple[dict[str, Any], ConnectorDocument]],
) -> date | None:
    dates: list[date] = []
    for row, _document in pykrx_rows_by_year.values():
        row_date = _date_or_none(_first_present(row, "date", "Date"))
        if row_date:
            dates.append(row_date)
    for row, _document in marcap_rows_by_year.values():
        row_date = _date_or_none(_first_present(row, "Date", "date"))
        if row_date:
            dates.append(row_date)
    return min(dates) if dates else None


def _raw_source_document_id(document: ConnectorDocument | None) -> str | None:
    if document is None:
        return None
    content_hash = document.metadata.get("content_hash")
    return f"raw:{document.source}:{content_hash}" if content_hash else None


def _kr_financial_gap_diagnostics(
    opendart_documents: list[ConnectorDocument],
    start_year: int,
    end_year: int,
    eps_facts_by_year: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    documents_by_year: dict[int, ConnectorDocument] = {}
    for document in opendart_documents:
        fiscal_year = _int_or_none(document.metadata.get("bsns_year"))
        if fiscal_year is not None:
            documents_by_year[fiscal_year] = document

    diagnostics: list[dict[str, Any]] = []
    for fiscal_year in range(start_year, end_year + 1):
        if fiscal_year in eps_facts_by_year:
            continue
        document = documents_by_year.get(fiscal_year)
        if document is None:
            diagnostics.append(
                {
                    "fiscal_year": fiscal_year,
                    "status": "missing_raw",
                    "reason": "No append-only OpenDART raw JSON exists for this requested year.",
                    "next_action": "collect_opendart_kr",
                }
            )
            continue

        payload = _json_payload_from_bytes(document.payload)
        rows = payload.get("list") if isinstance(payload.get("list"), list) else []
        source_status = str(payload.get("status") or document.metadata.get("status") or "")
        source_message = str(payload.get("message") or document.metadata.get("message") or "")
        source_summary = {
            "fiscal_year": fiscal_year,
            "source_document_id": f"raw:{document.source}:{document.metadata.get('content_hash')}",
            "filing_id": document.identifier,
            "opendart_status": source_status or None,
            "opendart_message": source_message or None,
            "row_count": len(rows),
        }
        if source_status and source_status != "000":
            diagnostics.append(
                source_summary
                | {
                    "status": "source_no_data",
                    "reason": "OpenDART returned a non-success status for this annual filing request.",
                    "next_action": "keep_partial_or_add_alternate_source",
                }
            )
        elif not rows:
            diagnostics.append(
                source_summary
                | {
                    "status": "source_no_data",
                    "reason": "OpenDART raw JSON is present but contains no financial statement rows.",
                    "next_action": "keep_partial_or_add_alternate_source",
                }
            )
        else:
            diagnostics.append(
                source_summary
                | {
                    "status": "parser_metric_gap",
                    "reason": "OpenDART rows exist but no EPS metric normalized from account_id/account_nm.",
                    "next_action": "inspect_opendart_metric_mapping",
                }
            )
    return diagnostics


def _json_payload_from_bytes(payload: bytes) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _opendart_year_from_payload_or_path(payload: dict[str, Any], path: Path) -> int | None:
    for candidate in (
        payload.get("bsns_year"),
        payload.get("year"),
        *[
            row.get("bsns_year")
            for row in payload.get("list", [])
            if isinstance(row, dict)
        ],
    ):
        fiscal_year = _int_or_none(candidate)
        if fiscal_year is not None:
            return fiscal_year
    match = re.search(r"(20\d{2})", _raw_identifier(path))
    return int(match.group(1)) if match else None


def _kr_financial_fact_source_trace(
    document: ConnectorDocument,
    *,
    ticker: str,
    metric: str,
    fiscal_year: int,
    unit: str,
    method: str,
    formula: str,
    confidence: Decimal,
    quality_flags: list[str],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    trace = {
        "source": document.source,
        "source_type": document.source,
        "source_document_id": f"raw:{document.source}:{document.metadata.get('content_hash')}",
        "filing_id": document.identifier,
        "form": "opendart_fnlttSinglAcntAll",
        "period": f"FY{fiscal_year}",
        "fiscal_year": fiscal_year,
        "fiscal_period": "FY",
        "period_start": date(fiscal_year, 1, 1).isoformat(),
        "period_end": date(fiscal_year, 12, 31).isoformat(),
        "available_at": f"{fiscal_year + 1}-04-01T00:00:00+09:00",
        "unit": unit,
        "currency": "KRW",
        "method": method,
        "formula": formula,
        "confidence": str(confidence),
        "quality_status": "source_backed",
        "quality_flags": quality_flags,
        "source_url": document.url,
        "table_hash": document.metadata.get("content_hash"),
        "row_hash": _stable_hash({"metric": metric, "evidence": evidence}),
        "version": 1,
        "metadata": {
            "ticker": ticker,
            "metric": metric,
            "raw_identifier": document.identifier,
            "opendart_status": document.metadata.get("status"),
            "opendart_message": document.metadata.get("message"),
        },
    }
    storage_trace = SourceTrace(**trace)
    storage_trace.assert_storage_ready()
    return storage_trace.model_dump(mode="json")


def _kr_source_backed_valuation_points(
    ticker: str,
    entity_id: str,
    price_facts_by_year: dict[int, dict[str, Any]],
    eps_facts_by_year: dict[int, dict[str, Any]],
    dividend_facts_by_year: dict[int, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    dividend_facts_by_year = dividend_facts_by_year or {}
    points: list[dict[str, Any]] = []
    for fiscal_year in sorted(set(price_facts_by_year) & set(eps_facts_by_year)):
        price_fact = price_facts_by_year[fiscal_year]
        eps_fact = eps_facts_by_year[fiscal_year]
        dividend_fact = dividend_facts_by_year.get(fiscal_year)
        input_fact_ids = [price_fact["fact_id"], eps_fact["fact_id"]]
        confidence_inputs = [
            SourceTrace(**price_fact["source_trace"]).confidence,
            SourceTrace(**eps_fact["source_trace"]).confidence,
        ]
        quality_flags = ["source_backed_valuation_input"]
        source_trace_metadata: dict[str, Any] = {
            "price_source_trace": price_fact["source_trace"],
            "metric_source_trace": eps_fact["source_trace"],
        }
        if dividend_fact is not None:
            input_fact_ids.append(dividend_fact["fact_id"])
            confidence_inputs.append(SourceTrace(**dividend_fact["source_trace"]).confidence)
            quality_flags.append("source_backed_dividend")
            source_trace_metadata["dividend_source_trace"] = dividend_fact["source_trace"]
        else:
            quality_flags.append("missing_dividend_source")
        trace = SourceTrace(
            source="derived",
            source_type="derived_valuation_input",
            source_document_id=f"derived:kr:{ticker}:{fiscal_year}:valuation-input",
            filing_id=f"KR_VALUATION_INPUT_{ticker}_{fiscal_year}",
            form="derived_valuation_input",
            period=f"FY{fiscal_year}",
            fiscal_year=fiscal_year,
            fiscal_period="FY",
            period_start=date(fiscal_year, 1, 1),
            period_end=date(fiscal_year, 12, 31),
            available_at=datetime(fiscal_year + 1, 4, 1, tzinfo=UTC),
            unit="KRW/share",
            currency="KRW",
            method="KR_SOURCE_BACKED_PRICE_EPS_JOIN",
            formula=(
                "valuation_input = source-backed year-end close price joined to "
                "OpenDART S3 adjusted operating EPS; dividend_per_share included when "
                "a source-backed pykrx or OpenDART DPS fact exists"
            ),
            input_fact_ids=input_fact_ids,
            confidence=min(confidence_inputs),
            quality_flags=quality_flags,
            quality_status="source_backed",
            version=1,
            metadata=source_trace_metadata,
        )
        trace.assert_storage_ready()
        point = {
            "valuation_point_id": f"valuation:kr:{ticker}:{fiscal_year}:adjusted_operating_eps",
            "entity_id": entity_id,
            "ticker": ticker,
            "fiscal_year": fiscal_year,
            "period": f"FY{fiscal_year}",
            "metric": "adjusted_operating_eps",
            "metric_value": eps_fact["value"],
            "price": price_fact["value"],
            "currency": "KRW",
            "source_trace": trace.model_dump(mode="json"),
            "quality_flags": trace.quality_flags,
        }
        if dividend_fact is not None:
            point["dividend"] = dividend_fact["value"]
        points.append(point)
    return points


def _kr_market_fact_source_trace(
    document: ConnectorDocument,
    *,
    ticker: str,
    metric: str,
    fiscal_year: int,
    trade_date: date,
    unit: str,
    method: str,
    formula: str,
    row: dict[str, Any],
    confidence: Decimal,
    quality_flags: list[str],
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    trace = {
        "source": document.source,
        "source_type": document.source,
        "source_document_id": f"raw:{document.source}:{document.metadata.get('content_hash')}",
        "filing_id": document.identifier,
        "form": "raw_market_file",
        "period": trade_date.isoformat(),
        "fiscal_year": fiscal_year,
        "fiscal_period": "FY",
        "period_start": date(fiscal_year, 1, 1).isoformat(),
        "period_end": date(fiscal_year, 12, 31).isoformat(),
        "available_at": f"{trade_date.isoformat()}T00:00:00+09:00",
        "unit": unit,
        "currency": "KRW" if unit != "shares" else "KRW",
        "method": method,
        "formula": formula,
        "confidence": str(confidence),
        "quality_status": "source_backed",
        "quality_flags": quality_flags,
        "source_url": document.url,
        "table_hash": document.metadata.get("content_hash"),
        "row_hash": _stable_hash(row),
        "version": 1,
        "metadata": {
            "ticker": ticker,
            "metric": metric,
            "raw_identifier": document.identifier,
        },
    }
    if extra_fields:
        trace.update(extra_fields)
    storage_trace = SourceTrace(**trace)
    storage_trace.assert_storage_ready()
    return storage_trace.model_dump(mode="json")


def _kr_dividend_fact_source_trace(
    document: ConnectorDocument,
    *,
    ticker: str,
    fiscal_year: int,
    unit: str,
    method: str,
    formula: str,
    evidence: dict[str, Any],
    confidence: Decimal,
    quality_flags: list[str],
) -> dict[str, Any]:
    trace = {
        "source": document.source,
        "source_type": document.source,
        "source_document_id": f"raw:{document.source}:{document.metadata.get('content_hash')}",
        "filing_id": document.identifier,
        "form": "opendart_alotMatter",
        "period": f"FY{fiscal_year}",
        "fiscal_year": fiscal_year,
        "fiscal_period": "FY",
        "period_start": date(fiscal_year, 1, 1).isoformat(),
        "period_end": date(fiscal_year, 12, 31).isoformat(),
        "available_at": f"{fiscal_year + 1}-04-01T00:00:00+09:00",
        "unit": unit,
        "currency": "KRW",
        "method": method,
        "formula": formula,
        "confidence": str(confidence),
        "quality_status": "source_backed",
        "quality_flags": quality_flags,
        "source_url": document.url,
        "table_hash": document.metadata.get("content_hash"),
        "row_hash": _stable_hash(evidence),
        "version": 1,
        "metadata": {
            "ticker": ticker,
            "metric": "dividend_per_share",
            "raw_identifier": document.identifier,
            "opendart_status": document.metadata.get("status"),
            "opendart_message": document.metadata.get("message"),
        },
    }
    storage_trace = SourceTrace(**trace)
    storage_trace.assert_storage_ready()
    return storage_trace.model_dump(mode="json")


def _kr_normalized_fact(
    entity_id: str,
    ticker: str,
    fiscal_year: int,
    metric: str,
    value: Decimal,
    unit: str,
    source_trace: dict[str, Any],
) -> dict[str, Any]:
    trace = SourceTrace(**source_trace)
    trace.assert_storage_ready()
    return {
        "fact_id": f"fact:kr:{ticker}:{fiscal_year}:{metric}",
        "entity_id": entity_id,
        "ticker": ticker,
        "metric": metric,
        "period": f"FY{fiscal_year}",
        "fiscal_year": fiscal_year,
        "fiscal_period": "FY",
        "value": _decimal_storage_string(value),
        "unit": unit,
        "currency": "KRW",
        "source_trace": trace.model_dump(mode="json"),
        "version": 1,
    }


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _decimal_storage_string(value: Decimal) -> str:
    if value == value.to_integral_value():
        return str(value.quantize(Decimal("1")))
    return format(value.normalize(), "f")


def _normalized_marcap_market_cap(row: dict[str, Any]) -> dict[str, Any]:
    raw_market_cap = _decimal_from_any(_first_present(row, "Marcap", "marcap"))
    close_price = _decimal_from_any(_first_present(row, "Close", "close"))
    listed_shares = _decimal_from_any(_first_present(row, "Stocks", "stocks"))
    if raw_market_cap is None:
        return {
            "value": None,
            "raw_value": None,
            "raw_unit_detected": None,
            "formula": "FinanceData marcap Marcap value missing",
            "quality_flags": ["missing_market_cap_raw_value"],
        }
    expected_market_cap = (
        close_price * listed_shares
        if close_price is not None and listed_shares is not None and close_price > 0 and listed_shares > 0
        else None
    )
    if expected_market_cap is not None and _decimal_nearly_equal(raw_market_cap, expected_market_cap):
        return {
            "value": raw_market_cap,
            "raw_value": _decimal_storage_string(raw_market_cap),
            "raw_unit_detected": "KRW",
            "formula": (
                "FinanceData marcap raw Marcap value imported as KRW market capitalization; "
                "unit verified against Close * Stocks"
            ),
            "quality_flags": ["marcap_market_cap_raw_krw"],
        }
    converted_from_millions = raw_market_cap * Decimal("1000000")
    if expected_market_cap is not None and _decimal_nearly_equal(converted_from_millions, expected_market_cap):
        return {
            "value": converted_from_millions,
            "raw_value": _decimal_storage_string(raw_market_cap),
            "raw_unit_detected": "KRW_millions",
            "formula": (
                "FinanceData marcap raw Marcap value converted from KRW millions to KRW; "
                "unit verified against Close * Stocks"
            ),
            "quality_flags": ["marcap_market_cap_converted_from_krw_millions"],
        }
    return {
        "value": raw_market_cap,
        "raw_value": _decimal_storage_string(raw_market_cap),
        "raw_unit_detected": "unverified",
        "formula": (
            "FinanceData marcap raw Marcap value imported as market capitalization; "
            "unit could not be cross-validated against Close * Stocks"
        ),
        "quality_flags": ["marcap_market_cap_unit_unverified"],
    }


def _decimal_nearly_equal(
    left: Decimal,
    right: Decimal,
    *,
    relative_tolerance: Decimal = Decimal("0.02"),
) -> bool:
    if right == 0:
        return left == 0
    return abs(left - right) / abs(right) <= relative_tolerance


def _inspect_raw_kr_ticker(
    ticker: str,
    start_year: int,
    end_year: int,
    raw_root: Path,
    *,
    require_opendart: bool = False,
) -> dict[str, Any]:
    normalized_ticker = ticker.upper()
    expected_years = set(range(start_year, end_year + 1))
    krx_code = _marcap_code_from_ticker(normalized_ticker)
    pykrx_files = _raw_files(raw_root / "pykrx" / normalized_ticker, "*ohlcv*.csv")
    pykrx_fundamental_files = _raw_files(
        raw_root / "pykrx" / normalized_ticker,
        "*fundamental*.csv",
    )
    marcap_files = _latest_raw_files_by_year(
        raw_root / "marcap" / "KR_MARKET",
        "marcap-*.parquet",
        start_year,
        end_year,
    )

    pykrx_documents = [
        _raw_connector_document(path, "pykrx", normalized_ticker, "text/csv")
        for path in pykrx_files
    ]
    pykrx_fundamental_documents = [
        _raw_connector_document(path, "pykrx", normalized_ticker, "text/csv")
        for path in pykrx_fundamental_files
    ]
    marcap_documents = [
        _raw_connector_document(path, "marcap", "KR_MARKET", "application/vnd.apache.parquet")
        for path in marcap_files
    ]
    opendart_documents = _raw_opendart_documents(
        raw_root,
        normalized_ticker,
        start_year,
        end_year,
    )
    opendart_dividend_documents = _raw_opendart_dividend_documents(
        raw_root,
        normalized_ticker,
        start_year,
        end_year,
    )

    pykrx_rows: list[dict[str, str]] = []
    pykrx_rows_by_year: dict[int, tuple[dict[str, Any], ConnectorDocument]] = {}
    pykrx_sources: list[dict[str, Any]] = []
    for document in pykrx_documents:
        rows = [
            row for row in _pykrx_csv_rows(document)
            if start_year <= date.fromisoformat(row["date"]).year <= end_year
        ]
        if not rows:
            continue
        pykrx_rows.extend(rows)
        for row in rows:
            row_date = date.fromisoformat(row["date"])
            current = pykrx_rows_by_year.get(row_date.year)
            if current is None or row_date > date.fromisoformat(current[0]["date"]):
                pykrx_rows_by_year[row_date.year] = (row, document)
        pykrx_sources.append(_raw_source_summary(document, rows, "date"))

    pykrx_fundamental_rows: list[dict[str, str]] = []
    pykrx_fundamental_sources: list[dict[str, Any]] = []
    for document in pykrx_fundamental_documents:
        rows = [
            row for row in _pykrx_fundamental_csv_rows(document)
            if start_year <= date.fromisoformat(row["date"]).year <= end_year
        ]
        if not rows:
            continue
        pykrx_fundamental_rows.extend(rows)
        pykrx_fundamental_sources.append(_raw_source_summary(document, rows, "date"))

    marcap_rows: list[dict[str, Any]] = []
    marcap_rows_by_year: dict[int, tuple[dict[str, Any], ConnectorDocument]] = {}
    marcap_sources: list[dict[str, Any]] = []
    for document in marcap_documents:
        rows = [
            row for row in _marcap_rows(document, {krx_code})
            if (row_date := _date_or_none(_first_present(row, "Date", "date")))
            and start_year <= row_date.year <= end_year
        ]
        if not rows:
            continue
        marcap_rows.extend(rows)
        for row in rows:
            row_date = _date_or_none(_first_present(row, "Date", "date"))
            if row_date is None:
                continue
            current = marcap_rows_by_year.get(row_date.year)
            current_date = (
                _date_or_none(_first_present(current[0], "Date", "date"))
                if current
                else None
            )
            if current_date is None or row_date > current_date:
                marcap_rows_by_year[row_date.year] = (row, document)
        marcap_sources.append(_raw_source_summary(document, rows, "Date"))

    pykrx_dates = [date.fromisoformat(row["date"]) for row in pykrx_rows]
    marcap_dates = [
        row_date for row in marcap_rows
        if (row_date := _date_or_none(_first_present(row, "Date", "date")))
    ]
    market_cap_rows = [
        row for row in marcap_rows
        if _first_present(row, "Marcap", "marcap") not in {None, "", 0}
    ]
    listed_shares_rows = [
        row for row in marcap_rows
        if _first_present(row, "Stocks", "stocks") not in {None, "", 0}
    ]
    pykrx_fundamental_dates = [
        date.fromisoformat(row["date"])
        for row in pykrx_fundamental_rows
        if _date_or_none(row["date"])
    ]
    pykrx_dividend_years = {
        row_date.year
        for row in pykrx_fundamental_rows
        if (row_date := _date_or_none(row["date"]))
        and _decimal_from_any(row.get("dps")) is not None
    }
    dividend_years = set(pykrx_dividend_years)
    pykrx_years = {row_date.year for row_date in pykrx_dates}
    marcap_years = {row_date.year for row_date in marcap_dates}
    market_cap_years = {
        row_date.year
        for row in market_cap_rows
        if (row_date := _date_or_none(_first_present(row, "Date", "date")))
    }
    listed_shares_years = {
        row_date.year
        for row in listed_shares_rows
        if (row_date := _date_or_none(_first_present(row, "Date", "date")))
    }
    opendart_sources: list[dict[str, Any]] = []
    opendart_metric_years: set[int] = set()
    opendart_eps_years: set[int] = set()
    opendart_errors: list[str] = []
    entity_id = f"kr:{normalized_ticker}"
    for document in opendart_documents:
        source_summary = _raw_opendart_source_summary(document)
        try:
            result = normalize_market_standard_document(document, entity_id, "KRW")
        except Exception as exc:  # pragma: no cover - defensive inspection path
            opendart_errors.append(f"{document.identifier}: {type(exc).__name__}: {exc}")
            result = None
        if result is not None:
            metric_years = {
                metric.fiscal_year
                for metric in result.metrics
                if start_year <= metric.fiscal_year <= end_year
            }
            current_doc_eps_years: set[int] = set()
            opendart_metric_years.update(metric_years)
            if (
                result.adjusted_record is not None
                and result.adjusted_record.adjusted_eps is not None
                and start_year <= result.adjusted_record.fiscal_year <= end_year
            ):
                current_doc_eps_years.add(result.adjusted_record.fiscal_year)
                opendart_eps_years.update(current_doc_eps_years)
            source_summary["normalized_metric_years"] = sorted(metric_years)
            source_summary["has_adjusted_operating_eps"] = bool(current_doc_eps_years)
        opendart_sources.append(source_summary)

    opendart_dividend_sources: list[dict[str, Any]] = []
    opendart_dividend_years: set[int] = set()
    for document in opendart_dividend_documents:
        dividend_per_share, _evidence = _opendart_dividend_per_share(document)
        fiscal_year = _int_or_none(document.metadata.get("bsns_year"))
        source_summary = _raw_opendart_source_summary(document)
        source_summary["dividend_row_count"] = _opendart_dividend_row_count(document)
        source_summary["has_dividend_per_share"] = dividend_per_share is not None
        if (
            dividend_per_share is not None
            and fiscal_year is not None
            and start_year <= fiscal_year <= end_year
        ):
            opendart_dividend_years.add(fiscal_year)
        opendart_dividend_sources.append(source_summary)
    dividend_years.update(opendart_dividend_years)

    missing_pykrx_years = sorted(expected_years - pykrx_years)
    missing_pykrx_dividend_years = sorted(expected_years - pykrx_dividend_years)
    missing_opendart_dividend_years = sorted(expected_years - opendart_dividend_years)
    missing_dividend_years = sorted(expected_years - dividend_years)
    missing_marcap_years = sorted(expected_years - marcap_years)
    missing_market_cap_years = sorted(expected_years - market_cap_years)
    missing_listed_shares_years = sorted(expected_years - listed_shares_years)
    missing_opendart_metric_years = sorted(expected_years - opendart_metric_years)
    missing_opendart_eps_years = sorted(expected_years - opendart_eps_years)
    price_years = sorted(pykrx_years | marcap_years)
    market_structure_years = sorted(market_cap_years & listed_shares_years)
    valuation_years = sorted(set(price_years) & set(market_structure_years) & opendart_eps_years)
    market_gap_diagnostics = _kr_market_gap_diagnostics(
        start_year,
        end_year,
        pykrx_rows_by_year,
        marcap_rows_by_year,
        price_years,
        market_structure_years,
    )
    financial_gap_diagnostics = _kr_financial_gap_diagnostics(
        opendart_documents,
        start_year,
        end_year,
        {year: {"fiscal_year": year} for year in opendart_eps_years},
    )
    full_coverage_ready = bool(valuation_years) and set(valuation_years) == expected_years
    acceptable_partial_coverage = bool(valuation_years) and all(
        gap.get("status") == "source_no_rows_before_first_trade"
        for gap in market_gap_diagnostics
    ) and all(
        gap.get("status") == "source_no_data"
        for gap in financial_gap_diagnostics
    )
    valuation_ready = full_coverage_ready or acceptable_partial_coverage
    coverage_status = (
        "complete"
        if full_coverage_ready
        else "partial_source_backed"
        if valuation_ready
        else "blocked"
    )
    checks = [
        _raw_check("pykrx_raw_file", bool(pykrx_files), "pykrx OHLCV raw CSV exists"),
        _raw_check("pykrx_rows", bool(pykrx_rows), "pykrx rows exist inside requested year range"),
        _raw_check(
            "pykrx_year_coverage",
            not missing_pykrx_years,
            f"pykrx rows cover every requested fiscal year; missing={missing_pykrx_years}",
        ),
        _raw_check("marcap_raw_file", bool(marcap_files), "FinanceData marcap raw parquet exists"),
        _raw_check("marcap_rows", bool(marcap_rows), "marcap rows exist for the requested ticker"),
        _raw_check(
            "marcap_year_coverage",
            not missing_marcap_years,
            f"marcap rows cover every requested fiscal year; missing={missing_marcap_years}",
        ),
        _raw_check("market_cap_evidence", bool(market_cap_rows), "marcap rows include market cap evidence"),
        _raw_check(
            "market_cap_year_coverage",
            not missing_market_cap_years,
            f"market cap evidence covers every requested fiscal year; missing={missing_market_cap_years}",
        ),
        _raw_check("listed_shares_evidence", bool(listed_shares_rows), "marcap rows include listed shares evidence"),
        _raw_check(
            "listed_shares_year_coverage",
            not missing_listed_shares_years,
            f"listed shares evidence covers every requested fiscal year; missing={missing_listed_shares_years}",
        ),
        _raw_check("pykrx_fundamental_raw_file", bool(pykrx_fundamental_files), "pykrx fundamental raw CSV exists", required=False),
        _raw_check("pykrx_dividend_rows", bool(pykrx_dividend_years), "pykrx fundamental rows include DPS evidence", required=False),
        _raw_check(
            "pykrx_dividend_year_coverage",
            not missing_pykrx_dividend_years,
            f"pykrx DPS evidence covers every requested fiscal year; missing={missing_pykrx_dividend_years}",
            required=False,
        ),
        _raw_check("opendart_dividend_raw_file", bool(opendart_dividend_documents), "OpenDART alotMatter raw dividend JSON exists", required=False),
        _raw_check("opendart_dividend_rows", bool(opendart_dividend_years), "OpenDART alotMatter rows include cash dividend-per-share evidence", required=False),
        _raw_check(
            "opendart_dividend_year_coverage",
            not missing_opendart_dividend_years,
            f"OpenDART DPS evidence covers every requested fiscal year; missing={missing_opendart_dividend_years}",
            required=False,
        ),
        _raw_check(
            "dividend_year_coverage",
            not missing_dividend_years,
            f"Any source-backed DPS evidence covers every requested fiscal year; missing={missing_dividend_years}",
            required=False,
        ),
        _raw_check("opendart_raw_file", bool(opendart_documents), "OpenDART annual financial statement raw JSON exists", required=require_opendart),
        _raw_check("opendart_metric_rows", bool(opendart_metric_years), "OpenDART raw JSON normalizes to financial metric rows", required=require_opendart),
        _raw_check(
            "opendart_metric_year_coverage",
            not missing_opendart_metric_years,
            f"OpenDART metric rows cover every requested fiscal year; missing={missing_opendart_metric_years}",
            required=require_opendart,
        ),
        _raw_check("opendart_adjusted_operating_eps", bool(opendart_eps_years), "OpenDART raw JSON provides reported EPS for KR valuation metric", required=require_opendart),
        _raw_check(
            "opendart_eps_year_coverage",
            not missing_opendart_eps_years,
            f"OpenDART reported EPS covers every requested fiscal year; missing={missing_opendart_eps_years}",
            required=require_opendart,
        ),
    ]
    required_checks = [check for check in checks if check.get("required", True)]
    status = "ok" if valuation_ready or all(check["ok"] for check in required_checks) else "missing"
    quality_flags = []
    for check in checks:
        if check["ok"]:
            continue
        check_name = str(check["name"])
        if (
            not missing_dividend_years
            and check_name
            in {
                "pykrx_fundamental_raw_file",
                "pykrx_dividend_rows",
                "pykrx_dividend_year_coverage",
                "opendart_dividend_raw_file",
                "opendart_dividend_rows",
                "opendart_dividend_year_coverage",
            }
        ):
            continue
        quality_flags.append(f"missing_{check_name}")
    if coverage_status == "partial_source_backed":
        quality_flags.append("partial_valuation_coverage")
    if opendart_errors:
        quality_flags.append("opendart_parse_error")
    source_trace = {
        "source_type": "raw_kr_market_evidence",
        "source_document_id": f"raw:kr:{normalized_ticker}:{start_year}-{end_year}",
        "filing_id": f"KR_RAW_MARKET_{normalized_ticker}_{start_year}_{end_year}",
        "period": f"{start_year}:{end_year}",
        "unit": "raw_rows",
        "currency": "KRW",
        "formula": (
            "Inspect cached pykrx OHLCV CSV, FinanceData marcap parquet, and OpenDART raw JSON files; "
            "no valuation metric is computed by this command"
        ),
        "method": "RAW_KR_MARKET_EVIDENCE_INSPECTION",
        "quality_status": "raw_evidence_available" if status == "ok" else "raw_evidence_incomplete",
        "quality_flags": quality_flags,
        "input_sources": {
            "pykrx": pykrx_sources,
            "pykrx_fundamentals": pykrx_fundamental_sources,
            "marcap": marcap_sources,
            "opendart": opendart_sources,
            "opendart_dividends": opendart_dividend_sources,
        },
    }
    return {
        "ticker": normalized_ticker,
        "status": status,
        "valuation_ready": valuation_ready,
        "full_coverage_ready": full_coverage_ready,
        "coverage_status": coverage_status,
        "valuation_years": valuation_years,
        "require_opendart": require_opendart,
        "krx_code": krx_code,
        "source_trace": source_trace,
        "checks": checks,
        "market_gap_diagnostics": market_gap_diagnostics,
        "financial_gap_diagnostics": financial_gap_diagnostics,
        "coverage_years": {
            "expected": sorted(expected_years),
            "price": price_years,
            "market_structure": market_structure_years,
            "valuation_points": valuation_years,
            "pykrx": sorted(pykrx_years),
            "pykrx_dividend": sorted(pykrx_dividend_years),
            "opendart_dividend": sorted(opendart_dividend_years),
            "dividend": sorted(dividend_years),
            "marcap": sorted(marcap_years),
            "market_cap": sorted(market_cap_years),
            "listed_shares": sorted(listed_shares_years),
            "opendart_metrics": sorted(opendart_metric_years),
            "opendart_eps": sorted(opendart_eps_years),
        },
        "missing_years": {
            "pykrx": missing_pykrx_years,
            "pykrx_dividend": missing_pykrx_dividend_years,
            "opendart_dividend": missing_opendart_dividend_years,
            "dividend": missing_dividend_years,
            "marcap": missing_marcap_years,
            "market_cap": missing_market_cap_years,
            "listed_shares": missing_listed_shares_years,
            "opendart_metrics": missing_opendart_metric_years,
            "opendart_eps": missing_opendart_eps_years,
        },
        "pykrx": {
            "files": pykrx_sources,
            "row_count": len(pykrx_rows),
            "date_range": _date_range(pykrx_dates),
        },
        "pykrx_fundamentals": {
            "files": pykrx_fundamental_sources,
            "row_count": len(pykrx_fundamental_rows),
            "date_range": _date_range(pykrx_fundamental_dates),
            "dividend_years": sorted(pykrx_dividend_years),
        },
        "marcap": {
            "files": marcap_sources,
            "row_count": len(marcap_rows),
            "date_range": _date_range(marcap_dates),
            "market_cap_rows": len(market_cap_rows),
            "listed_shares_rows": len(listed_shares_rows),
        },
        "opendart": {
            "files": opendart_sources,
            "metric_years": sorted(opendart_metric_years),
            "eps_years": sorted(opendart_eps_years),
            "parse_errors": opendart_errors,
        },
        "opendart_dividends": {
            "files": opendart_dividend_sources,
            "dividend_years": sorted(opendart_dividend_years),
        },
    }


def _raw_files(directory: Path, pattern: str) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        [path for path in directory.glob(pattern) if path.is_file()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def _latest_raw_files_by_year(
    directory: Path,
    pattern: str,
    start_year: int,
    end_year: int,
) -> list[Path]:
    files = _raw_files(directory, pattern)
    selected: list[Path] = []
    for year in range(start_year, end_year + 1):
        matches = [
            path for path in files
            if f"-{year}-" in path.name or path.name.startswith(f"marcap-{year}-")
        ]
        if matches:
            selected.append(matches[0])
    return selected


def _raw_connector_document(
    path: Path,
    source: str,
    ticker: str,
    content_type: str,
) -> ConnectorDocument:
    return ConnectorDocument(
        source=source,
        market="KR",
        ticker=ticker,
        identifier=_raw_identifier(path),
        url=path.as_posix(),
        payload=path.read_bytes(),
        content_type=content_type,
        metadata={"local_path": str(path), "content_hash": _hash_file(path)},
    )


def _raw_identifier(path: Path) -> str:
    parts = path.stem.split("-")
    if parts and len(parts[-1]) == 12 and all(char in "0123456789abcdef" for char in parts[-1].lower()):
        return "-".join(parts[:-1])
    return path.stem


def _raw_source_summary(
    document: ConnectorDocument,
    rows: list[dict[str, Any]],
    date_key: str,
) -> dict[str, Any]:
    dates = [
        row_date for row in rows
        if (row_date := _date_or_none(_first_present(row, date_key, date_key.lower())))
    ]
    return {
        "source": document.source,
        "ticker": document.ticker,
        "identifier": document.identifier,
        "local_path": document.metadata.get("local_path"),
        "content_hash": document.metadata.get("content_hash"),
        "row_count": len(rows),
        "date_range": _date_range(dates),
        "source_trace": {
            "source_type": document.source,
            "source_document_id": f"raw:{document.source}:{document.metadata.get('content_hash')}",
            "source_url": document.url,
            "filing_id": document.identifier,
            "period": _date_range_label(dates),
            "unit": "raw_rows",
            "currency": "KRW",
            "formula": "Raw source document loaded from append-only local storage for evidence inspection",
            "method": "RAW_DOCUMENT_HASH_INSPECTION",
            "quality_status": "raw_document_available",
            "content_hash": document.metadata.get("content_hash"),
        },
    }


def _raw_opendart_source_summary(document: ConnectorDocument) -> dict[str, Any]:
    payload = _json_payload_from_bytes(document.payload)
    rows = payload.get("list", [])
    row_count = len(rows) if isinstance(rows, list) else 0
    fiscal_year = _opendart_year_from_payload_or_path(payload, Path(str(document.metadata.get("local_path") or "")))
    return {
        "source": document.source,
        "ticker": document.ticker,
        "identifier": document.identifier,
        "local_path": document.metadata.get("local_path"),
        "content_hash": document.metadata.get("content_hash"),
        "row_count": row_count,
        "fiscal_year": fiscal_year,
        "status": payload.get("status"),
        "message": payload.get("message"),
        "source_trace": {
            "source_type": document.source,
            "source_document_id": f"raw:{document.source}:{document.metadata.get('content_hash')}",
            "source_url": document.url,
            "filing_id": document.identifier,
            "period": f"FY{fiscal_year}" if fiscal_year else "unknown",
            "unit": "raw_rows",
            "currency": "KRW",
            "formula": "Raw OpenDART fnlttSinglAcntAll JSON loaded from append-only local storage for evidence inspection",
            "method": "RAW_DOCUMENT_HASH_INSPECTION",
            "quality_status": "raw_document_available",
            "content_hash": document.metadata.get("content_hash"),
        },
    }


def _raw_check(name: str, ok: bool, detail: str, *, required: bool = True) -> dict[str, Any]:
    return {"name": name, "ok": ok, "required": required, "detail": detail}


def _date_range(dates: list[date]) -> dict[str, str | None]:
    if not dates:
        return {"start": None, "end": None}
    return {"start": min(dates).isoformat(), "end": max(dates).isoformat()}


def _date_range_label(dates: list[date]) -> str:
    values = _date_range(dates)
    if values["start"] and values["end"]:
        return f"{values['start']}:{values['end']}"
    return "unknown"


def collect_research_metadata(
    tickers: str | list[str],
    market: str,
    sources: str | list[str],
    start_year: int,
    end_year: int,
    *,
    persist: bool = False,
    force_refresh: bool = False,
    continue_on_error: bool = False,
) -> dict[str, Any]:
    normalized_market = market.upper()
    if normalized_market != "KR":
        raise ValueError("research metadata collection currently supports KR only")
    requested_tickers = _split_csv(tickers)
    requested_sources = [source.lower() for source in _split_identifiers(sources)]
    documents: list[ConnectorDocument] = []
    failures: list[dict[str, str]] = []
    for source_name in requested_sources:
        try:
            connector = _research_metadata_connector(source_name)
        except Exception as exc:
            failures.append(
                {
                    "source": source_name,
                    "ticker": "*",
                    "error_type": exc.__class__.__name__,
                    "error": str(exc),
                }
            )
            if not continue_on_error:
                raise
            continue
        for ticker in requested_tickers:
            try:
                documents.extend(
                    connector.collect(
                        ConnectorRequest(
                            ticker=ticker,
                            market=normalized_market,
                            start_year=start_year,
                            end_year=end_year,
                            force_refresh=force_refresh,
                        )
                    )
                )
            except Exception as exc:
                failures.append(
                    {
                        "source": connector.source,
                        "ticker": ticker,
                        "error_type": exc.__class__.__name__,
                        "error": str(exc),
                    }
                )
                if not continue_on_error:
                    raise
    persisted = (
        _persist_research_metadata_documents(normalized_market, documents)
        if persist and documents
        else []
    )
    status = "ok" if not failures else ("partial" if documents else "failed")
    return {
        "status": status,
        "market": normalized_market,
        "tickers": requested_tickers,
        "sources": requested_sources,
        "documents": [_document_summary(document) for document in documents],
        "metadata_items": sum(
            int(document.metadata.get("item_count") or 0)
            for document in documents
        ),
        "persisted": persisted,
        "failures": failures,
        "policy": "metadata_only_no_financial_numbers",
    }


def _research_metadata_connector(source_name: str):
    aliases = {
        "naver": "naver_search_research",
        "naver_search": "naver_search_research",
        "naver_search_research": "naver_search_research",
        "hankyung": "hankyung_consensus_metadata",
        "hankyung_consensus": "hankyung_consensus_metadata",
        "hankyung_consensus_metadata": "hankyung_consensus_metadata",
    }
    source_id = aliases.get(source_name.lower())
    if source_id == "naver_search_research":
        return NaverResearchSearchConnector()
    if source_id == "hankyung_consensus_metadata":
        return HankyungConsensusMetadataConnector()
    raise ValueError(f"unknown research metadata source: {source_name}")


def collect_jquants_data(
    tickers: str | list[str],
    start_year: int,
    end_year: int,
    *,
    endpoints: str | list[str] = "daily_quotes,statements,dividends",
    persist: bool = False,
    force_refresh: bool = False,
) -> dict[str, Any]:
    requested_tickers = _split_csv(tickers)
    requested_endpoints = [endpoint.lower() for endpoint in _split_csv(endpoints)]
    documents: list[ConnectorDocument] = []
    connector = JQuantsConnector()
    for ticker in requested_tickers:
        documents.extend(
            connector.collect_bundle(
                ConnectorRequest(
                    ticker=ticker,
                    market="JP",
                    start_year=start_year,
                    end_year=end_year,
                    force_refresh=force_refresh,
                ),
                endpoints=requested_endpoints,
            )
        )
    persisted = _persist_jquants_documents(documents) if persist else []
    return {
        "status": "ok",
        "market": "JP",
        "tickers": requested_tickers,
        "endpoints": requested_endpoints,
        "documents": [_document_summary(document) for document in documents],
        "price_rows": sum(_jquants_daily_quote_row_count(document) for document in documents),
        "dividend_rows": sum(_jquants_dividend_row_count(document) for document in documents),
        "statement_rows": sum(_jquants_statement_row_count(document) for document in documents),
        "persisted": persisted,
    }


def collect_edinet_filings(
    tickers: str | list[str],
    start_year: int,
    end_year: int,
    *,
    download_types: str | list[str] = "metadata,csv",
    doc_type_codes: str | list[str] = "120",
    persist: bool = False,
    force_refresh: bool = False,
) -> dict[str, Any]:
    requested_tickers = _split_csv(tickers)
    requested_downloads = [item.lower() for item in _split_csv(download_types)]
    requested_doc_types = _split_csv(doc_type_codes)
    documents: list[ConnectorDocument] = []
    connector = EdinetConnector()
    for ticker in requested_tickers:
        documents.extend(
            connector.collect_bundle(
                ConnectorRequest(
                    ticker=ticker,
                    market="JP",
                    start_year=start_year,
                    end_year=end_year,
                    force_refresh=force_refresh,
                ),
                download_types=requested_downloads,
                doc_type_codes=requested_doc_types,
            )
        )
    persisted = _persist_edinet_documents(documents) if persist else []
    return {
        "status": "ok",
        "market": "JP",
        "tickers": requested_tickers,
        "download_types": requested_downloads,
        "doc_type_codes": requested_doc_types,
        "documents": [_document_summary(document) for document in documents],
        "metadata_documents": sum(
            _edinet_document_type(document) == "metadata_list" for document in documents
        ),
        "csv_zips": sum(
            _edinet_document_type(document) == "xbrl_to_csv_zip" for document in documents
        ),
        "xbrl_zips": sum(_edinet_document_type(document) == "xbrl_zip" for document in documents),
        "persisted": persisted,
    }


def _collect_connector_documents(
    market: str,
    ticker: str,
    start_year: int,
    end_year: int,
    force_refresh: bool,
) -> list[ConnectorDocument]:
    request = ConnectorRequest(
        ticker=ticker,
        market=market,
        start_year=start_year,
        end_year=end_year,
        force_refresh=force_refresh,
    )
    if market == "US":
        return SecEdgarConnector().collect(request)
    if market == "KR":
        return OpenDartConnector().collect(request)
    jquants_docs = JQuantsConnector().collect(request)
    try:
        return [*jquants_docs, *EdinetConnector().collect(request)]
    except (RuntimeError, LookupError):
        return jquants_docs


def _persist_connector_documents(
    market: str,
    ticker: str,
    documents: list[ConnectorDocument],
) -> list[dict[str, str]]:
    repo = IngestionRepository()
    run_id = repo.start_run(market=market, source="connector_collect", ticker=ticker)
    queue = BlobUploadQueue()
    persisted: list[dict[str, str]] = []
    try:
        meta = _security_meta(ticker)
        security = repo.ensure_security(
            ticker.upper(),
            meta.name,
            meta.country,
            meta.currency,
            meta.exchange,
        )
        for document in documents:
            local_path, digest = _write_raw_document(document)
            source_document = SourceDocument(
                id=digest,
                ticker=ticker.upper(),
                accession_number=document.metadata.get("accession_number") or document.identifier,
                form_type=document.metadata.get("form_type"),
                filing_url=document.metadata.get("filing_url"),
                source_url=document.url,
                content=document.payload.decode("utf-8", errors="ignore"),
                local_path=str(local_path),
                content_hash=digest,
                metadata=document.metadata,
            )
            source_document_id = repo.store_source_document(
                security.id,
                source_document,
                document.source,
            )
            blob_key = _blob_key(document, digest)
            queue.enqueue(
                BlobQueueItem(
                    local_path=str(local_path),
                    blob_key=blob_key,
                    content_type=document.content_type,
                    metadata=document.metadata,
                )
            )
            repo.store_raw_object(
                ingestion_run_id=run_id,
                source_document_id=source_document_id,
                market=document.market,
                source=document.source,
                ticker=ticker,
                identifier=document.identifier,
                source_url=document.url,
                local_path=str(local_path),
                content_hash=digest,
                content_type=document.content_type,
                metadata=document.metadata,
                blob_key=blob_key,
            )
            normalized_counts = _persist_market_standard_document(
                repo,
                security.id,
                source_document_id,
                document,
            )
            persisted.append(
                {
                    "identifier": document.identifier,
                    "content_hash": digest,
                    "blob_key": blob_key,
                    "adjusted_earnings": str(normalized_counts["adjusted_earnings"]),
                    "metric_values": str(normalized_counts["metric_values"]),
                }
            )
        repo.finish_run(run_id)
    except Exception as exc:
        repo.finish_run(run_id, status="failed", error_summary=str(exc))
        raise
    return persisted


def _persist_sec_bulk_documents(documents: list[ConnectorDocument]) -> list[dict[str, str]]:
    repo = IngestionRepository()
    run_id = repo.start_run(market="US", source="sec_bulk", ticker="BULK")
    queue = BlobUploadQueue()
    persisted: list[dict[str, str]] = []
    try:
        for document in documents:
            local_path, digest = _write_raw_document(document)
            archive = str(document.metadata.get("archive") or document.identifier)
            source_document = SourceDocument(
                id=digest,
                ticker=None,
                accession_number=document.identifier,
                form_type=_sec_bulk_form_type(archive),
                filing_url=None,
                source_url=document.url,
                content=None,
                local_path=str(local_path),
                content_hash=digest,
                metadata=document.metadata,
            )
            source_document_id = repo.store_source_document(None, source_document, "sec_bulk")
            blob_key = _blob_key(document, digest)
            queue.enqueue(
                BlobQueueItem(
                    local_path=str(local_path),
                    blob_key=blob_key,
                    content_type=document.content_type,
                    metadata=document.metadata,
                )
            )
            repo.store_raw_object(
                ingestion_run_id=run_id,
                source_document_id=source_document_id,
                market=document.market,
                source=document.source,
                ticker=document.ticker,
                identifier=document.identifier,
                source_url=document.url,
                local_path=str(local_path),
                content_hash=digest,
                content_type=document.content_type,
                metadata=document.metadata,
                blob_key=blob_key,
            )
            persisted.append(
                {
                    "identifier": document.identifier,
                    "content_hash": digest,
                    "blob_key": blob_key,
                    "archive": archive,
                    "bytes": str(len(document.payload)),
                }
            )
        repo.finish_run(run_id)
    except Exception as exc:
        repo.finish_run(run_id, status="failed", error_summary=str(exc))
        raise
    return persisted


def _persist_sec_bulk_warehouse(
    companyfacts_zip: Path,
    submissions_zip: Path | None,
    fact_rows: list[SecBulkFactRow],
    metric_rows: list[SecBulkFactRow | SecBulkDerivedMetricRow],
) -> dict[str, int]:
    repo = IngestionRepository()
    run_id = repo.start_run(
        market="US",
        source="sec_bulk_warehouse",
        ticker="BULK",
        metadata={
            "companyfacts_zip": str(companyfacts_zip),
            "submissions_zip": str(submissions_zip) if submissions_zip else None,
        },
    )
    try:
        companyfacts_source_document_id = _store_sec_bulk_archive_source_document(
            repo,
            run_id,
            companyfacts_zip,
            "companyfacts",
            SEC_COMPANYFACTS_URL,
        )
        source_documents = 1
        if submissions_zip is not None:
            _store_sec_bulk_archive_source_document(
                repo,
                run_id,
                submissions_zip,
                "submissions",
                SEC_SUBMISSIONS_URL,
            )
            source_documents += 1
        securities = _ensure_sec_bulk_securities(repo, fact_rows)
        financial_count = 0
        for row in fact_rows:
            security = securities[row.ticker]
            trace = row.source_trace | {
                "source_document_id": str(companyfacts_source_document_id),
            }
            repo.store_financial_fact(
                security.id,
                companyfacts_source_document_id,
                taxonomy=row.taxonomy,
                tag=row.tag,
                label=row.label,
                fiscal_year=row.fiscal_year,
                fiscal_period=row.fiscal_period,
                period_start=row.period_start,
                period_end=row.period_end,
                filed_at=row.filed_at,
                accession_number=row.accession_number,
                form_type=row.form_type,
                frame=row.frame,
                unit=row.unit,
                currency=row.currency,
                value=row.value,
                source="sec_companyfacts_bulk",
                source_url=row.source_url,
                quality_status=row.quality_status,
                source_trace=trace,
                metadata=row.metadata,
            )
            financial_count += 1
        metric_count = 0
        for row in metric_rows:
            security = securities[row.ticker]
            trace = row.source_trace | {
                "source_document_id": str(companyfacts_source_document_id),
            }
            formula = getattr(row, "formula", None) or (
                f"SEC companyfacts {row.taxonomy}:{row.tag} reported fact"
            )
            method = getattr(row, "method", "SEC_COMPANYFACTS_BULK")
            repo.store_metric_value(
                security_id=security.id,
                source_document_id=companyfacts_source_document_id,
                metric_key=row.metric_key,
                fiscal_year=row.fiscal_year,
                value=row.value,
                unit=row.unit,
                currency=row.currency,
                formula=formula,
                method=method,
                quality_status=row.quality_status,
                source_trace=trace,
            )
            metric_count += 1
        repo.finish_run(run_id)
    except Exception as exc:
        repo.finish_run(run_id, status="failed", error_summary=str(exc))
        raise
    return {
        "financial_facts": financial_count,
        "metric_values": metric_count,
        "source_documents": source_documents,
    }


def _store_sec_bulk_archive_source_document(
    repo: IngestionRepository,
    run_id,
    path: Path,
    archive: str,
    source_url: str,
):
    digest = _hash_file(path)
    source_document = SourceDocument(
        id=digest,
        ticker=None,
        accession_number=f"sec-bulk-{archive}",
        form_type=_sec_bulk_form_type(archive),
        filing_url=None,
        source_url=source_url,
        content=None,
        local_path=str(path),
        content_hash=digest,
        metadata={
            "archive": archive,
            "endpoint": source_url,
            "source_type": "sec_edgar_bulk_archive",
            "content_hash": digest,
        },
    )
    source_document_id = repo.store_source_document(None, source_document, "sec_bulk")
    repo.store_raw_object(
        ingestion_run_id=run_id,
        source_document_id=source_document_id,
        market="US",
        source="sec_bulk",
        ticker="BULK",
        identifier=f"sec-bulk-{archive}",
        source_url=source_url,
        local_path=str(path),
        content_hash=digest,
        content_type="application/zip",
        metadata=source_document.metadata,
        blob_key=f"raw/sec_bulk/BULK/{digest}.zip",
    )
    return source_document_id


def _ensure_sec_bulk_securities(
    repo: IngestionRepository,
    fact_rows: list[SecBulkFactRow],
):
    securities = {}
    for row in fact_rows:
        if row.ticker in securities:
            continue
        meta = _security_meta(row.ticker, "USD")
        securities[row.ticker] = repo.ensure_security(
            row.ticker,
            row.entity_name or meta.name,
            "US",
            "USD",
            row.exchange or meta.exchange,
        )
    return securities


def _persist_raw_stat_documents(
    market: str,
    source: str,
    ticker: str,
    form_type: str,
    documents: list[ConnectorDocument],
    *,
    normalize_macro: bool = False,
) -> list[dict[str, str]]:
    repo = IngestionRepository()
    run_id = repo.start_run(market=market, source=source, ticker=ticker)
    queue = BlobUploadQueue()
    persisted: list[dict[str, str]] = []
    try:
        for document in documents:
            local_path, digest = _write_raw_document(document)
            source_document = SourceDocument(
                id=digest,
                ticker=document.ticker.upper(),
                accession_number=document.identifier,
                form_type=form_type,
                filing_url=None,
                source_url=document.url,
                content=document.payload.decode("utf-8", errors="ignore"),
                local_path=str(local_path),
                content_hash=digest,
                metadata=document.metadata,
            )
            source_document_id = repo.store_source_document(None, source_document, source)
            blob_key = _blob_key(document, digest)
            queue.enqueue(
                BlobQueueItem(
                    local_path=str(local_path),
                    blob_key=blob_key,
                    content_type=document.content_type,
                    metadata=document.metadata,
                )
            )
            repo.store_raw_object(
                ingestion_run_id=run_id,
                source_document_id=source_document_id,
                market=document.market,
                source=document.source,
                ticker=document.ticker,
                identifier=document.identifier,
                source_url=document.url,
                local_path=str(local_path),
                content_hash=digest,
                content_type=document.content_type,
                metadata=document.metadata,
                blob_key=blob_key,
            )
            macro_observations = (
                _persist_official_stat_document(repo, source_document_id, document)
                if normalize_macro
                else {"macro_observations": 0, "industry_observations": 0}
            )
            persisted.append(
                {
                    "identifier": document.identifier,
                    "content_hash": digest,
                    "blob_key": blob_key,
                    "raw_observations": str(_json_payload_row_count(document)),
                    "macro_observations": str(macro_observations["macro_observations"]),
                    "industry_observations": str(
                        macro_observations["industry_observations"]
                    ),
                }
            )
        repo.finish_run(run_id)
    except Exception as exc:
        repo.finish_run(run_id, status="failed", error_summary=str(exc))
        raise
    return persisted


def _persist_research_metadata_documents(
    market: str,
    documents: list[ConnectorDocument],
) -> list[dict[str, str]]:
    repo = IngestionRepository()
    run_id = repo.start_run(market=market, source="research_metadata", ticker="RESEARCH_BATCH")
    queue = BlobUploadQueue()
    persisted: list[dict[str, str]] = []
    try:
        for document in documents:
            meta = _security_meta(
                document.ticker.upper(),
                fallback_currency=_currency_for_market(market),
            )
            security = repo.ensure_security(
                document.ticker.upper(),
                meta.name,
                meta.country,
                meta.currency,
                meta.exchange,
            )
            local_path, digest = _write_raw_document(document)
            source_document = SourceDocument(
                id=digest,
                ticker=document.ticker.upper(),
                accession_number=document.identifier,
                form_type=str(document.metadata.get("form_type") or "RESEARCH_LINK_METADATA"),
                filing_url=None,
                source_url=document.url,
                content=document.payload.decode("utf-8", errors="ignore"),
                local_path=str(local_path),
                content_hash=digest,
                metadata=document.metadata,
            )
            source_document_id = repo.store_source_document(
                security.id,
                source_document,
                document.source,
            )
            blob_key = _blob_key(document, digest)
            queue.enqueue(
                BlobQueueItem(
                    local_path=str(local_path),
                    blob_key=blob_key,
                    content_type=document.content_type,
                    metadata=document.metadata,
                )
            )
            repo.store_raw_object(
                ingestion_run_id=run_id,
                source_document_id=source_document_id,
                market=document.market,
                source=document.source,
                ticker=document.ticker,
                identifier=document.identifier,
                source_url=document.url,
                local_path=str(local_path),
                content_hash=digest,
                content_type=document.content_type,
                metadata=document.metadata
                | {
                    "source_document_id": str(source_document_id),
                    "storage_policy": "metadata_only_no_financial_numbers",
                },
                blob_key=blob_key,
            )
            persisted.append(
                {
                    "identifier": document.identifier,
                    "source": document.source,
                    "content_hash": digest,
                    "blob_key": blob_key,
                    "metadata_items": str(document.metadata.get("item_count") or 0),
                }
            )
        repo.finish_run(run_id)
    except Exception as exc:
        repo.finish_run(run_id, status="failed", error_summary=str(exc))
        raise
    return persisted


def _persist_official_stat_document(
    repo: IngestionRepository,
    source_document_id,
    document: ConnectorDocument,
) -> dict[str, int]:
    observations = normalize_official_stat_document(document)
    industry_count = 0
    for observation in observations:
        trace = observation.source_trace | {
            "source_document_id": str(source_document_id),
        }
        repo.store_macro_observation(
            observation.series_id,
            observation.observation_date,
            observation.value,
            document.source,
            trace,
            unit=observation.unit,
            frequency=observation.frequency,
            source_url=document.url,
            source_document_id=source_document_id,
        )
        industry_payload = _official_stat_industry_payload(document, observation)
        if industry_payload is not None:
            repo.store_industry_observation(
                market=industry_payload["market"],
                series_id=industry_payload["series_id"],
                observation_date=observation.observation_date,
                value=observation.value,
                source=document.source,
                category=industry_payload["category"],
                source_trace=trace
                | {
                    "industry_series_id": industry_payload["series_id"],
                    "industry_category": industry_payload["category"],
                },
                unit=observation.unit,
                frequency=observation.frequency,
                region=industry_payload["region"],
                industry=industry_payload["industry"],
                source_url=document.url,
                source_document_id=source_document_id,
                dimensions=industry_payload["dimensions"],
            )
            industry_count += 1
    return {
        "macro_observations": len(observations),
        "industry_observations": industry_count,
    }


def _official_stat_industry_payload(
    document: ConnectorDocument,
    observation,
) -> dict[str, Any] | None:
    trace = observation.source_trace
    dimensions = trace.get("dimensions")
    if not isinstance(dimensions, dict):
        dimensions = {}
    if document.source == "kosis":
        category = "official_kr_statistics"
        market = "KR"
        industry = _dimension_label(dimensions) or trace.get("tbl_id")
        region = _region_label(dimensions)
    elif document.source == "estat":
        category = "official_jp_statistics"
        market = "JP"
        industry = _dimension_label(dimensions) or trace.get("stats_data_id")
        region = _region_label(dimensions)
    elif document.source == "ecos":
        category = "official_kr_macro_industry"
        market = "KR"
        industry = trace.get("item_name") or trace.get("item_code")
        region = None
    else:
        return None
    return {
        "market": market,
        "series_id": f"IND:{observation.series_id}",
        "category": category,
        "region": region,
        "industry": str(industry) if industry else None,
        "dimensions": dimensions
        | {
            key: value
            for key, value in {
                "stat_code": trace.get("stat_code"),
                "tbl_id": trace.get("tbl_id"),
                "stats_data_id": trace.get("stats_data_id"),
                "item_code": trace.get("item_code"),
                "item_name": trace.get("item_name"),
            }.items()
            if value not in {None, ""}
        },
    }


def _dimension_label(dimensions: dict[str, Any]) -> str | None:
    if not dimensions:
        return None
    candidates = [
        str(value)
        for key, value in sorted(dimensions.items())
        if value not in {None, ""} and not key.upper().startswith("REG")
    ]
    return " / ".join(candidates) if candidates else None


def _region_label(dimensions: dict[str, Any]) -> str | None:
    for key, value in sorted(dimensions.items()):
        normalized = key.upper()
        if value in {None, ""}:
            continue
        if "REG" in normalized or "AREA" in normalized or "PREF" in normalized:
            return str(value)
    return None


def _persist_fred_documents(documents: list[ConnectorDocument]) -> list[dict[str, str]]:
    repo = IngestionRepository()
    run_id = repo.start_run(market="GLOBAL", source="fred", ticker="MACRO")
    queue = BlobUploadQueue()
    persisted: list[dict[str, str]] = []
    try:
        for document in documents:
            local_path, digest = _write_raw_document(document)
            source_document = SourceDocument(
                id=digest,
                ticker=document.ticker.upper(),
                accession_number=document.identifier,
                form_type="FRED_SERIES_OBSERVATIONS",
                filing_url=None,
                source_url=document.url,
                content=document.payload.decode("utf-8", errors="ignore"),
                local_path=str(local_path),
                content_hash=digest,
                metadata=document.metadata,
            )
            source_document_id = repo.store_source_document(None, source_document, "fred")
            blob_key = _blob_key(document, digest)
            queue.enqueue(
                BlobQueueItem(
                    local_path=str(local_path),
                    blob_key=blob_key,
                    content_type=document.content_type,
                    metadata=document.metadata,
                )
            )
            repo.store_raw_object(
                ingestion_run_id=run_id,
                source_document_id=source_document_id,
                market=document.market,
                source=document.source,
                ticker=document.ticker,
                identifier=document.identifier,
                source_url=document.url,
                local_path=str(local_path),
                content_hash=digest,
                content_type=document.content_type,
                metadata=document.metadata,
                blob_key=blob_key,
            )
            counts = _persist_fred_macro_document(repo, source_document_id, document)
            persisted.append(
                {
                    "identifier": document.identifier,
                    "content_hash": digest,
                    "blob_key": blob_key,
                    "macro_observations": str(counts["macro_observations"]),
                    "recession_periods": str(counts["recession_periods"]),
                }
            )
        repo.finish_run(run_id)
    except Exception as exc:
        repo.finish_run(run_id, status="failed", error_summary=str(exc))
        raise
    return persisted


def _persist_stooq_documents(
    market: str,
    documents: list[ConnectorDocument],
) -> list[dict[str, str]]:
    repo = IngestionRepository()
    run_id = repo.start_run(market=market.upper(), source="stooq", ticker="PRICE_BATCH")
    queue = BlobUploadQueue()
    persisted: list[dict[str, str]] = []
    try:
        for document in documents:
            meta = _security_meta(document.ticker, _currency_for_market(document.market))
            country = (
                _country_for_market(document.market)
                if document.market.upper() in {"US", "JP", "KR"}
                else meta.country
            )
            security = repo.ensure_security(
                document.ticker.upper(),
                meta.name,
                country,
                meta.currency,
                meta.exchange or document.market.upper(),
            )
            local_path, digest = _write_raw_document(document)
            source_document = SourceDocument(
                id=digest,
                ticker=document.ticker.upper(),
                accession_number=document.identifier,
                form_type="STOOQ_DAILY_CSV",
                filing_url=None,
                source_url=document.url,
                content=document.payload.decode("utf-8", errors="ignore"),
                local_path=str(local_path),
                content_hash=digest,
                metadata=document.metadata,
            )
            source_document_id = repo.store_source_document(security.id, source_document, "stooq")
            blob_key = _blob_key(document, digest)
            queue.enqueue(
                BlobQueueItem(
                    local_path=str(local_path),
                    blob_key=blob_key,
                    content_type=document.content_type,
                    metadata=document.metadata,
                )
            )
            repo.store_raw_object(
                ingestion_run_id=run_id,
                source_document_id=source_document_id,
                market=document.market,
                source=document.source,
                ticker=document.ticker,
                identifier=document.identifier,
                source_url=document.url,
                local_path=str(local_path),
                content_hash=digest,
                content_type=document.content_type,
                metadata=document.metadata,
                blob_key=blob_key,
            )
            price_rows = _persist_stooq_price_bars(
                repo,
                security.id,
                source_document_id,
                document,
                meta.currency,
            )
            persisted.append(
                {
                    "identifier": document.identifier,
                    "content_hash": digest,
                    "blob_key": blob_key,
                    "price_bars": str(price_rows),
                }
            )
        repo.finish_run(run_id)
    except Exception as exc:
        repo.finish_run(run_id, status="failed", error_summary=str(exc))
        raise
    return persisted


def _persist_fdr_documents(
    market: str,
    documents: list[ConnectorDocument],
) -> list[dict[str, str]]:
    repo = IngestionRepository()
    run_id = repo.start_run(
        market=market.upper(),
        source="finance_data_reader",
        ticker="PRICE_BATCH",
    )
    queue = BlobUploadQueue()
    persisted: list[dict[str, str]] = []
    try:
        for document in documents:
            meta = _security_meta(document.ticker, _currency_for_market(document.market))
            country = (
                _country_for_market(document.market)
                if document.market.upper() in {"US", "JP", "KR"}
                else meta.country
            )
            security = repo.ensure_security(
                document.ticker.upper(),
                meta.name,
                country,
                meta.currency,
                meta.exchange or document.market.upper(),
            )
            local_path, digest = _write_raw_document(document)
            source_document = SourceDocument(
                id=digest,
                ticker=document.ticker.upper(),
                accession_number=document.identifier,
                form_type="FDR_DAILY_CSV",
                filing_url=None,
                source_url=document.url,
                content=document.payload.decode("utf-8-sig", errors="ignore"),
                local_path=str(local_path),
                content_hash=digest,
                metadata=document.metadata,
            )
            source_document_id = repo.store_source_document(
                security.id,
                source_document,
                "finance_data_reader",
            )
            blob_key = _blob_key(document, digest)
            queue.enqueue(
                BlobQueueItem(
                    local_path=str(local_path),
                    blob_key=blob_key,
                    content_type=document.content_type,
                    metadata=document.metadata,
                )
            )
            repo.store_raw_object(
                ingestion_run_id=run_id,
                source_document_id=source_document_id,
                market=document.market,
                source=document.source,
                ticker=document.ticker,
                identifier=document.identifier,
                source_url=document.url,
                local_path=str(local_path),
                content_hash=digest,
                content_type=document.content_type,
                metadata=document.metadata,
                blob_key=blob_key,
            )
            price_rows = _persist_fdr_price_bars(
                repo,
                security.id,
                source_document_id,
                document,
                meta.currency,
            )
            persisted.append(
                {
                    "identifier": document.identifier,
                    "content_hash": digest,
                    "blob_key": blob_key,
                    "price_bars": str(price_rows),
                }
            )
        repo.finish_run(run_id)
    except Exception as exc:
        repo.finish_run(run_id, status="failed", error_summary=str(exc))
        raise
    return persisted


def _persist_pykrx_documents(documents: list[ConnectorDocument]) -> list[dict[str, str]]:
    repo = IngestionRepository()
    run_id = repo.start_run(market="KR", source="pykrx", ticker="PRICE_BATCH")
    queue = BlobUploadQueue()
    persisted: list[dict[str, str]] = []
    try:
        for document in documents:
            meta = _security_meta(document.ticker, "KRW")
            security = repo.ensure_security(
                document.ticker.upper(),
                meta.name,
                "KR",
                "KRW",
                meta.exchange or "KRX",
            )
            local_path, digest = _write_raw_document(document)
            source_document = SourceDocument(
                id=digest,
                ticker=document.ticker.upper(),
                accession_number=document.identifier,
                form_type=(
                    "PYKRX_FUNDAMENTAL_CSV"
                    if _is_pykrx_fundamental_document(document)
                    else "PYKRX_DAILY_OHLCV_CSV"
                ),
                filing_url=None,
                source_url=document.url,
                content=document.payload.decode("utf-8-sig", errors="ignore"),
                local_path=str(local_path),
                content_hash=digest,
                metadata=document.metadata,
            )
            source_document_id = repo.store_source_document(security.id, source_document, "pykrx")
            blob_key = _blob_key(document, digest)
            queue.enqueue(
                BlobQueueItem(
                    local_path=str(local_path),
                    blob_key=blob_key,
                    content_type=document.content_type,
                    metadata=document.metadata,
                )
            )
            repo.store_raw_object(
                ingestion_run_id=run_id,
                source_document_id=source_document_id,
                market=document.market,
                source=document.source,
                ticker=document.ticker,
                identifier=document.identifier,
                source_url=document.url,
                local_path=str(local_path),
                content_hash=digest,
                content_type=document.content_type,
                metadata=document.metadata,
                blob_key=blob_key,
            )
            if _is_pykrx_fundamental_document(document):
                price_rows = 0
                fundamental_rows = _pykrx_fundamental_row_count(document)
            else:
                price_rows = _persist_pykrx_price_bars(
                    repo,
                    security.id,
                    source_document_id,
                    document,
                )
                fundamental_rows = 0
            persisted.append(
                {
                    "identifier": document.identifier,
                    "content_hash": digest,
                    "blob_key": blob_key,
                    "price_bars": str(price_rows),
                    "fundamental_rows": str(fundamental_rows),
                }
            )
        repo.finish_run(run_id)
    except Exception as exc:
        repo.finish_run(run_id, status="failed", error_summary=str(exc))
        raise
    return persisted


def _persist_marcap_documents(
    documents: list[ConnectorDocument],
    tickers: list[str],
) -> list[dict[str, str]]:
    repo = IngestionRepository()
    run_id = repo.start_run(market="KR", source="marcap", ticker="KR_MARKET")
    queue = BlobUploadQueue()
    persisted: list[dict[str, str]] = []
    ticker_filter = {_marcap_code_from_ticker(ticker) for ticker in tickers if ticker.strip()}
    security_cache: dict[str, Any] = {}
    try:
        for document in documents:
            local_path, digest = _write_raw_document(document)
            source_document = SourceDocument(
                id=digest,
                ticker="KR_MARKET",
                accession_number=document.identifier,
                form_type="MARCAP_YEARLY_PARQUET",
                filing_url=None,
                source_url=document.url,
                content=None,
                local_path=str(local_path),
                content_hash=digest,
                metadata=document.metadata,
            )
            source_document_id = repo.store_source_document(None, source_document, "marcap")
            blob_key = _blob_key(document, digest)
            queue.enqueue(
                BlobQueueItem(
                    local_path=str(local_path),
                    blob_key=blob_key,
                    content_type=document.content_type,
                    metadata=document.metadata,
                )
            )
            repo.store_raw_object(
                ingestion_run_id=run_id,
                source_document_id=source_document_id,
                market=document.market,
                source=document.source,
                ticker=document.ticker,
                identifier=document.identifier,
                source_url=document.url,
                local_path=str(local_path),
                content_hash=digest,
                content_type=document.content_type,
                metadata=document.metadata,
                blob_key=blob_key,
            )
            price_rows = _persist_marcap_price_bars(
                repo,
                source_document_id,
                document,
                ticker_filter,
                security_cache,
            )
            persisted.append(
                {
                    "identifier": document.identifier,
                    "content_hash": digest,
                    "blob_key": blob_key,
                    "price_bars": str(price_rows),
                }
            )
        repo.finish_run(run_id)
    except Exception as exc:
        repo.finish_run(run_id, status="failed", error_summary=str(exc))
        raise
    return persisted


def _persist_jquants_documents(documents: list[ConnectorDocument]) -> list[dict[str, str]]:
    repo = IngestionRepository()
    run_id = repo.start_run(market="JP", source="jquants", ticker="JP_BATCH")
    queue = BlobUploadQueue()
    persisted: list[dict[str, str]] = []
    try:
        for document in documents:
            meta = _security_meta(document.ticker, "JPY")
            security = repo.ensure_security(
                document.ticker.upper(),
                meta.name,
                "JP",
                "JPY",
                meta.exchange or "TSE",
            )
            local_path, digest = _write_raw_document(document)
            endpoint = str(document.metadata.get("endpoint") or "")
            source_document = SourceDocument(
                id=digest,
                ticker=document.ticker.upper(),
                accession_number=document.identifier,
                form_type=_jquants_form_type(endpoint),
                filing_url=None,
                source_url=document.url,
                content=document.payload.decode("utf-8", errors="ignore"),
                local_path=str(local_path),
                content_hash=digest,
                metadata=document.metadata,
            )
            source_document_id = repo.store_source_document(security.id, source_document, "jquants")
            blob_key = _blob_key(document, digest)
            queue.enqueue(
                BlobQueueItem(
                    local_path=str(local_path),
                    blob_key=blob_key,
                    content_type=document.content_type,
                    metadata=document.metadata,
                )
            )
            repo.store_raw_object(
                ingestion_run_id=run_id,
                source_document_id=source_document_id,
                market=document.market,
                source=document.source,
                ticker=document.ticker,
                identifier=document.identifier,
                source_url=document.url,
                local_path=str(local_path),
                content_hash=digest,
                content_type=document.content_type,
                metadata=document.metadata,
                blob_key=blob_key,
            )
            price_rows = 0
            dividend_rows = 0
            adjusted_rows = 0
            metric_rows = 0
            if endpoint == "/prices/daily_quotes":
                price_rows = _persist_jquants_price_bars(
                    repo,
                    security.id,
                    source_document_id,
                    document,
                )
            elif endpoint == "/fins/dividend":
                dividend_rows = _persist_jquants_dividends(
                    repo,
                    security.id,
                    source_document_id,
                    document,
                )
            elif endpoint == "/fins/statements":
                counts = _persist_market_standard_document(
                    repo,
                    security.id,
                    source_document_id,
                    document,
                )
                adjusted_rows = counts["adjusted_earnings"]
                metric_rows = counts["metric_values"]
            persisted.append(
                {
                    "identifier": document.identifier,
                    "content_hash": digest,
                    "blob_key": blob_key,
                    "price_bars": str(price_rows),
                    "dividends": str(dividend_rows),
                    "adjusted_earnings": str(adjusted_rows),
                    "metric_values": str(metric_rows),
                }
            )
        repo.finish_run(run_id)
    except Exception as exc:
        repo.finish_run(run_id, status="failed", error_summary=str(exc))
        raise
    return persisted


def _persist_edinet_documents(documents: list[ConnectorDocument]) -> list[dict[str, str]]:
    repo = IngestionRepository()
    run_id = repo.start_run(market="JP", source="edinet", ticker="JP_FILINGS_BATCH")
    queue = BlobUploadQueue()
    persisted: list[dict[str, str]] = []
    try:
        for document in documents:
            meta = _security_meta(document.ticker, "JPY")
            security = repo.ensure_security(
                document.ticker.upper(),
                meta.name,
                "JP",
                "JPY",
                meta.exchange or "TSE",
            )
            local_path, digest = _write_raw_document(document)
            document_type = _edinet_document_type(document)
            source_document = SourceDocument(
                id=digest,
                ticker=document.ticker.upper(),
                accession_number=(
                    document.metadata.get("doc_id")
                    or document.metadata.get("edinet_code")
                    or document.identifier
                ),
                form_type=_edinet_form_type(document_type),
                filing_url=None,
                source_url=document.url,
                content=_edinet_source_document_content(document),
                local_path=str(local_path),
                content_hash=digest,
                metadata=document.metadata,
            )
            source_document_id = repo.store_source_document(security.id, source_document, "edinet")
            blob_key = _blob_key(document, digest)
            queue.enqueue(
                BlobQueueItem(
                    local_path=str(local_path),
                    blob_key=blob_key,
                    content_type=document.content_type,
                    metadata=document.metadata,
                )
            )
            repo.store_raw_object(
                ingestion_run_id=run_id,
                source_document_id=source_document_id,
                market=document.market,
                source=document.source,
                ticker=document.ticker,
                identifier=document.identifier,
                source_url=document.url,
                local_path=str(local_path),
                content_hash=digest,
                content_type=document.content_type,
                metadata=document.metadata,
                blob_key=blob_key,
            )
            persisted.append(
                {
                    "identifier": document.identifier,
                    "content_hash": digest,
                    "blob_key": blob_key,
                    "document_type": document_type,
                }
            )
        repo.finish_run(run_id)
    except Exception as exc:
        repo.finish_run(run_id, status="failed", error_summary=str(exc))
        raise
    return persisted


def _persist_stooq_price_bars(
    repo: IngestionRepository,
    security_id,
    source_document_id,
    document: ConnectorDocument,
    currency: str,
) -> int:
    rows = _stooq_csv_rows(document)
    stored = 0
    for row in rows:
        trade_date = date.fromisoformat(row["Date"])
        close_price = Decimal(row["Close"])
        trace = {
            "source_type": "stooq",
            "source_document_id": str(source_document_id),
            "source_url": document.url,
            "filing_id": document.identifier,
            "period": trade_date.isoformat(),
            "unit": "per_share",
            "currency": currency,
            "formula": "Stooq daily CSV Close column imported as price_bars.close_price",
            "method": "STOOQ_DAILY_CLOSE",
            "quality_status": "source_backed_price",
            "stooq_symbol": document.metadata.get("stooq_symbol"),
            "raw_open": row.get("Open"),
            "raw_high": row.get("High"),
            "raw_low": row.get("Low"),
            "raw_volume": row.get("Volume"),
        }
        repo.store_price_bar(
            security_id,
            trade_date.year,
            trade_date,
            close_price,
            currency,
            document.source,
            trace,
        )
        stored += 1
    return stored


def _persist_fdr_price_bars(
    repo: IngestionRepository,
    security_id,
    source_document_id,
    document: ConnectorDocument,
    currency: str,
) -> int:
    rows = _fdr_csv_rows(document)
    stored = 0
    for row in rows:
        raw_date = _first_present(row, "Date", "date")
        raw_close = _first_present(row, "Close", "close")
        if not raw_date or not raw_close:
            continue
        trade_date = date.fromisoformat(str(raw_date))
        close_price = Decimal(str(raw_close))
        trace = {
            "source_type": "finance_data_reader",
            "source_document_id": str(source_document_id),
            "source_url": document.url,
            "filing_id": document.identifier,
            "period": trade_date.isoformat(),
            "unit": "per_share",
            "currency": currency,
            "formula": (
                "FinanceDataReader daily CSV Close column imported as "
                "price_bars.close_price"
            ),
            "method": "FDR_DAILY_CLOSE",
            "quality_status": "wrapper_derived_price",
            "fdr_symbol": document.metadata.get("fdr_symbol"),
            "raw_open": _first_present(row, "Open", "open"),
            "raw_high": _first_present(row, "High", "high"),
            "raw_low": _first_present(row, "Low", "low"),
            "raw_volume": _first_present(row, "Volume", "volume"),
            "raw_change": _first_present(row, "Change", "change"),
        }
        repo.store_price_bar(
            security_id,
            trade_date.year,
            trade_date,
            close_price,
            currency,
            document.source,
            trace,
        )
        stored += 1
    return stored


def _persist_pykrx_price_bars(
    repo: IngestionRepository,
    security_id,
    source_document_id,
    document: ConnectorDocument,
) -> int:
    rows = _pykrx_csv_rows(document)
    stored = 0
    for row in rows:
        trade_date = date.fromisoformat(row["date"])
        close_price = Decimal(row["close"])
        trace = {
            "source_type": "pykrx",
            "source_document_id": str(source_document_id),
            "source_url": document.url,
            "filing_id": document.identifier,
            "period": trade_date.isoformat(),
            "unit": "per_share",
            "currency": "KRW",
            "formula": "pykrx OHLCV CSV close column imported as price_bars.close_price",
            "method": "PYKRX_DAILY_CLOSE",
            "quality_status": "source_backed_price",
            "krx_code": document.metadata.get("krx_code"),
            "endpoint": document.metadata.get("endpoint"),
            "raw_open": row.get("open"),
            "raw_high": row.get("high"),
            "raw_low": row.get("low"),
            "raw_volume": row.get("volume"),
            "raw_value_traded": row.get("value_traded"),
            "raw_change_pct": row.get("change_pct"),
        }
        repo.store_price_bar(
            security_id,
            trade_date.year,
            trade_date,
            close_price,
            "KRW",
            document.source,
            trace,
        )
        stored += 1
    return stored


def _persist_marcap_price_bars(
    repo: IngestionRepository,
    source_document_id,
    document: ConnectorDocument,
    ticker_filter: set[str],
    security_cache: dict[str, Any],
) -> int:
    rows = _marcap_rows(document, ticker_filter)
    stored = 0
    for row in rows:
        krx_code = _normalize_marcap_code(_first_present(row, "Code", "code"))
        if not krx_code:
            continue
        trade_date = _date_or_none(_first_present(row, "Date", "date"))
        close_price = _decimal_from_any(_first_present(row, "Close", "close"))
        if trade_date is None or close_price is None or close_price <= 0:
            continue
        ticker = f"{krx_code}.KS"
        security = security_cache.get(ticker)
        if security is None:
            meta = _security_meta(ticker, "KRW")
            security = repo.ensure_security(
                ticker,
                str(_first_present(row, "Name", "name") or meta.name),
                "KR",
                "KRW",
                str(_first_present(row, "Market", "market") or meta.exchange or "KRX"),
            )
            security_cache[ticker] = security
        market_cap = _normalized_marcap_market_cap(row)
        trace = {
            "source_type": "marcap",
            "source_document_id": str(source_document_id),
            "source_url": document.url,
            "filing_id": document.identifier,
            "period": trade_date.isoformat(),
            "unit": "per_share",
            "currency": "KRW",
            "formula": (
                "FinanceData marcap yearly parquet Close, Marcap, and Stocks "
                "columns imported as source-backed daily price, market cap, "
                "and listed-share evidence"
            ),
            "method": "MARCAP_DAILY_CLOSE",
            "quality_status": "open_dataset_price",
            "krx_code": krx_code,
            "rank": _string_or_none(_first_present(row, "Rank", "rank")),
            "market_cap": (
                _decimal_storage_string(market_cap["value"])
                if isinstance(market_cap["value"], Decimal)
                else None
            ),
            "market_cap_unit": "KRW",
            "market_cap_raw": market_cap["raw_value"],
            "market_cap_raw_unit_detected": market_cap["raw_unit_detected"],
            "market_cap_formula": market_cap["formula"],
            "market_cap_quality_flags": market_cap["quality_flags"],
            "listed_shares": _string_or_none(_first_present(row, "Stocks", "stocks")),
            "listed_shares_unit": "shares",
            "listed_shares_formula": (
                "FinanceData marcap Stocks column reported as listed shares"
            ),
            "raw_open": _string_or_none(_first_present(row, "Open", "open")),
            "raw_high": _string_or_none(_first_present(row, "High", "high")),
            "raw_low": _string_or_none(_first_present(row, "Low", "low")),
            "raw_volume": _string_or_none(_first_present(row, "Volume", "volume")),
            "raw_amount": _string_or_none(_first_present(row, "Amount", "amount")),
            "raw_changes": _string_or_none(_first_present(row, "Changes", "changes")),
            "raw_change_code": _string_or_none(_first_present(row, "ChangeCode", "change_code")),
            "raw_change_ratio": _string_or_none(
                _first_present(row, "ChagesRatio", "ChangesRatio", "change_ratio")
            ),
            "market_id": _string_or_none(_first_present(row, "MarketId", "market_id")),
            "market": _string_or_none(_first_present(row, "Market", "market")),
            "department": _string_or_none(_first_present(row, "Dept", "dept")),
        }
        repo.store_price_bar(
            security.id,
            trade_date.year,
            trade_date,
            close_price,
            "KRW",
            document.source,
            trace,
        )
        stored += 1
    return stored


def _persist_jquants_price_bars(
    repo: IngestionRepository,
    security_id,
    source_document_id,
    document: ConnectorDocument,
) -> int:
    rows = _jquants_daily_quote_rows(document)
    stored = 0
    for row in rows:
        trade_date = _date_or_none(_first_present(row, "Date", "date"))
        close_price = _decimal_from_any(
            _first_present(row, "AdjustmentClose", "AdjustedClose", "Close")
        )
        if trade_date is None or close_price is None:
            continue
        trace = {
            "source_type": "jquants",
            "source_document_id": str(source_document_id),
            "source_url": document.url,
            "filing_id": document.identifier,
            "period": trade_date.isoformat(),
            "unit": "per_share",
            "currency": "JPY",
            "formula": "J-Quants daily_quotes adjusted close if available, else close",
            "method": "JQUANTS_DAILY_CLOSE",
            "quality_status": "source_backed_price",
            "local_code": document.metadata.get("local_code"),
            "endpoint": document.metadata.get("endpoint"),
            "raw_open": _first_present(row, "Open"),
            "raw_high": _first_present(row, "High"),
            "raw_low": _first_present(row, "Low"),
            "raw_close": _first_present(row, "Close"),
            "raw_adjustment_close": _first_present(row, "AdjustmentClose", "AdjustedClose"),
            "raw_volume": _first_present(row, "Volume"),
            "raw_turnover_value": _first_present(row, "TurnoverValue"),
            "raw_adjustment_factor": _first_present(row, "AdjustmentFactor"),
        }
        repo.store_price_bar(
            security_id,
            trade_date.year,
            trade_date,
            close_price,
            "JPY",
            document.source,
            trace,
        )
        stored += 1
    return stored


def _persist_jquants_dividends(
    repo: IngestionRepository,
    security_id,
    source_document_id,
    document: ConnectorDocument,
) -> int:
    rows = _jquants_dividend_rows(document)
    stored = 0
    for row in rows:
        ex_date = _date_or_none(
            _first_present(row, "ExDate", "RecordDate", "ActualRecordDate", "Date")
        )
        amount = _decimal_from_any(
            _first_present(
                row,
                "GrossDividendRate",
                "DividendPerShare",
                "DividendAmount",
                "ResultDividendPerShareFiscalYearEnd",
                "ForecastDividendPerShareAnnual",
            )
        )
        if ex_date is None or amount is None:
            continue
        trace = {
            "source_type": "jquants",
            "source_document_id": str(source_document_id),
            "source_url": document.url,
            "filing_id": document.identifier,
            "period": ex_date.isoformat(),
            "unit": "per_share",
            "currency": "JPY",
            "formula": "J-Quants fins/dividend per-share dividend amount",
            "method": "JQUANTS_DIVIDEND_PER_SHARE",
            "quality_status": "source_backed_dividend",
            "local_code": document.metadata.get("local_code"),
            "endpoint": document.metadata.get("endpoint"),
            "raw_ex_date": _first_present(row, "ExDate"),
            "raw_record_date": _first_present(row, "RecordDate", "ActualRecordDate"),
            "raw_payable_date": _first_present(row, "PayableDate"),
            "raw_interim_final_code": _first_present(row, "InterimFinalCode"),
            "raw_forecast_result_code": _first_present(row, "ForecastResultCode"),
        }
        repo.store_dividend(
            security_id,
            ex_date.year,
            ex_date,
            amount,
            "JPY",
            document.source,
            trace,
        )
        stored += 1
    return stored


def _persist_fred_macro_document(
    repo: IngestionRepository,
    source_document_id,
    document: ConnectorDocument,
) -> dict[str, int]:
    payload = json.loads(document.payload.decode("utf-8"))
    series_id = str(payload.get("series_id") or document.ticker).upper()
    series_meta = (payload.get("series") or [{}])[0]
    unit = series_meta.get("units_short") or series_meta.get("units")
    frequency = series_meta.get("frequency_short") or series_meta.get("frequency")
    observations = payload.get("observations") or []
    stored_observations = 0
    for observation in observations:
        value = _fred_decimal(observation.get("value"))
        if value is None:
            continue
        observation_date = date.fromisoformat(str(observation["date"]))
        trace = {
            "source_type": "fred",
            "source_document_id": str(source_document_id),
            "source_url": document.url,
            "series_id": series_id,
            "period": observation_date.isoformat(),
            "unit": unit,
            "currency": "N/A",
            "formula": "FRED reported observation value",
            "method": "FRED_SERIES_OBSERVATIONS",
            "quality_status": "source_backed_macro",
            "realtime_start": observation.get("realtime_start"),
            "realtime_end": observation.get("realtime_end"),
        }
        repo.store_macro_observation(
            series_id,
            observation_date,
            value,
            document.source,
            trace,
            unit=unit,
            frequency=frequency,
            source_url=document.url,
            source_document_id=source_document_id,
        )
        stored_observations += 1
    recession_periods = 0
    if series_id == "USREC":
        for start_date, end_date in _fred_recession_periods(observations):
            repo.store_recession_period(
                series_id,
                start_date,
                end_date,
                document.source,
                {
                    "source_type": "fred",
                    "source_document_id": str(source_document_id),
                    "source_url": document.url,
                    "series_id": series_id,
                    "formula": "Contiguous FRED USREC observations equal to 1",
                    "method": "FRED_RECESSION_BAND",
                    "quality_status": "source_backed_macro",
                },
                source_document_id=source_document_id,
            )
            recession_periods += 1
    return {"macro_observations": stored_observations, "recession_periods": recession_periods}


def _persist_market_standard_document(
    repo: IngestionRepository,
    security_id,
    source_document_id,
    document: ConnectorDocument,
) -> dict[str, int]:
    result = normalize_market_standard_document(
        document,
        security_id=str(security_id),
        currency=_security_meta(document.ticker).currency,
    )
    adjusted_count = 0
    metric_count = 0
    if result.adjusted_record is not None:
        trace = result.adjusted_record.source_trace.model_dump(mode="json") | {
            "source_document_id": str(source_document_id),
        }
        record = result.adjusted_record.model_copy(update={"source_trace": SourceTrace(**trace)})
        repo.store_adjusted_earnings(security_id, source_document_id, record)
        adjusted_count += 1
    for metric in result.metrics:
        trace = metric.source_trace | {"source_document_id": str(source_document_id)}
        repo.store_metric_value(
            security_id=security_id,
            source_document_id=source_document_id,
            metric_key=metric.metric_key,
            fiscal_year=metric.fiscal_year,
            value=metric.value,
            unit=metric.unit,
            currency=metric.currency,
            formula=metric.formula,
            method=metric.method,
            quality_status=metric.quality_status,
            source_trace=trace,
        )
        metric_count += 1
    return {"adjusted_earnings": adjusted_count, "metric_values": metric_count}


def _persist_normalization(
    ticker: str,
    source_documents: list[SourceDocument],
    records: list,
) -> dict[str, Any]:
    repo = IngestionRepository()
    run_id = repo.start_run(market="US", source="sec_adjusted_normalize", ticker=ticker)
    queue = BlobUploadQueue()
    meta = _security_meta(ticker)
    security = repo.ensure_security(
        ticker.upper(),
        meta.name,
        meta.country,
        meta.currency,
        meta.exchange,
    )
    source_ids: dict[str, Any] = {}
    try:
        for document in source_documents:
            content_hash = document.content_hash or _document_hash(document)
            source_document_id = repo.store_source_document(security.id, document, "sec_edgar")
            source_ids[content_hash] = source_document_id
            source_ids[document.id] = source_document_id
            blob_key = f"raw/sec_edgar/{ticker.upper()}/{content_hash}.html"
            if document.local_path:
                queue.enqueue(
                    BlobQueueItem(
                        local_path=document.local_path,
                        blob_key=blob_key,
                        content_type="text/html",
                        metadata=document.metadata,
                    )
                )
            repo.store_raw_object(
                ingestion_run_id=run_id,
                source_document_id=source_document_id,
                market="US",
                source="sec_edgar",
                ticker=ticker,
                identifier=document.accession_number or document.id,
                source_url=document.source_url,
                local_path=document.local_path,
                content_hash=content_hash,
                content_type="text/html",
                metadata=document.metadata,
                blob_key=blob_key,
            )
        for record in records:
            key = record.source_trace.source_document_id or record.source_trace.table_hash
            source_document_id = source_ids.get(key) or next(iter(source_ids.values()), None)
            adjusted_earnings_id = repo.store_adjusted_earnings(
                security.id,
                source_document_id,
                record,
            )
            repo.delete_adjustments_for(adjusted_earnings_id)
            for adjustment in record.adjustments:
                repo.store_adjustment(
                    security.id,
                    source_document_id,
                    adjusted_earnings_id,
                    adjustment,
                )
        repo.finish_run(run_id)
    except Exception as exc:
        repo.finish_run(run_id, status="failed", error_summary=str(exc))
        raise
    return {"adjusted_earnings": len(records), "source_documents": len(source_ids)}


def _read_tabular_export(path: Path, *, sheet: str | None = None) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return list(csv.DictReader(path.open(encoding="utf-8-sig")))
    if suffix in {".xlsx", ".xlsm", ".xls"}:
        try:
            import pandas as pd
        except ImportError as exc:
            raise RuntimeError(
                "pandas is required to read FnGuide/DataGuide Excel exports"
            ) from exc
        frame = pd.read_excel(path, sheet_name=sheet or 0, dtype=str)
        frame = frame.where(frame.notna(), "")
        return [dict(row) for row in frame.to_dict(orient="records")]
    raise ValueError("FnGuide/DataGuide export must be a CSV or Excel file")


def _fnguide_metric_rows(
    rows: list[dict[str, Any]],
    *,
    sheet: str | None = None,
) -> tuple[list[FnguideMetricRow], int]:
    if not rows:
        return [], 0
    lookup = _column_lookup(rows[0], FNGUIDE_COLUMN_ALIASES)
    required = {"ticker", "fiscal_year", "metric_key", "value"}
    if not required <= set(lookup):
        missing = sorted(required - set(lookup))
        raise ValueError(f"missing required FnGuide/DataGuide columns: {', '.join(missing)}")
    metric_rows: list[FnguideMetricRow] = []
    skipped = 0
    for row_number, row in enumerate(rows, start=2):
        raw_value = _cell(row, lookup["value"])
        if not raw_value:
            skipped += 1
            continue
        try:
            value = _parse_decimal_value(raw_value)
            if value is None:
                skipped += 1
                continue
            ticker = _canonical_kr_ticker(_cell(row, lookup["ticker"]))
            label = _cell(row, lookup["metric_key"])
            currency = _optional_text(_cell(row, lookup.get("currency"))) or "KRW"
            currency = currency.upper()
            if not re.fullmatch(r"[A-Z]{3}", currency):
                raise ValueError("currency must be a 3-letter ISO code")
            metric_rows.append(
                FnguideMetricRow(
                    ticker=ticker,
                    name=_optional_text(_cell(row, lookup.get("name"))),
                    fiscal_year=_parse_fiscal_year(_cell(row, lookup["fiscal_year"])),
                    fiscal_period=_optional_text(_cell(row, lookup.get("fiscal_period"))) or "FY",
                    metric_key=_fnguide_metric_key(label),
                    metric_label=label,
                    value=value,
                    raw_value=_cell(row, lookup["value"]),
                    unit=(
                        _optional_text(_cell(row, lookup.get("unit")))
                        or _default_metric_unit(label)
                    ),
                    currency=currency,
                    row_number=row_number,
                    source_sheet=sheet,
                )
            )
        except (ArithmeticError, ValueError) as exc:
            raise ValueError(
                f"row {row_number}: invalid FnGuide/DataGuide metric row: {exc}"
            ) from exc
    return metric_rows, skipped


def _column_lookup(row: dict[str, Any], aliases: dict[str, set[str]]) -> dict[str, str]:
    normalized = {_normalize_header(key): key for key in row}
    lookup: dict[str, str] = {}
    for canonical, candidates in aliases.items():
        for candidate in candidates:
            column = normalized.get(_normalize_header(candidate))
            if column is not None:
                lookup[canonical] = column
                break
    return lookup


def _cell(row: dict[str, Any], key: str | None) -> str:
    if not key:
        return ""
    value = row.get(key)
    return "" if value is None else str(value).strip()


def _optional_text(value: str) -> str | None:
    stripped = value.strip()
    return stripped or None


def _normalize_header(value: str) -> str:
    return re.sub(r"[\s_\-./()]+", "", str(value).strip().lower())


def _canonical_kr_ticker(value: str) -> str:
    stripped = value.strip().upper()
    if not stripped:
        raise ValueError("ticker is required")
    if "." in stripped:
        code = stripped.split(".", 1)[0]
    else:
        code = stripped
    digits = re.sub(r"\D", "", code)
    if not digits:
        raise ValueError(f"KR ticker must contain a 6-digit code, got {value!r}")
    code = digits.zfill(6)
    if len(code) != 6:
        raise ValueError(f"KR ticker must normalize to a 6-digit code, got {value!r}")
    return f"{code}.KS"


def _parse_fiscal_year(value: str) -> int:
    match = re.search(r"(19|20)\d{2}", value)
    if not match:
        raise ValueError(f"fiscal_year is not parseable: {value!r}")
    return int(match.group(0))


def _parse_decimal_value(value: str) -> Decimal | None:
    stripped = value.strip()
    if stripped in {"", "-", "—", "N/A", "NA", "nan", "None"}:
        return None
    negative = stripped.startswith("(") and stripped.endswith(")")
    normalized = stripped.strip("()").replace(",", "").replace("%", "").replace("₩", "")
    normalized = normalized.replace("$", "").strip()
    if normalized in {"", "-"}:
        return None
    amount = Decimal(normalized)
    return -amount if negative else amount


def _decimal_from_any(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return _parse_decimal_value(str(value))
    except (ArithmeticError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(str(value)[:4])
    except ValueError:
        return None


def _date_or_none(value: Any) -> date | None:
    if value in {None, ""}:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _first_present(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in {None, ""}:
            return value
    return None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in {"", "nan", "nat", "none", "<na>"}:
        return None
    return text


def _marcap_code_from_ticker(ticker: str) -> str:
    value = ticker.strip().upper()
    if "." in value:
        value = value.split(".", 1)[0]
    return _normalize_marcap_code(value)


def _normalize_marcap_code(value: Any) -> str:
    text = _string_or_none(value)
    if not text:
        return ""
    if "." in text:
        text = text.split(".", 1)[0]
    digits = re.sub(r"\D", "", text)
    return digits.zfill(6) if digits else ""


def _fnguide_metric_key(label: str) -> str:
    normalized = _normalize_header(label)
    if normalized in FNGUIDE_METRIC_ALIASES:
        return FNGUIDE_METRIC_ALIASES[normalized]
    compact_label = label.strip()
    if compact_label in FNGUIDE_METRIC_ALIASES:
        return FNGUIDE_METRIC_ALIASES[compact_label]
    key = re.sub(r"[^0-9A-Za-z가-힣]+", "_", compact_label).strip("_").lower()
    if not key:
        raise ValueError("metric label is required")
    return key


def _default_metric_unit(label: str) -> str:
    key = _fnguide_metric_key(label)
    if key in {"reported_eps", "reported_eps_diluted", "book_value_per_share"}:
        return "per_share"
    if key in {"roe", "roic", "debt_to_equity", "dividend_yield"}:
        return "percent"
    return "raw"


def _write_raw_document(document: ConnectorDocument) -> tuple[Path, str]:
    digest = hashlib.sha256(document.payload).hexdigest()
    suffix = _suffix_for(document.content_type)
    path = (
        Path("storage/raw")
        / document.source
        / document.ticker.upper()
        / f"{document.identifier}-{digest[:12]}{suffix}"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(document.payload)
    return path, digest


def _cache_raw_documents(documents: list[ConnectorDocument]) -> list[dict[str, str]]:
    cached: list[dict[str, str]] = []
    for document in documents:
        local_path, digest = _write_raw_document(document)
        cached.append(
            {
                "source": document.source,
                "ticker": document.ticker,
                "identifier": document.identifier,
                "local_path": str(local_path),
                "content_hash": digest,
            }
        )
    return cached


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _latest_sec_bulk_zip(archive: str) -> Path:
    matches = sorted(
        Path("storage/raw/sec_bulk/BULK").glob(f"sec-bulk-{archive}-*.zip"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    if not matches:
        raise FileNotFoundError(
            f"SEC bulk {archive} archive not found. Run collect-sec-bulk first "
            f"or pass --{archive}-zip explicitly."
        )
    return matches[0]


def _document_hash(document: SourceDocument) -> str:
    if document.local_path and Path(document.local_path).exists():
        return hashlib.sha256(Path(document.local_path).read_bytes()).hexdigest()
    return hashlib.sha256((document.content or document.id).encode("utf-8")).hexdigest()


def _blob_key(document: ConnectorDocument, digest: str) -> str:
    suffix = _suffix_for(document.content_type)
    return f"raw/{document.source}/{document.ticker.upper()}/{digest}{suffix}"


def _suffix_for(content_type: str) -> str:
    if "json" in content_type:
        return ".json"
    if "html" in content_type:
        return ".html"
    if "xml" in content_type:
        return ".xml"
    if "csv" in content_type:
        return ".csv"
    if "parquet" in content_type:
        return ".parquet"
    if "zip" in content_type:
        return ".zip"
    if "spreadsheet" in content_type or "excel" in content_type:
        return ".xlsx"
    return ".bin"


def _content_type_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return "text/csv"
    if suffix in {".xlsx", ".xlsm", ".xls"}:
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return "application/octet-stream"


def _is_binary_tabular(path: Path) -> bool:
    return path.suffix.lower() in {".xlsx", ".xlsm", ".xls"}


def _security_meta(ticker: str, fallback_currency: str = "USD") -> SecurityMeta:
    return SEED_SECURITY_META.get(
        ticker.upper(),
        SecurityMeta(
            ticker=ticker.upper(),
            name=ticker.upper(),
            country="US",
            currency=fallback_currency,
            exchange=None,
        ),
    )


def _document_summary(document: ConnectorDocument) -> dict[str, Any]:
    return {
        "source": document.source,
        "market": document.market,
        "ticker": document.ticker,
        "identifier": document.identifier,
        "url": document.url,
        "content_type": document.content_type,
        "bytes": len(document.payload),
        "metadata": document.metadata,
    }


def _json_payload_row_count(document: ConnectorDocument) -> int:
    try:
        payload = json.loads(document.payload.decode("utf-8"))
    except json.JSONDecodeError:
        return 0
    if isinstance(payload, list):
        return len(payload)
    if not isinstance(payload, dict):
        return 0
    ecos_rows = payload.get("StatisticSearch")
    if isinstance(ecos_rows, dict) and isinstance(ecos_rows.get("row"), list):
        return len(ecos_rows["row"])
    estat_data = payload.get("GET_STATS_DATA")
    if isinstance(estat_data, dict):
        values = (
            estat_data.get("STATISTICAL_DATA", {})
            .get("DATA_INF", {})
            .get("VALUE", [])
        )
        if isinstance(values, list):
            return len(values)
    for key in ("data", "rows", "result", "results", "observations"):
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
    return 0


def _fred_observation_count(document: ConnectorDocument) -> int:
    payload = json.loads(document.payload.decode("utf-8"))
    return len(payload.get("observations") or [])


def _stooq_price_row_count(document: ConnectorDocument) -> int:
    return len(_stooq_csv_rows(document))


def _fdr_price_row_count(document: ConnectorDocument) -> int:
    return len(_fdr_csv_rows(document))


def _pykrx_price_row_count(document: ConnectorDocument) -> int:
    return len(_pykrx_csv_rows(document))


def _pykrx_fundamental_row_count(document: ConnectorDocument) -> int:
    return len(_pykrx_fundamental_csv_rows(document))


def _opendart_dividend_row_count(document: ConnectorDocument) -> int:
    return len(_opendart_dividend_candidates(document))


def _opendart_dividend_per_share(
    document: ConnectorDocument,
) -> tuple[Decimal | None, dict[str, Any] | None]:
    candidates = _opendart_dividend_candidates(document)
    if not candidates:
        return None, None
    candidates.sort(key=lambda item: _opendart_dividend_candidate_rank(item[1]))
    return candidates[0]


def _opendart_dividend_candidates(
    document: ConnectorDocument,
) -> list[tuple[Decimal, dict[str, Any]]]:
    payload = _json_payload_from_bytes(document.payload)
    rows = payload.get("list")
    if not isinstance(rows, list):
        return []
    candidates: list[tuple[Decimal, dict[str, Any]]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = str(
            _first_present(row, "se", "account_nm", "account_name", "sj_div", "label")
            or ""
        )
        if not _looks_like_opendart_dps_label(label):
            continue
        raw_amount = _first_present(row, "thstrm", "thstrm_amount", "thstrm_value", "current", "value")
        amount = _decimal_from_any(raw_amount)
        zero_assumption = None
        if amount is None and _is_opendart_dash_no_cash_dividend(raw_amount, payload):
            amount = Decimal("0")
            zero_assumption = "opendart_dash_no_cash_dividend"
        if amount is None:
            continue
        evidence = dict(row)
        evidence.update(
            {
                "matched_label": label,
                "matched_metric": "dividend_per_share",
                "opendart_status": payload.get("status"),
                "opendart_message": payload.get("message"),
                "raw_amount": raw_amount,
            }
        )
        if zero_assumption:
            evidence["dividend_zero_assumption"] = zero_assumption
        candidates.append((amount, evidence))
    return candidates


def _is_opendart_dash_no_cash_dividend(value: Any, payload: dict[str, Any]) -> bool:
    if str(payload.get("status") or "").strip() != "000":
        return False
    marker = str(value or "").strip()
    return marker in {"-", "\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\uff0d", "\u2212"}


def _looks_like_opendart_dps_label(label: str) -> bool:
    compact = re.sub(r"\s+", "", label).lower()
    if not compact:
        return False
    has_per_share = "\uc8fc\ub2f9" in compact or "pershare" in compact or "dps" in compact
    has_dividend = "\ubc30\ub2f9" in compact or "dividend" in compact
    has_yield = "\uc218\uc775\ub960" in compact or "yield" in compact or "ratio" in compact
    return has_per_share and has_dividend and not has_yield


def _opendart_dividend_candidate_rank(evidence: dict[str, Any]) -> int:
    stock_kind = str(_first_present(evidence, "stock_knd", "stock_kind", "kind") or "").lower()
    label = str(evidence.get("matched_label") or "").lower()
    combined = f"{stock_kind} {label}"
    if "\ubcf4\ud1b5" in combined or "common" in combined:
        return 0
    if "\uc6b0\uc120" in combined or "preferred" in combined:
        return 2
    return 1


def _marcap_price_row_count(
    document: ConnectorDocument,
    tickers: str | list[str] | set[str] | None = None,
) -> int:
    if isinstance(tickers, set):
        ticker_values = list(tickers)
    elif tickers:
        ticker_values = _split_csv(tickers)
    else:
        ticker_values = []
    ticker_filter = {_marcap_code_from_ticker(ticker) for ticker in ticker_values}
    return len(_marcap_rows(document, ticker_filter))


def _jquants_daily_quote_row_count(document: ConnectorDocument) -> int:
    return len(_jquants_daily_quote_rows(document))


def _jquants_dividend_row_count(document: ConnectorDocument) -> int:
    return len(_jquants_dividend_rows(document))


def _jquants_statement_row_count(document: ConnectorDocument) -> int:
    return len(_jquants_statement_rows(document))


def _stooq_csv_rows(document: ConnectorDocument) -> list[dict[str, str]]:
    text = document.payload.decode("utf-8-sig")
    rows = [
        row
        for row in csv.DictReader(io.StringIO(text))
        if row.get("Date") and row.get("Close") not in {None, "", "0"}
    ]
    required = {"Date", "Open", "High", "Low", "Close"}
    if rows and not required <= set(rows[0]):
        missing = sorted(required - set(rows[0]))
        raise ValueError(f"missing required Stooq CSV columns: {', '.join(missing)}")
    return rows


def _fdr_csv_rows(document: ConnectorDocument) -> list[dict[str, str]]:
    text = document.payload.decode("utf-8-sig")
    rows = [
        row
        for row in csv.DictReader(io.StringIO(text))
        if _first_present(row, "Date", "date") and _first_present(row, "Close", "close")
    ]
    if not rows:
        return []
    return rows


def _pykrx_csv_rows(document: ConnectorDocument) -> list[dict[str, str]]:
    if _is_pykrx_fundamental_document(document):
        return []
    text = document.payload.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, str]] = []
    for raw_row in reader:
        normalized = _normalize_pykrx_csv_row(raw_row)
        if normalized["date"] and normalized["close"] not in {"", "0"}:
            rows.append(normalized)
    if not rows:
        return []
    missing = [
        column
        for column in ("date", "close")
        if column not in rows[0] or not rows[0][column]
    ]
    if missing:
        raise ValueError(f"missing required pykrx CSV columns: {', '.join(missing)}")
    return rows


def _pykrx_fundamental_csv_rows(document: ConnectorDocument) -> list[dict[str, str]]:
    if not _is_pykrx_fundamental_document(document):
        return []
    text = document.payload.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, str]] = []
    for raw_row in reader:
        normalized = _normalize_pykrx_fundamental_csv_row(raw_row)
        if normalized["date"] and normalized["dps"] not in {"", "nan", "NaN"}:
            rows.append(normalized)
    return rows


def _normalize_pykrx_csv_row(row: dict[str, str]) -> dict[str, str]:
    return {
        "date": _row_value(row, "date", "날짜"),
        "open": _row_value(row, "open", "Open", "시가"),
        "high": _row_value(row, "high", "High", "고가"),
        "low": _row_value(row, "low", "Low", "저가"),
        "close": _row_value(row, "close", "Close", "종가"),
        "volume": _row_value(row, "volume", "Volume", "거래량"),
        "value_traded": _row_value(row, "value_traded", "Value", "거래대금"),
        "change_pct": _row_value(row, "change_pct", "Change", "등락률"),
    }


def _normalize_pykrx_fundamental_csv_row(row: dict[str, str]) -> dict[str, str]:
    return {
        "date": _row_value(row, "date", "Date", "날짜"),
        "bps": _row_value(row, "bps", "BPS"),
        "per": _row_value(row, "per", "PER"),
        "pbr": _row_value(row, "pbr", "PBR"),
        "eps": _row_value(row, "eps", "EPS"),
        "dps": _row_value(row, "dps", "DPS", "배당", "배당금"),
        "dividend_yield": _row_value(row, "dividend_yield", "DIV", "Div", "배당수익률"),
    }


def _is_pykrx_fundamental_document(document: ConnectorDocument) -> bool:
    endpoint = str(document.metadata.get("endpoint") or "").lower()
    identifier = document.identifier.lower()
    return "fundamental" in endpoint or "fundamental" in identifier


def _marcap_rows(
    document: ConnectorDocument,
    ticker_filter: set[str] | None = None,
) -> list[dict[str, Any]]:
    import pandas as pd

    frame = pd.read_parquet(io.BytesIO(document.payload))
    if "Date" not in frame.columns:
        frame = frame.reset_index()
    required = {"Date", "Code", "Close"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"missing required marcap parquet columns: {', '.join(missing)}")
    frame["Code"] = frame["Code"].map(_normalize_marcap_code)
    if ticker_filter:
        frame = frame[frame["Code"].isin(ticker_filter)]
    if frame.empty:
        return []
    frame = frame[frame["Close"].notna()]
    return frame.to_dict("records")


def _jquants_daily_quote_rows(document: ConnectorDocument) -> list[dict[str, Any]]:
    if document.metadata.get("endpoint") not in {None, "/prices/daily_quotes"}:
        return []
    return _jquants_rows(document, "daily_quotes", "DailyQuotes", "quotes")


def _jquants_dividend_rows(document: ConnectorDocument) -> list[dict[str, Any]]:
    if document.metadata.get("endpoint") not in {None, "/fins/dividend"}:
        return []
    return _jquants_rows(document, "dividend", "dividends", "Dividend")


def _jquants_statement_rows(document: ConnectorDocument) -> list[dict[str, Any]]:
    if document.metadata.get("endpoint") not in {None, "/fins/statements"}:
        return []
    return _jquants_rows(document, "statements", "Statements")


def _jquants_rows(document: ConnectorDocument, *keys: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(document.payload.decode("utf-8"))
    except json.JSONDecodeError:
        return []
    for key in keys:
        rows = payload.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    payload_key = document.metadata.get("payload_key")
    rows = payload.get(str(payload_key)) if payload_key else None
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    return []


def _jquants_form_type(endpoint: str) -> str:
    if endpoint == "/prices/daily_quotes":
        return "JQUANTS_DAILY_QUOTES"
    if endpoint == "/fins/dividend":
        return "JQUANTS_DIVIDEND"
    if endpoint == "/fins/statements":
        return "JQUANTS_STATEMENTS"
    return "JQUANTS_JSON"


def _edinet_document_type(document: ConnectorDocument) -> str:
    return str(document.metadata.get("document_type") or "unknown")


def _edinet_form_type(document_type: str) -> str:
    if document_type == "metadata_list":
        return "EDINET_METADATA_LIST"
    if document_type == "xbrl_to_csv_zip":
        return "EDINET_XBRL_TO_CSV_ZIP"
    if document_type == "xbrl_zip":
        return "EDINET_XBRL_ZIP"
    return "EDINET_DOCUMENT"


def _sec_bulk_form_type(archive: str) -> str:
    if archive == "companyfacts":
        return "SEC_COMPANYFACTS_BULK_ZIP"
    if archive == "submissions":
        return "SEC_SUBMISSIONS_BULK_ZIP"
    return "SEC_BULK_ZIP"


def _edinet_source_document_content(document: ConnectorDocument) -> str | None:
    if _edinet_document_type(document) == "metadata_list":
        return document.payload.decode("utf-8", errors="ignore")
    return json.dumps(document.metadata, ensure_ascii=False, sort_keys=True)


def _row_value(row: dict[str, str], *candidates: str) -> str:
    lowered = {key.lower(): value for key, value in row.items()}
    for candidate in candidates:
        if candidate in row and row[candidate] is not None:
            return str(row[candidate]).strip()
        value = lowered.get(candidate.lower())
        if value is not None:
            return str(value).strip()
    return ""


def _fred_decimal(raw: Any) -> Decimal | None:
    if raw is None:
        return None
    value = str(raw).strip()
    if value in {"", "."}:
        return None
    return Decimal(value)


def _fred_recession_periods(observations: list[dict[str, Any]]) -> list[tuple[date, date | None]]:
    periods: list[tuple[date, date | None]] = []
    in_period = False
    start_date: date | None = None
    last_recession_date: date | None = None
    for observation in sorted(observations, key=lambda row: str(row.get("date"))):
        value = _fred_decimal(observation.get("value"))
        observation_date = date.fromisoformat(str(observation["date"]))
        is_recession = value == Decimal("1")
        if is_recession and not in_period:
            in_period = True
            start_date = observation_date
        if is_recession:
            last_recession_date = observation_date
        if not is_recession and in_period and start_date is not None:
            periods.append((start_date, last_recession_date))
            in_period = False
            start_date = None
            last_recession_date = None
    if in_period and start_date is not None:
        periods.append((start_date, None))
    return periods


def _print_json(payload: dict[str, Any], *, summary_only: bool = False) -> None:
    if summary_only:
        payload = _e2e_output_summary(payload)
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def _e2e_output_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Return an operator-readable E2E summary without raw payload noise."""
    if "market_results" in summary:
        return {
            "status": summary.get("status"),
            "scope": summary.get("scope"),
            "market_order": summary.get("market_order"),
            "years": summary.get("years"),
            "policy": summary.get("policy"),
            "persist": summary.get("persist"),
            "dry_run": summary.get("dry_run"),
            "tickers": summary.get("tickers", []),
            "missing_required": summary.get("missing_required", []),
            "executed_markets": summary.get("executed_markets", []),
            "failed": _summarize_failures(summary.get("failed", [])),
            "market_results": [
                _e2e_output_summary(row)
                for row in summary.get("market_results", [])
                if isinstance(row, dict)
            ],
            "coverage": _coverage_status(summary.get("coverage")),
            "completion_gate": _summarize_completion_gate(summary.get("completion_gate")),
        }

    return {
        "status": summary.get("status"),
        "market": summary.get("market"),
        "tickers": summary.get("tickers", []),
        "years": summary.get("years"),
        "policy": summary.get("policy"),
        "persist": summary.get("persist"),
        "dry_run": summary.get("dry_run"),
        "missing_required": summary.get("missing_required", []),
        "executed_steps": summary.get("executed_steps", []),
        "failed": _summarize_failures(summary.get("failed", [])),
        "local_raw_evidence": _summarize_local_raw_evidence(
            summary.get("local_raw_evidence")
        ),
        "coverage": _coverage_status(summary.get("coverage")),
        "next_actions": _summarize_actions(
            (summary.get("local_raw_evidence") or {}).get("next_actions", [])
        ),
        "completion_gate": _summarize_completion_gate(summary.get("completion_gate")),
    }


def _summarize_local_raw_evidence(raw_payload: Any) -> dict[str, Any] | None:
    if not isinstance(raw_payload, dict):
        return None
    raw_evidence = raw_payload.get("raw_evidence")
    raw_summary = raw_evidence.get("summary", {}) if isinstance(raw_evidence, dict) else {}
    return {
        "status": raw_payload.get("status"),
        "mode": raw_payload.get("mode"),
        "summary": raw_summary,
        "missing": raw_summary.get("missing", []),
        "tickers": _summarize_evidence_tickers(
            raw_evidence.get("tickers", []) if isinstance(raw_evidence, dict) else []
        ),
    }


def _summarize_evidence_tickers(tickers: Any) -> list[dict[str, Any]]:
    if not isinstance(tickers, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in tickers:
        if not isinstance(item, dict):
            continue
        checks = item.get("checks", [])
        failed_required = [
            check.get("name")
            for check in checks
            if isinstance(check, dict) and check.get("required") and not check.get("ok")
        ]
        rows.append(
            {
                "ticker": item.get("ticker"),
                "status": item.get("status"),
                "valuation_ready": item.get("valuation_ready"),
                "missing_years": _summarize_missing_years(item.get("missing_years", {})),
                "failed_required_checks": failed_required,
            }
        )
    return rows


def _summarize_missing_years(missing_years: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(missing_years, dict):
        return {}
    summarized: dict[str, dict[str, Any]] = {}
    for source, years in missing_years.items():
        if not isinstance(years, list) or not years:
            continue
        sorted_years = sorted(int(year) for year in years)
        summarized[str(source)] = {
            "count": len(sorted_years),
            "start": sorted_years[0],
            "end": sorted_years[-1],
        }
    return summarized


def _coverage_status(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    return {
        "status": payload.get("status"),
        "summary": payload.get("summary"),
        "missing_core": (payload.get("summary") or {}).get("missing_core")
        if isinstance(payload.get("summary"), dict)
        else None,
    }


def _summarize_actions(actions: Any) -> list[dict[str, Any]]:
    if not isinstance(actions, list):
        return []
    summarized: list[dict[str, Any]] = []
    for action in actions[:20]:
        if not isinstance(action, dict):
            continue
        summarized.append(
            {
                "id": action.get("id"),
                "ticker": action.get("ticker"),
                "command": action.get("command"),
                "reason": action.get("reason"),
            }
        )
    return summarized


def _summarize_completion_gate(gate: Any) -> dict[str, Any] | None:
    if not isinstance(gate, dict):
        return None
    return {
        "status": gate.get("status"),
        "market": gate.get("market"),
        "tickers": gate.get("tickers", []),
        "years": gate.get("years"),
        "coverage_status": gate.get("coverage_status"),
        "local_raw_ready": gate.get("local_raw_ready"),
        "missing_required": gate.get("missing_required", []),
        "required_proofs": gate.get("required_proofs", []),
        "next_commands": _summarize_gate_commands(gate.get("next_commands", [])),
        "deployment_commands": _summarize_gate_commands(gate.get("deployment_commands", [])),
    }


def _summarize_gate_commands(commands: Any) -> list[dict[str, Any]]:
    if not isinstance(commands, list):
        return []
    rows: list[dict[str, Any]] = []
    for command in commands[:10]:
        if not isinstance(command, dict):
            continue
        rows.append(
            {
                "id": command.get("id"),
                "command": command.get("command"),
                "requires": command.get("requires"),
                "proves": command.get("proves"),
            }
        )
    return rows


def _summarize_failures(failures: Any) -> list[dict[str, Any]]:
    if not isinstance(failures, list):
        return []
    summarized: list[dict[str, Any]] = []
    for failure in failures[:20]:
        if not isinstance(failure, dict):
            continue
        summarized.append(
            {
                "id": failure.get("id"),
                "market": failure.get("market"),
                "ticker": failure.get("ticker"),
                "status": failure.get("status"),
                "error_type": failure.get("error_type"),
                "error": failure.get("error"),
                "missing_required": failure.get("missing_required"),
            }
        )
    return summarized


def _split_csv(value: str | list[str]) -> list[str]:
    values = value.split(",") if isinstance(value, str) else value
    return [str(item).strip().upper() for item in values if str(item).strip()]


def _split_identifiers(value: str | list[str]) -> list[str]:
    values = value.split(",") if isinstance(value, str) else value
    return [str(item).strip() for item in values if str(item).strip()]


def _currency_for_market(market: str) -> str:
    return {"US": "USD", "JP": "JPY", "KR": "KRW"}.get(market.upper(), "USD")


def _currency_for_ticker(ticker: str) -> str:
    value = ticker.upper()
    if value.endswith(".KS") or value.endswith(".KQ"):
        return "KRW"
    if value.endswith(".T"):
        return "JPY"
    return "USD"


def _country_for_market(market: str) -> str:
    return {"US": "US", "JP": "JP", "KR": "KR"}.get(market.upper(), "US")


def _parse_years(value: str) -> tuple[int, int]:
    if ":" in value:
        start, end = value.split(":", 1)
        return int(start), int(end)
    year = int(value)
    return year, year


def _normalize_estimate_case(raw: str) -> str:
    value = raw.strip().lower().replace("-", "_")
    if value in {"low", "bear", "pessimistic"}:
        return "low"
    if value in {"high", "bull", "optimistic"}:
        return "high"
    if value in {"current", "point", "actual_current"}:
        return "current"
    return "median"


def _normalize_consensus_import_case(raw: str, row_number: int) -> str:
    value = raw.strip().lower().replace("-", "_")
    case_map = {
        "low": "low",
        "bear": "low",
        "pessimistic": "low",
        "median": "median",
        "base": "median",
        "mean": "median",
        "current": "current",
        "point": "current",
        "actual_current": "current",
        "high": "high",
        "bull": "high",
        "optimistic": "high",
    }
    if value not in case_map:
        raise ValueError(
            f"row {row_number}: estimate_case must be low, median, high, or current"
        )
    return case_map[value]


def _normalize_consensus_template_cases(raw: str) -> list[str]:
    cases: list[str] = []
    for index, item in enumerate(raw.split(","), start=1):
        if not item.strip():
            continue
        normalized = _normalize_consensus_import_case(item, index)
        if normalized not in cases:
            cases.append(normalized)
    if not cases:
        raise ValueError("at least one estimate case is required")
    return cases


def _required_date(raw: str | None, field: str, row_number: int) -> date:
    try:
        return date.fromisoformat((raw or "").strip())
    except ValueError as exc:
        raise ValueError(f"row {row_number}: {field} must be an ISO date") from exc


def _required_decimal(raw: str | None, field: str, row_number: int) -> Decimal:
    try:
        return Decimal((raw or "").strip())
    except Exception as exc:
        raise ValueError(f"row {row_number}: {field} must be a decimal number") from exc


def _required_int(raw: str | None, field: str, row_number: int) -> int:
    try:
        return int((raw or "").strip())
    except ValueError as exc:
        raise ValueError(f"row {row_number}: {field} must be an integer") from exc


def _optional_decimal(raw: str | None) -> Decimal | None:
    if raw is None or raw.strip() == "":
        return None
    return Decimal(raw)


def _optional_int(raw: str | None) -> int | None:
    if raw is None or raw.strip() == "":
        return None
    return int(raw)


if __name__ == "__main__":
    main()
