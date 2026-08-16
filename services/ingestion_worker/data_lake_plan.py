from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from packages.core.universe import KR_TOP_MARKET_CAP_PRIORITY_TICKERS
from services.api.source_coverage import normalize_coverage_tickers
from services.ingestion_worker.source_catalog import SOURCE_CATALOG

DEFAULT_US_TICKERS = ["AAPL", "NVDA", "CRM", "O", "JPM"]
DEFAULT_KR_TICKERS = list(KR_TOP_MARKET_CAP_PRIORITY_TICKERS)
DEFAULT_JP_TICKERS = ["7203.T", "6758.T", "6861.T", "8306.T", "7974.T"]
DEFAULT_FRED_SERIES = [
    "DGS10",
    "DGS2",
    "FEDFUNDS",
    "CPIAUCSL",
    "UNRATE",
    "USREC",
    "DEXKOUS",
    "DEXJPUS",
]


def build_data_lake_plan(
    *,
    markets: str | list[str] = "US,KR,JP",
    years: str = "2020:2025",
    tickers: str | list[str] | None = None,
    include_premium: bool = False,
    include_wrappers: bool = True,
    partition: str = "annual",
) -> dict[str, Any]:
    requested_markets = _normalize_markets(markets)
    start_year, end_year = _parse_years(years)
    ticker_map = _ticker_map(tickers)
    sources = [
        source
        for source in SOURCE_CATALOG
        if requested_markets.intersection(source["markets"])
        and (include_premium or source["lane"] != "user_supplied_premium_import")
        and (include_wrappers or source["lane"] != "open_source_wrapper")
    ]
    jobs: list[dict[str, Any]] = []
    for source in sources:
        jobs.extend(
            _jobs_for_source(
                source,
                requested_markets=requested_markets,
                start_year=start_year,
                end_year=end_year,
                ticker_map=ticker_map,
                partition=partition,
            )
        )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "markets": sorted(requested_markets),
        "years": {"start": start_year, "end": end_year},
        "partition": partition,
        "include_premium": include_premium,
        "include_wrappers": include_wrappers,
        "source_count": len(sources),
        "job_count": len(jobs),
        "storage_policy": {
            "raw": "append_only_content_hash",
            "warehouse": "version_by_source_period_accession_or_snapshot",
            "premium": "user_supplied_files_only",
        },
        "sources": [
            {
                "id": source["id"],
                "name": source["name"],
                "lane": source["lane"],
                "priority": source["priority"],
                "raw_prefix": source["raw_prefix"],
            }
            for source in sources
        ],
        "jobs": jobs,
    }


def write_plan_manifest(plan: dict[str, Any], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return out


def render_data_lake_plan_markdown(plan: dict[str, Any]) -> str:
    lines = [
        "# Data Lake Ingestion Plan",
        "",
        f"- Markets: {', '.join(plan['markets'])}",
        f"- Years: {plan['years']['start']}:{plan['years']['end']}",
        f"- Sources: {plan['source_count']}",
        f"- Jobs: {plan['job_count']}",
        "",
        "| Job | Source | Market | Years | Ticker/Scope | Executable | Command |",
        "|---|---|---:|---:|---|---:|---|",
    ]
    for job in plan["jobs"]:
        lines.append(
            (
                "| {id} | {source} | {market} | {years} | {scope} | "
                "{executable} | `{command}` |"
            ).format(
                id=job["id"],
                source=job["source_id"],
                market=job.get("market", ""),
                years=job.get("years", ""),
                scope=job.get("ticker") or job.get("scope") or "",
                executable="yes" if job["executable"] else "no",
                command=job.get("command") or "manual/import-specific",
            )
        )
    return "\n".join(lines)


def _jobs_for_source(
    source: dict[str, Any],
    *,
    requested_markets: set[str],
    start_year: int,
    end_year: int,
    ticker_map: dict[str, list[str]],
    partition: str,
) -> list[dict[str, Any]]:
    source_id = source["id"]
    if source_id == "sec_bulk_companyfacts":
        tickers = ticker_map["US"]
        return [
            _job(
                source,
                "US",
                start_year,
                end_year,
                "sec-bulk-companyfacts-submissions",
                "all-us-filers",
                command=(
                    "python -m services.ingestion_worker.cli collect-sec-bulk "
                    "--archives companyfacts,submissions --persist"
                ),
            ),
            _job(
                source,
                "US",
                start_year,
                end_year,
                "sec-bulk-companyfacts-warehouse",
                ",".join(tickers),
                command=(
                    "python -m services.ingestion_worker.cli load-sec-bulk-warehouse "
                    f"--tickers {','.join(tickers)} --persist"
                ),
            ),
        ]
    if source_id == "sec_8k_exhibits":
        tickers = ticker_map["US"]
        return [
            _job(
                source,
                "US",
                start_year,
                end_year,
                "sec-8k-exhibit-normalize",
                ",".join(tickers),
                command=(
                    "python -m services.ingestion_worker.cli normalize-us-batch "
                    f"--tickers {','.join(tickers)} --years {start_year}:{end_year} "
                    "--persist --continue-on-error"
                ),
            )
        ]
    if source_id == "opendart_xbrl":
        return [
            _job(
                source,
                "KR",
                year,
                year,
                "opendart-annual",
                ticker,
                ticker=ticker,
                command=(
                    "python -m services.ingestion_worker.cli collect "
                    f"--market KR --ticker {ticker} --years {year}:{year} --persist"
                ),
            )
            for year in range(start_year, end_year + 1)
            for ticker in ticker_map["KR"]
        ]
    if source_id == "krx_public_prices":
        return _date_partition_jobs(
            source,
            "KR",
            start_year,
            end_year,
            partition,
            "krx-public-prices",
        )
    if source_id == "pykrx":
        tickers = ticker_map["KR"]
        return [
            _job(
                source,
                "KR",
                year,
                year,
                "pykrx-daily-ohlcv",
                ",".join(tickers),
                command=(
                    "python -m services.ingestion_worker.cli collect-pykrx-prices "
                    f"--tickers {','.join(tickers)} --years {year}:{year} --persist"
                ),
            )
            for year in range(start_year, end_year + 1)
        ]
    if source_id == "marcap_dataset":
        tickers = ticker_map["KR"]
        return [
            _job(
                source,
                "KR",
                year,
                year,
                "marcap-yearly-parquet",
                ",".join(tickers),
                command=(
                    "python -m services.ingestion_worker.cli collect-marcap "
                    f"--tickers {','.join(tickers)} --years {year}:{year} --persist"
                ),
            )
            for year in range(start_year, end_year + 1)
        ]
    if source_id in {"naver_search_research", "hankyung_consensus_metadata"}:
        tickers = ticker_map["KR"]
        source_arg = (
            "naver"
            if source_id == "naver_search_research"
            else "hankyung"
        )
        return [
            _job(
                source,
                "KR",
                start_year,
                end_year,
                "research-link-metadata",
                ",".join(tickers),
                command=(
                    "python -m services.ingestion_worker.cli collect-research-metadata "
                    f"--market KR --sources {source_arg} --tickers {','.join(tickers)} "
                    f"--years {start_year}:{end_year} --persist --continue-on-error"
                ),
            )
        ]
    if source_id == "finance_data_reader":
        markets = sorted(requested_markets.intersection({"US", "KR", "JP"}))
        return [
            _job(
                source,
                market,
                start_year,
                end_year,
                "fdr-listings-prices",
                ",".join(ticker_map[market]),
                command=(
                    "python -m services.ingestion_worker.cli collect-fdr-prices "
                    f"--market {market} --tickers {','.join(ticker_map[market])} "
                    f"--years {start_year}:{end_year} --persist"
                ),
            )
            for market in markets
        ]
    if source_id == "jquants_api":
        tickers = ticker_map["JP"]
        return [
            _job(
                source,
                "JP",
                year,
                year,
                "jquants-statements-prices-dividends",
                ",".join(tickers),
                command=(
                    "python -m services.ingestion_worker.cli collect-jquants "
                    f"--tickers {','.join(tickers)} --years {year}:{year} --persist"
                ),
            )
            for year in range(start_year, end_year + 1)
        ]
    if source_id == "edinet_api":
        tickers = ticker_map["JP"]
        return [
            _job(
                source,
                "JP",
                year,
                year,
                "edinet-xbrl-csv-filings",
                ",".join(tickers),
                command=(
                    "python -m services.ingestion_worker.cli collect-edinet "
                    f"--tickers {','.join(tickers)} --years {year}:{year} --persist"
                ),
            )
            for year in range(start_year, end_year + 1)
        ]
    if source_id == "fred":
        return [
            _job(
                source,
                "GLOBAL",
                start_year,
                end_year,
                "fred-curated-series",
                ",".join(DEFAULT_FRED_SERIES),
                command=(
                    "python -m services.ingestion_worker.cli collect-fred "
                    f"--series {','.join(DEFAULT_FRED_SERIES)} "
                    f"--years {start_year}:{end_year} --persist"
                ),
            )
        ]
    if source_id == "ecos":
        return [
            _job(
                source,
                "KR",
                start_year,
                end_year,
                "ecos-curated-macro-industry",
                "curated-stat-code-list-required",
                command=(
                    "python -m services.ingestion_worker.cli collect-ecos "
                    "--series <stat_code:cycle:item_code,...> "
                    f"--years {start_year}:{end_year} --persist"
                ),
                executable=False,
            )
        ]
    if source_id == "kosis":
        return [
            _job(
                source,
                "KR",
                start_year,
                end_year,
                "kosis-curated-statistics",
                "curated-org-table-or-user-stats-id-list-required",
                command=(
                    "python -m services.ingestion_worker.cli collect-kosis "
                    "--tables <orgId:tblId-or-userStatsId,...> "
                    f"--years {start_year}:{end_year} --persist"
                ),
                executable=False,
            )
        ]
    if source_id == "estat":
        return [
            _job(
                source,
                "JP",
                start_year,
                end_year,
                "estat-curated-statistics",
                "curated-stats-data-id-list-required",
                command=(
                    "python -m services.ingestion_worker.cli collect-estat "
                    "--stats-data-ids <statsDataId,...> "
                    f"--years {start_year}:{end_year} --persist"
                ),
                executable=False,
            )
        ]
    if source_id == "stooq_bulk_prices":
        markets = sorted(requested_markets.intersection({"US", "JP", "GLOBAL"}))
        return [
            _job(
                source,
                market,
                start_year,
                end_year,
                "stooq-daily-prices",
                ",".join(ticker_map.get(market, [f"{market.lower()}-prices"])),
                command=(
                    "python -m services.ingestion_worker.cli collect-stooq-prices "
                    f"--market {market} --tickers {','.join(ticker_map[market])} "
                    f"--years {start_year}:{end_year} --persist"
                )
                if market in ticker_map
                else None,
            )
            for market in markets
        ]
    if source_id == "fnguide_dataguide":
        return [
            _job(
                source,
                "KR",
                start_year,
                end_year,
                "fnguide-user-export",
                "licensed-user-excel-csv",
                command=(
                    "python -m services.ingestion_worker.cli import-fnguide-export "
                    "--path storage/imports/fnguide_dataguide.csv --persist"
                ),
                executable=False,
            )
        ]
    return []


def _date_partition_jobs(
    source: dict[str, Any],
    market: str,
    start_year: int,
    end_year: int,
    partition: str,
    job_type: str,
) -> list[dict[str, Any]]:
    if partition == "annual":
        return [
            _job(source, market, year, year, job_type, f"{market}-{year}")
            for year in range(start_year, end_year + 1)
        ]
    if partition == "monthly":
        return [
            _job(source, market, year, year, job_type, f"{market}-{year}-{month:02d}")
            for year in range(start_year, end_year + 1)
            for month in range(1, 13)
        ]
    raise ValueError("partition must be annual or monthly")


def _job(
    source: dict[str, Any],
    market: str,
    start_year: int,
    end_year: int,
    job_type: str,
    scope: str,
    *,
    ticker: str | None = None,
    command: str | None = None,
    executable: bool = True,
) -> dict[str, Any]:
    return {
        "id": f"{source['id']}:{market}:{start_year}:{end_year}:{scope}",
        "source_id": source["id"],
        "source_name": source["name"],
        "lane": source["lane"],
        "priority": source["priority"],
        "market": market,
        "years": f"{start_year}:{end_year}",
        "type": job_type,
        "scope": scope,
        "ticker": ticker,
        "raw_prefix": source["raw_prefix"],
        "warehouse_targets": source["warehouse_targets"],
        "executable": executable and command is not None,
        "command": command,
        "notes": source["notes"],
    }


def _ticker_map(tickers: str | list[str] | None) -> dict[str, list[str]]:
    if tickers:
        normalized = normalize_coverage_tickers(tickers)
        return {"US": normalized, "KR": normalized, "JP": normalized}
    return {"US": DEFAULT_US_TICKERS, "KR": DEFAULT_KR_TICKERS, "JP": DEFAULT_JP_TICKERS}


def _normalize_markets(markets: str | list[str]) -> set[str]:
    values = markets.split(",") if isinstance(markets, str) else markets
    normalized = {str(value).strip().upper() for value in values if str(value).strip()}
    return normalized or {"US", "KR", "JP"}


def _parse_years(years: str) -> tuple[int, int]:
    if ":" in years:
        start, end = years.split(":", 1)
    else:
        start = end = years
    start_year = int(start)
    end_year = int(end)
    if start_year > end_year:
        raise ValueError("start year must be <= end year")
    return start_year, end_year
