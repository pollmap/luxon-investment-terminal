from __future__ import annotations

from typing import Any

SOURCE_CATALOG: list[dict[str, Any]] = [
    {
        "id": "sec_bulk_companyfacts",
        "name": "SEC EDGAR Bulk Company Facts",
        "markets": ["US"],
        "lane": "official_open_bulk",
        "priority": 1,
        "coverage": ["xbrl_companyfacts", "submissions", "filing_history"],
        "bulk_strategy": "nightly_zip_to_raw_then_normalize",
        "raw_prefix": "storage/raw/sec_bulk/BULK/",
        "warehouse_targets": ["source_documents", "financial_facts", "metric_values"],
        "notes": (
            "Primary US fundamentals lake. Collects SEC companyfacts.zip and "
            "submissions.zip raw archives with a SEC-compliant User-Agent."
        ),
        "url": "https://www.sec.gov/search-filings/edgar-application-programming-interfaces",
    },
    {
        "id": "sec_8k_exhibits",
        "name": "SEC 8-K Item 2.02 Exhibit 99.1",
        "markets": ["US"],
        "lane": "official_open_filing",
        "priority": 1,
        "coverage": ["earnings_release", "non_gaap_reconciliation", "adjusted_eps"],
        "bulk_strategy": "ticker_universe_exhibit_fetch_to_raw_then_parse",
        "raw_prefix": "storage/raw/sec/exhibits/",
        "warehouse_targets": ["source_documents", "adjustments", "adjusted_earnings"],
        "notes": "Best path for company-reported adjusted EPS; parser failures must be retained.",
        "url": "https://www.sec.gov/search-filings/edgar-application-programming-interfaces",
    },
    {
        "id": "opendart_xbrl",
        "name": "OpenDART Financial Statements and XBRL",
        "markets": ["KR"],
        "lane": "official_open_api",
        "priority": 1,
        "coverage": ["financial_statements", "xbrl_raw", "operating_profit", "eps"],
        "bulk_strategy": "corp_code_universe_by_year_report_to_raw_json_xbrl",
        "raw_prefix": "storage/raw/opendart/",
        "warehouse_targets": ["source_documents", "financial_facts", "metric_values"],
        "notes": (
            "Primary KR filing-based fundamentals. Requires OPENDART_API_KEY; "
            "DART_API_KEY is accepted as an alias."
        ),
        "url": "https://engopendart.fss.or.kr/intro/main.do",
    },
    {
        "id": "krx_public_prices",
        "name": "Korea Public Data Portal KRX Stock Price API",
        "markets": ["KR"],
        "lane": "official_open_api",
        "priority": 1,
        "coverage": ["daily_price", "volume", "market_cap", "listed_shares"],
        "bulk_strategy": "date_partitioned_market_download_to_price_bars",
        "raw_prefix": "storage/raw/krx_public/",
        "warehouse_targets": ["securities", "price_bars"],
        "notes": "Official public API for KRX-derived stock price information.",
        "url": "https://www.data.go.kr/en/data/15094808/openapi.do",
    },
    {
        "id": "pykrx",
        "name": "pykrx",
        "markets": ["KR"],
        "lane": "open_source_wrapper",
        "priority": 2,
        "coverage": [
            "ohlcv",
            "fundamentals",
            "market_cap",
            "short_selling",
            "foreign_ownership",
            "etf",
        ],
        "bulk_strategy": "calendar_partitioned_wrapper_fetch_with_raw_snapshot",
        "raw_prefix": "storage/raw/pykrx/",
        "warehouse_targets": ["price_bars", "metric_values", "market_microstructure"],
        "notes": (
            "Useful KR OHLCV and breadth layer; keep raw snapshots because it wraps "
            "upstream web sources."
        ),
        "url": "https://github.com/sharebook-kr/pykrx",
    },
    {
        "id": "marcap_dataset",
        "name": "FinanceData marcap KRX Market Cap Dataset",
        "markets": ["KR"],
        "lane": "free_open_dataset",
        "priority": 2,
        "coverage": [
            "daily_price",
            "market_cap",
            "listed_shares",
            "rank",
            "turnover",
        ],
        "bulk_strategy": "yearly_parquet_archive_to_raw_then_price_bars",
        "raw_prefix": "storage/raw/marcap/",
        "warehouse_targets": ["source_documents", "raw_objects", "price_bars"],
        "notes": (
            "GitHub-published KRX market cap parquet dataset. Preserve yearly "
            "parquet raw files and load filtered seed tickers into price_bars."
        ),
        "url": "https://github.com/financedata/marcap",
    },
    {
        "id": "naver_search_research",
        "name": "Naver OpenAPI Research Link Search",
        "markets": ["KR"],
        "lane": "official_api_key_metadata",
        "priority": 3,
        "coverage": ["research_link_metadata", "company_search_context"],
        "bulk_strategy": "ticker_search_metadata_to_raw_json",
        "raw_prefix": "storage/raw/naver_search_research/",
        "warehouse_targets": ["source_documents", "raw_objects"],
        "notes": (
            "Metadata-only discovery layer for KR report/search links. Requires "
            "NAVER_CLIENT_ID and NAVER_CLIENT_SECRET. Do not promote search snippets "
            "to financial facts or consensus values."
        ),
        "url": "https://developers.naver.com/docs/serviceapi/search/web/web.md",
    },
    {
        "id": "hankyung_consensus_metadata",
        "name": "Hankyung Consensus Research Metadata",
        "markets": ["KR"],
        "lane": "free_web_metadata",
        "priority": 3,
        "coverage": ["research_report_metadata", "consensus_link_metadata"],
        "bulk_strategy": "ticker_report_list_metadata_to_raw_json",
        "raw_prefix": "storage/raw/hankyung_consensus_metadata/",
        "warehouse_targets": ["source_documents", "raw_objects"],
        "notes": (
            "Metadata-only public report-list evidence. Do not extract PDFs, report "
            "bodies, target prices, or estimates without separate rights review."
        ),
        "url": "https://consensus.hankyung.com/",
    },
    {
        "id": "finance_data_reader",
        "name": "FinanceDataReader",
        "markets": ["KR", "US", "JP", "GLOBAL"],
        "lane": "open_source_wrapper",
        "priority": 2,
        "coverage": ["listings", "prices", "indices", "fx", "crypto", "fred_series"],
        "bulk_strategy": "universe_listing_then_symbol_price_download",
        "raw_prefix": "storage/raw/finance_data_reader/",
        "warehouse_targets": ["securities", "price_bars", "macro_series"],
        "notes": (
            "Good bootstrap layer for listings and price history; do not treat as "
            "filing source."
        ),
        "url": "https://github.com/financedata/financedatareader",
    },
    {
        "id": "jquants_api",
        "name": "J-Quants API",
        "markets": ["JP"],
        "lane": "official_api_plan_dependent",
        "priority": 1,
        "coverage": [
            "daily_quotes",
            "financial_statements",
            "listed_info",
            "dividends",
            "earnings_schedule",
        ],
        "bulk_strategy": "listed_info_universe_then_quotes_statements_dividends",
        "raw_prefix": "storage/raw/jquants/",
        "warehouse_targets": [
            "securities",
            "price_bars",
            "metric_values",
            "dividends",
            "estimates",
        ],
        "notes": (
            "Primary JP API path for individual use; plan coverage and delays must be "
            "recorded."
        ),
        "url": "https://www.jpx.co.jp/english/markets/other-data-services/j-quants-api/index.html",
    },
    {
        "id": "edinet_api",
        "name": "EDINET API v2",
        "markets": ["JP"],
        "lane": "official_open_api",
        "priority": 1,
        "coverage": ["annual_filings", "xbrl", "csv_converted_xbrl", "source_documents"],
        "bulk_strategy": "submission_date_scan_then_xbrl_zip_download",
        "raw_prefix": "storage/raw/edinet/",
        "warehouse_targets": ["source_documents", "financial_facts", "metric_values"],
        "notes": "Primary JP filing evidence. Use with J-Quants for listed info and prices.",
        "url": "https://api.edinet-fsa.go.jp/api/v2/documents.json",
    },
    {
        "id": "fred",
        "name": "FRED API",
        "markets": ["US", "KR", "JP", "GLOBAL"],
        "lane": "official_open_api_key",
        "priority": 2,
        "coverage": ["rates", "inflation", "fx", "macro", "recession"],
        "bulk_strategy": "curated_series_list_to_macro_series",
        "raw_prefix": "storage/raw/fred/",
        "warehouse_targets": ["macro_series", "recession_periods"],
        "notes": "Macro overlay for rates, inflation, FX, recession bands, and factor context.",
        "url": "https://fred.stlouisfed.org/docs/api/fred/overview.html",
    },
    {
        "id": "ecos",
        "name": "Bank of Korea ECOS API",
        "markets": ["KR", "GLOBAL"],
        "lane": "official_open_api_key",
        "priority": 2,
        "coverage": ["rates", "fx", "macro", "monetary", "industry_macro"],
        "bulk_strategy": "operator_curated_stat_code_list_to_raw_json",
        "raw_prefix": "storage/raw/ecos/",
        "warehouse_targets": ["source_documents", "raw_objects", "macro_series", "industry_series"],
        "notes": (
            "KR macro and industry context. Requires ECOS_API_KEY and curated "
            "stat_code:cycle:item_code series ids."
        ),
        "url": "https://ecos.bok.or.kr/api/#/",
    },
    {
        "id": "kosis",
        "name": "KOSIS OpenAPI",
        "markets": ["KR"],
        "lane": "official_open_api_key",
        "priority": 2,
        "coverage": ["population", "industry", "regional_macro", "economic_statistics"],
        "bulk_strategy": "operator_curated_org_tbl_or_user_stats_ids_to_raw_json",
        "raw_prefix": "storage/raw/kosis/",
        "warehouse_targets": ["source_documents", "raw_objects", "macro_series", "industry_series"],
        "notes": (
            "Official KR statistics portal. Requires KOSIS_API_KEY and curated "
            "orgId:tblId or userStatsId table ids."
        ),
        "url": "https://kosis.kr/openapi/devGuide/devGuide_0201List.do",
    },
    {
        "id": "estat",
        "name": "Japan e-Stat API",
        "markets": ["JP"],
        "lane": "official_open_api_key",
        "priority": 2,
        "coverage": ["population", "industry", "prices", "labor", "regional_macro"],
        "bulk_strategy": "operator_curated_stats_data_ids_to_raw_json",
        "raw_prefix": "storage/raw/estat/",
        "warehouse_targets": ["source_documents", "raw_objects", "macro_series", "industry_series"],
        "notes": (
            "Official JP government statistics API. Requires ESTAT_APP_ID and curated "
            "statsDataId values."
        ),
        "url": "https://www.e-stat.go.jp/api/",
    },
    {
        "id": "stooq_bulk_prices",
        "name": "Stooq Historical Market Data",
        "markets": ["US", "JP", "GLOBAL"],
        "lane": "free_web_bulk",
        "priority": 3,
        "coverage": ["daily_prices", "intraday_prices", "indices", "fx"],
        "bulk_strategy": "daily_csv_download_to_raw_then_price_bars",
        "raw_prefix": "storage/raw/stooq/",
        "warehouse_targets": ["price_bars", "macro_series"],
        "notes": (
            "Useful fallback price lake; store raw CSV and source trace, confirm "
            "redistribution constraints before public use."
        ),
        "url": "https://stooq.com/db/h/",
    },
    {
        "id": "fnguide_dataguide",
        "name": "FnGuide DataGuide",
        "markets": ["KR", "JP", "GLOBAL"],
        "lane": "user_supplied_premium_import",
        "priority": 3,
        "coverage": ["financials", "prices", "bonds", "industry_timeseries", "research"],
        "bulk_strategy": "user_exported_excel_csv_to_manual_premium_import",
        "raw_prefix": "storage/raw/fnguide/",
        "warehouse_targets": [
            "source_documents",
            "financial_facts",
            "metric_values",
            "industry_series",
        ],
        "notes": "Do not scrape. Use only licensed/user-exported files with provenance.",
        "url": "https://dataguide.fnguide.com/",
    },
]


def source_catalog_payload(
    markets: str | list[str] | None = None,
    *,
    include_premium: bool = True,
) -> dict[str, Any]:
    requested_markets = _normalize_markets(markets)
    sources = [
        source
        for source in SOURCE_CATALOG
        if (not requested_markets or requested_markets.intersection(source["markets"]))
        and (include_premium or source["lane"] != "user_supplied_premium_import")
    ]
    return {
        "markets": sorted(requested_markets) if requested_markets else ["ALL"],
        "include_premium": include_premium,
        "source_count": len(sources),
        "sources": sources,
    }


def render_source_catalog_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Data Source Catalog",
        "",
        "| Source | Markets | Lane | Priority | Coverage | Raw prefix |",
        "|---|---:|---|---:|---|---|",
    ]
    for source in payload["sources"]:
        lines.append(
            "| {name} | {markets} | {lane} | {priority} | {coverage} | `{raw_prefix}` |".format(
                name=source["name"],
                markets=", ".join(source["markets"]),
                lane=source["lane"],
                priority=source["priority"],
                coverage=", ".join(source["coverage"]),
                raw_prefix=source["raw_prefix"],
            )
        )
    return "\n".join(lines)


def _normalize_markets(markets: str | list[str] | None) -> set[str]:
    if markets is None:
        return set()
    values = markets.split(",") if isinstance(markets, str) else markets
    return {str(value).strip().upper() for value in values if str(value).strip()}
