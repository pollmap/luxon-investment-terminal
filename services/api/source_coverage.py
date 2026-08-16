from __future__ import annotations

import csv
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
from typing import Any

from packages.core.universe import (
    JP_TOP_MARKET_CAP_PRIORITY_TICKERS,
    KR_TOP_MARKET_CAP_PRIORITY_TICKERS,
    SUPPORTED_PRIORITY_MARKETS,
    TOP_MARKET_CAP_PRIORITY_TICKERS,
    US_TOP_MARKET_CAP_PRIORITY_TICKERS,
)

DEFAULT_SOURCE_COVERAGE_TICKERS = KR_TOP_MARKET_CAP_PRIORITY_TICKERS
DEFAULT_SOURCE_COVERAGE_MARKET = "KR"
US_PATTERN_SOURCE_COVERAGE_TICKERS = US_TOP_MARKET_CAP_PRIORITY_TICKERS
DEFAULT_REMEDIATION_YEARS = "2020:2025"
CONSENSUS_TRACE_ANCHOR_FIELDS = ("source_url", "source_document_id", "filing_id")
CONSENSUS_VALUE_FIELDS = ("estimate_eps", "currency", "source")
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
MANUAL_FORECAST_SOURCES = {
    "manual",
    "manual_assumption",
    "manual_forecast_assumption",
    "user_manual_forecast_assumption",
    "explicit_manual_forecast_assumption",
}

MVP_PATTERN_BY_TICKER = {
    "AAPL": "near_gaap_adjusted",
    "NVDA": "semiconductor_special_items",
    "CRM": "sbc_heavy_software",
    "O": "reit_ffo_affo",
    "JPM": "bank_sector",
    **{ticker: "kr_top_market_cap" for ticker in KR_TOP_MARKET_CAP_PRIORITY_TICKERS},
    **{
        ticker: "us_top_market_cap"
        for ticker in US_TOP_MARKET_CAP_PRIORITY_TICKERS
        if ticker not in {"AAPL", "NVDA", "CRM", "O", "JPM"}
    },
    **{ticker: "jp_top_market_cap" for ticker in JP_TOP_MARKET_CAP_PRIORITY_TICKERS},
}


def priority_coverage_tickers_for_market(market: str = DEFAULT_SOURCE_COVERAGE_MARKET) -> tuple[str, ...]:
    normalized_market = market.strip().upper()
    if normalized_market == "ALL":
        return TOP_MARKET_CAP_PRIORITY_TICKERS
    if normalized_market == "KR":
        return KR_TOP_MARKET_CAP_PRIORITY_TICKERS
    if normalized_market == "US":
        return US_TOP_MARKET_CAP_PRIORITY_TICKERS
    if normalized_market == "JP":
        return JP_TOP_MARKET_CAP_PRIORITY_TICKERS
    allowed = ", ".join([*SUPPORTED_PRIORITY_MARKETS, "ALL"])
    raise ValueError(f"market must be one of {allowed}")


def normalize_coverage_tickers(
    tickers: str | Iterable[str] | None = None,
    *,
    market: str = DEFAULT_SOURCE_COVERAGE_MARKET,
) -> list[str]:
    if tickers is None:
        return list(priority_coverage_tickers_for_market(market))
    if isinstance(tickers, str):
        values = tickers.split(",")
    else:
        values = tickers
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        ticker = str(value).strip().upper()
        if not ticker or ticker in seen:
            continue
        normalized.append(ticker)
        seen.add(ticker)
    return normalized or list(priority_coverage_tickers_for_market(market))


def build_source_coverage_report(
    rows: Iterable[dict[str, Any]],
    expected_tickers: Iterable[str] | None = None,
    *,
    min_historical_years: int = 3,
    min_forecast_years: int = 5,
    require_consensus_forecast: bool = False,
    postgres_reachable: bool = True,
    error: str | None = None,
) -> dict[str, Any]:
    tickers = normalize_coverage_tickers(expected_tickers)
    rows_by_ticker = {
        str(row.get("ticker", "")).upper(): row
        for row in rows
        if row.get("ticker")
    }
    ticker_reports = [
        _ticker_coverage(
            ticker,
            rows_by_ticker.get(ticker, {}),
            min_historical_years=min_historical_years,
            min_forecast_years=min_forecast_years,
            require_consensus_forecast=require_consensus_forecast,
        )
        for ticker in tickers
    ]
    core_ready_count = sum(1 for row in ticker_reports if row["core_ready"])
    consensus_ready_count = sum(1 for row in ticker_reports if row["consensus_forecast_ready"])
    any_source_rows = any(
        any(value > 0 for value in row["counts"].values())
        for row in ticker_reports
    )
    required_ready_count = sum(1 for row in ticker_reports if not row["missing_required"])
    required_ready = ticker_reports and required_ready_count == len(ticker_reports)
    status = "ready" if required_ready else "partial" if any_source_rows else "missing"
    missing_by_requirement = _missing_by_requirement(ticker_reports)
    remediation = _remediation_plan(
        ticker_reports,
        missing_by_requirement,
        years=DEFAULT_REMEDIATION_YEARS,
        min_forecast_years=min_forecast_years,
        postgres_reachable=postgres_reachable,
        postgres_error=error,
    )
    return {
        "status": status,
        "data_mode": "source_backed_required",
        "postgres": {"reachable": postgres_reachable, "error": error},
        "requirements": {
            "min_historical_years": min_historical_years,
            "min_forecast_years": min_forecast_years,
            "consensus_forecast_required": require_consensus_forecast,
            "core_required": [
                "security",
                "adjusted_earnings",
                "price_bars",
                "financial_metrics",
                "source_evidence",
            ],
            "conditional_core_required": [
                {
                    "requirement": "market_cap_evidence",
                    "applies_to": "KR/US/JP top-market-cap priority tickers",
                },
                {
                    "requirement": "listed_shares_evidence",
                    "applies_to": "KR top-market-cap priority tickers where marcap evidence is available",
                },
            ],
            "consensus_forecast_optional": [
                "consensus_estimate_snapshots",
                "median_or_current_adjusted_operating_eps_by_forecast_year",
            ],
        },
        "summary": {
            "tickers_expected": len(ticker_reports),
            "core_ready": core_ready_count,
            "consensus_forecast_ready": consensus_ready_count,
            "missing_core": [
                row["ticker"] for row in ticker_reports if not row["core_ready"]
            ],
            "missing_consensus_forecast": [
                row["ticker"]
                for row in ticker_reports
                if not row["consensus_forecast_ready"]
            ],
            "missing_by_requirement": missing_by_requirement,
        },
        "remediation": remediation,
        "tickers": ticker_reports,
    }


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = value.split(",")
    else:
        try:
            values = list(value)
        except TypeError:
            values = [value]
    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = str(item).strip()
        if not text or text in seen:
            continue
        normalized.append(text)
        seen.add(text)
    return normalized


def _ticker_coverage(
    ticker: str,
    row: dict[str, Any],
    *,
    min_historical_years: int,
    min_forecast_years: int,
    require_consensus_forecast: bool,
) -> dict[str, Any]:
    counts = {
        "security": _int(row.get("security_count")),
        "adjusted_years": _int(row.get("adjusted_years")),
        "price_years": _int(row.get("price_years")),
        "market_cap_years": _int(row.get("market_cap_years")),
        "listed_shares_years": _int(row.get("listed_shares_years")),
        "financial_fact_years": _int(row.get("financial_fact_years")),
        "financial_fact_tags": _int(row.get("financial_fact_tags")),
        "financial_metric_years": _int(row.get("financial_metric_years")),
        "financial_metric_keys": _int(row.get("financial_metric_keys")),
        "dividend_years": _int(row.get("dividend_years")),
        "consensus_forecast_years": _int(row.get("consensus_forecast_years")),
        "consensus_valuation_years": _int(row.get("consensus_valuation_years")),
        "consensus_snapshots": _int(row.get("consensus_snapshots")),
        "consensus_valuation_snapshots": _int(row.get("consensus_valuation_snapshots")),
        "adjustment_rows": _int(row.get("adjustment_rows")),
        "source_documents": _int(row.get("source_documents")),
        "raw_objects": _int(row.get("raw_objects")),
    }
    available_metric_keys = _string_list(row.get("available_metric_keys"))
    method_counts = {
        "s1": _int(row.get("s1_periods")),
        "s2": _int(row.get("s2_periods")),
        "s4": _int(row.get("s4_periods")),
    }
    market_cap_required = _requires_market_cap_evidence(ticker)
    listed_shares_required = _requires_listed_shares_evidence(ticker)
    checks = [
        _check("security", counts["security"] > 0, True, "security row exists"),
        _check(
            "adjusted_earnings",
            counts["adjusted_years"] >= min_historical_years,
            True,
            f"needs at least {min_historical_years} adjusted EPS fiscal years",
        ),
        _check(
            "price_bars",
            counts["price_years"] >= min_historical_years,
            True,
            f"needs at least {min_historical_years} price fiscal years",
        ),
        _check(
            "source_evidence",
            counts["source_documents"] > 0
            or counts["raw_objects"] > 0
            or counts["financial_fact_years"] > 0,
            True,
            "needs source_documents, raw_objects, or financial_facts evidence",
        ),
        _check(
            "financial_metrics",
            counts["financial_metric_years"] >= min_historical_years
            and counts["financial_metric_keys"] > 0,
            True,
            (
                f"needs at least {min_historical_years} metric_values fiscal years "
                "and one valuation metric key"
            ),
        ),
        _check(
            "waterfall_adjustments",
            counts["adjustment_rows"] > 0 or method_counts["s4"] > 0,
            False,
            "S1/S2 waterfall rows or explicit S4 GAAP fallback",
        ),
        _check(
            "market_cap_evidence",
            counts["market_cap_years"] >= min_historical_years,
            market_cap_required,
            f"needs at least {min_historical_years} fiscal years with market cap evidence",
        ),
        _check(
            "listed_shares_evidence",
            counts["listed_shares_years"] >= min_historical_years,
            listed_shares_required,
            f"needs at least {min_historical_years} fiscal years with listed shares evidence",
        ),
        _check(
            "consensus_forecast",
            counts["consensus_valuation_years"] >= min_forecast_years,
            require_consensus_forecast,
            (
                f"needs {min_forecast_years} forecast years with median/current "
                "adjusted operating EPS snapshots"
            ),
        ),
    ]
    core_check_names = {
        "security",
        "adjusted_earnings",
        "price_bars",
        "financial_metrics",
        "source_evidence",
    }
    if market_cap_required:
        core_check_names.add("market_cap_evidence")
    if listed_shares_required:
        core_check_names.add("listed_shares_evidence")
    core_ready = all(check["ok"] for check in checks if check["name"] in core_check_names)
    consensus_ready = counts["consensus_valuation_years"] >= min_forecast_years
    missing_required = [
        check["name"] for check in checks if check["required"] and not check["ok"]
    ]
    status = "ready" if not missing_required else "partial" if counts["security"] else "missing"
    return {
        "ticker": ticker,
        "name": row.get("name") or ticker,
        "market": row.get("market"),
        "country": row.get("country"),
        "currency": row.get("currency"),
        "pattern": MVP_PATTERN_BY_TICKER.get(ticker, "custom"),
        "status": status,
        "core_ready": core_ready,
        "consensus_forecast_ready": consensus_ready,
        "counts": counts,
        "method_counts": method_counts,
        "available_metric_keys": available_metric_keys,
        "latest_years": {
            "adjusted": _optional_int(row.get("latest_adjusted_year")),
            "price": _optional_int(row.get("latest_price_year")),
            "financial_fact": _optional_int(row.get("latest_financial_fact_year")),
            "consensus": _optional_int(row.get("latest_consensus_year")),
        },
        "local_consensus_overlay_ready": bool(row.get("local_consensus_overlay_ready")),
        "local_consensus_overlay_source": row.get("local_consensus_overlay_source"),
        "checks": checks,
        "missing_required": missing_required,
    }


def _requires_market_cap_evidence(ticker: str) -> bool:
    return ticker.upper() in TOP_MARKET_CAP_PRIORITY_TICKERS


def _requires_listed_shares_evidence(ticker: str) -> bool:
    return ticker.upper() in KR_TOP_MARKET_CAP_PRIORITY_TICKERS


def _check(name: str, ok: bool, required: bool, detail: str) -> dict[str, Any]:
    return {
        "name": name,
        "ok": ok,
        "required": required,
        "detail": detail,
    }


def _missing_by_requirement(ticker_reports: list[dict[str, Any]]) -> dict[str, list[str]]:
    missing: dict[str, list[str]] = {}
    for row in ticker_reports:
        for requirement in row["missing_required"]:
            missing.setdefault(requirement, []).append(row["ticker"])
    return dict(sorted(missing.items()))


def _consensus_csv_path(tickers: list[str]) -> str:
    if len(tickers) != 1:
        return "storage/imports/consensus_estimates.csv"
    ticker = tickers[0].strip().upper()
    for suffix in (".KS", ".KQ", ".T", ".US"):
        if ticker.endswith(suffix):
            ticker = ticker[: -len(suffix)]
            break
    slug = "".join(char.lower() if char.isalnum() else "_" for char in ticker).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return f"storage/imports/consensus_{slug or 'custom'}.csv"


def _consensus_workpaper_path(csv_path: str) -> str:
    if csv_path.endswith(".csv"):
        return f"{csv_path[:-4]}_workpaper.md"
    return f"{csv_path}_workpaper.md"


def _remediation_plan(
    ticker_reports: list[dict[str, Any]],
    missing_by_requirement: dict[str, list[str]],
    *,
    years: str,
    min_forecast_years: int,
    postgres_reachable: bool,
    postgres_error: str | None,
) -> dict[str, Any]:
    if not missing_by_requirement:
        return {
            "status": "ready",
            "prerequisites": [],
            "next_actions": [],
            "notes": ["source coverage requirements are satisfied"],
        }

    actions: list[dict[str, Any]] = []
    ticker_order = [row["ticker"] for row in ticker_reports]
    priority_bootstrap = _priority_bootstrap_action(
        ticker_reports,
        missing_by_requirement,
        years=years,
    )
    if priority_bootstrap:
        actions.append(priority_bootstrap)

    def missing_tickers(*requirements: str) -> list[str]:
        seen: set[str] = set()
        for requirement in requirements:
            for ticker in missing_by_requirement.get(requirement, []):
                seen.add(ticker)
        return [ticker for ticker in ticker_order if ticker in seen]

    source_metric_tickers = missing_tickers("security", "source_evidence", "financial_metrics")
    for market, tickers in _group_tickers_by_market(source_metric_tickers).items():
        actions.extend(_source_metric_actions(market, tickers, years))

    adjusted_tickers = missing_tickers("adjusted_earnings")
    for market, tickers in _group_tickers_by_market(adjusted_tickers).items():
        actions.extend(_adjusted_earnings_actions(market, tickers, years))

    price_tickers = missing_tickers("price_bars")
    for market, tickers in _group_tickers_by_market(price_tickers).items():
        actions.extend(_price_actions(market, tickers, years))

    market_structure_tickers = missing_tickers(
        "market_cap_evidence",
        "listed_shares_evidence",
    )
    for market, tickers in _group_tickers_by_market(market_structure_tickers).items():
        actions.extend(_market_structure_actions(market, tickers, years))

    forecast_tickers = missing_tickers("consensus_forecast")
    forecast_csv_preflight: dict[str, Any] | None = None
    if forecast_tickers:
        joined = ",".join(forecast_tickers)
        consensus_csv_path = _consensus_csv_path(forecast_tickers)
        consensus_workpaper_path = _consensus_workpaper_path(consensus_csv_path)
        forecast_csv_preflight = _forecast_csv_preflight(
            forecast_tickers,
            consensus_csv_path,
            min_forecast_years=min_forecast_years,
        )
        actions.append(
            {
                "id": "consensus_workpaper",
                "priority": 38,
                "requirements": ["consensus_forecast"],
                "tickers": forecast_tickers,
                "description": (
                    "Create a forecast evidence workpaper before filling the 1Y-5Y CSV; "
                    "document accepted sources, blocked inputs, required rows, and trace anchors."
                ),
                "cli_commands": [
                    (
                        "python -m services.ingestion_worker.cli consensus-workpaper "
                        f"--tickers {joined} --csv-path {consensus_csv_path} "
                        "--template-cases median --validation-cases median,current "
                        f"--case-mode any --out {consensus_workpaper_path}"
                    )
                ],
                "github_actions": {
                    "workflow": "ingestion-worker.yml",
                    "command": "consensus_workpaper",
                    "coverage_tickers": joined,
                    "csv_path": consensus_csv_path,
                    "persist": False,
                },
            }
        )
        actions.append(
            {
                "id": "export_consensus_template",
                "priority": 39,
                "requirements": ["consensus_forecast"],
                "tickers": forecast_tickers,
                "description": (
                    "Create a blank 1Y-5Y forecast snapshot CSV template; "
                    "fill EPS, source, and a trace anchor from traceable evidence before import."
                ),
                "cli_commands": [
                    (
                        "python -m services.ingestion_worker.cli export-consensus-template "
                        f"--tickers {joined} --cases median --out {consensus_csv_path}"
                    )
                ],
                "github_actions": {
                    "workflow": "ingestion-worker.yml",
                    "command": "export_consensus_template",
                    "coverage_tickers": joined,
                    "csv_path": consensus_csv_path,
                    "persist": False,
                },
            }
        )
        kr_forecast_tickers = [
            ticker for ticker in forecast_tickers if _ticker_market(ticker) == "KR"
        ]
        if kr_forecast_tickers:
            kr_joined = ",".join(kr_forecast_tickers)
            actions.append(
                {
                    "id": "export_deterministic_forecast_csv",
                    "priority": 39.5,
                    "requirements": ["consensus_forecast"],
                    "tickers": kr_forecast_tickers,
                    "description": (
                        "Create a source-backed historical CAGR manual forecast assumption CSV "
                        "from KR valuation cache; label it as manual, not external consensus."
                    ),
                    "cli_commands": [
                        (
                            "python -m services.ingestion_worker.cli "
                            "export-deterministic-forecast-csv "
                            f"--tickers {kr_joined} --cases median --out {consensus_csv_path}"
                        )
                    ],
                    "github_actions": {
                        "workflow": "ingestion-worker.yml",
                        "command": "export_deterministic_forecast_csv",
                        "coverage_tickers": kr_joined,
                        "csv_path": consensus_csv_path,
                        "persist": False,
                    },
                }
            )
        actions.append(
            {
                "id": "import_consensus_csv",
                "priority": 41,
                "requirements": ["consensus_forecast"],
                "tickers": forecast_tickers,
                "description": (
                    "Import user-verified 1Y-5Y consensus forecast snapshots; "
                    "do not synthesize analyst estimates or omit trace anchors."
                ),
                "cli_commands": [
                    (
                        "python -m services.ingestion_worker.cli import-consensus-csv "
                        f"--path {consensus_csv_path} --persist"
                    )
                ],
                "github_actions": {
                    "workflow": "ingestion-worker.yml",
                    "command": "import_consensus_csv",
                    "coverage_tickers": joined,
                    "csv_path": consensus_csv_path,
                    "persist": True,
                },
            }
        )
        actions.insert(
            -1,
            {
                "id": "validate_consensus_csv",
                "priority": 40,
                "requirements": ["consensus_forecast"],
                "tickers": forecast_tickers,
                "description": (
                    "Validate the filled 1Y-5Y forecast CSV for required ticker-year "
                    "coverage, median/current cases, trace anchors, and blocked "
                    "template quality statuses before import."
                ),
                "cli_commands": [
                    (
                        "python -m services.ingestion_worker.cli validate-consensus-csv "
                        f"--path {consensus_csv_path} "
                        f"--tickers {joined} --cases median,current --case-mode any --strict"
                    )
                ],
                "github_actions": {
                    "workflow": "ingestion-worker.yml",
                    "command": "validate_consensus_csv",
                    "coverage_tickers": joined,
                    "csv_path": consensus_csv_path,
                    "persist": False,
                },
            },
        )

    prerequisites = _remediation_prerequisites(
        ticker_reports,
        actions,
        postgres_reachable=postgres_reachable,
        postgres_error=postgres_error,
    )
    return {
        "status": "needs_source_data",
        "years": years,
        "forecast_csv_preflight": forecast_csv_preflight,
        "prerequisites": prerequisites,
        "next_actions": sorted(
            _dedupe_actions(actions),
            key=lambda action: action["priority"],
        ),
        "notes": [
            "satisfy prerequisites before running --persist commands",
            "run actions in priority order",
            "all imported files must be source-backed and traceable",
        ],
    }


def _forecast_csv_preflight(
    tickers: list[str],
    csv_path: str,
    *,
    min_forecast_years: int,
) -> dict[str, Any]:
    path = Path(csv_path)
    base: dict[str, Any] = {
        "path": csv_path,
        "exists": path.exists(),
        "status": "missing_csv",
        "tickers": tickers,
        "required_periods": len(tickers) * min_forecast_years,
        "import_ready_candidate": False,
        "strict_validator": (
            "python -m services.ingestion_worker.cli validate-consensus-csv "
            f"--path {csv_path} --tickers {','.join(tickers)} "
            "--cases median,current --case-mode any --strict"
        ),
    }
    if not path.exists():
        return base

    ticker_set = {ticker.strip().upper() for ticker in tickers}
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError as exc:
        return {
            **base,
            "exists": True,
            "status": "unreadable_csv",
            "error": str(exc),
        }

    candidate_rows = [
        row
        for row in rows
        if (row.get("ticker") or "").strip().upper() in ticker_set
        and (row.get("estimate_case") or "").strip().lower() in {"median", "current"}
    ]
    ready_rows = [
        row
        for row in candidate_rows
        if _forecast_preflight_row_ready(row)
    ]
    missing_value_rows = sum(
        1
        for row in candidate_rows
        if any(not (row.get(field) or "").strip() for field in CONSENSUS_VALUE_FIELDS)
    )
    missing_trace_rows = sum(
        1
        for row in candidate_rows
        if not any((row.get(field) or "").strip() for field in CONSENSUS_TRACE_ANCHOR_FIELDS)
    )
    missing_manual_notes_rows = sum(
        1 for row in candidate_rows if _forecast_preflight_manual_notes_missing(row)
    )
    invalid_value_rows = sum(
        1 for row in candidate_rows if _forecast_preflight_invalid_estimate(row)
    )
    invalid_currency_rows = sum(
        1 for row in candidate_rows if _forecast_preflight_invalid_currency(row)
    )
    blocked_evidence_rows = sum(
        1 for row in candidate_rows if _forecast_preflight_blocked_evidence(row)
    )
    manual_assumption_ready_rows = sum(
        1 for row in ready_rows if _forecast_preflight_assumption_type(row) == "manual_assumption"
    )
    external_consensus_ready_rows = sum(
        1 for row in ready_rows if _forecast_preflight_assumption_type(row) == "external_consensus"
    )
    covered_periods = {
        ((row.get("ticker") or "").strip().upper(), str(row.get("fiscal_year") or "").strip())
        for row in ready_rows
        if row.get("fiscal_year")
    }
    missing_periods = _forecast_preflight_missing_periods(
        tickers=tickers,
        candidate_rows=candidate_rows,
        ready_rows=ready_rows,
        min_forecast_years=min_forecast_years,
    )
    status = "candidate_ready"
    if not rows:
        status = "empty_csv"
    elif not candidate_rows:
        status = "no_matching_rows"
    elif (
        candidate_rows
        and not ready_rows
        and not missing_value_rows
        and not missing_trace_rows
        and (missing_manual_notes_rows or invalid_value_rows or invalid_currency_rows or blocked_evidence_rows)
    ):
        status = "invalid_candidate"
    elif not ready_rows and candidate_rows:
        status = "template_pending"
    elif len(covered_periods) < base["required_periods"]:
        status = "not_import_ready"
    return {
        **base,
        "exists": True,
        "status": status,
        "rows": len(rows),
        "candidate_rows": len(candidate_rows),
        "ready_rows": len(ready_rows),
        "covered_periods": len(covered_periods),
        "missing_periods": missing_periods,
        "missing_value_rows": missing_value_rows,
        "missing_trace_rows": missing_trace_rows,
        "missing_manual_notes_rows": missing_manual_notes_rows,
        "invalid_value_rows": invalid_value_rows,
        "invalid_currency_rows": invalid_currency_rows,
        "blocked_evidence_rows": blocked_evidence_rows,
        "manual_assumption_ready_rows": manual_assumption_ready_rows,
        "external_consensus_ready_rows": external_consensus_ready_rows,
        "assumption_types": {
            "manual_assumption": manual_assumption_ready_rows,
            "external_consensus": external_consensus_ready_rows,
        },
        "import_ready_candidate": status == "candidate_ready",
    }


def _forecast_preflight_missing_periods(
    *,
    tickers: list[str],
    candidate_rows: list[dict[str, str]],
    ready_rows: list[dict[str, str]],
    min_forecast_years: int,
) -> list[dict[str, Any]]:
    fiscal_years = sorted(
        {
            int(str(row.get("fiscal_year") or "").strip())
            for row in candidate_rows
            if str(row.get("fiscal_year") or "").strip().isdigit()
        }
    )
    if not fiscal_years:
        return []
    start_year = fiscal_years[0]
    expected_years = list(range(start_year, start_year + min_forecast_years))
    ready_periods = {
        (
            (row.get("ticker") or "").strip().upper(),
            int(str(row.get("fiscal_year") or "").strip()),
        )
        for row in ready_rows
        if str(row.get("fiscal_year") or "").strip().isdigit()
    }
    missing: list[dict[str, Any]] = []
    for ticker in tickers:
        normalized_ticker = ticker.strip().upper()
        for fiscal_year in expected_years:
            if (normalized_ticker, fiscal_year) not in ready_periods:
                missing.append(
                    {
                        "ticker": normalized_ticker,
                        "fiscal_year": fiscal_year,
                        "estimate_cases_allowed": ["median", "current"],
                    }
                )
    return missing


def _forecast_preflight_row_ready(row: dict[str, str]) -> bool:
    return (
        all((row.get(field) or "").strip() for field in CONSENSUS_VALUE_FIELDS)
        and any((row.get(field) or "").strip() for field in CONSENSUS_TRACE_ANCHOR_FIELDS)
        and not _forecast_preflight_manual_notes_missing(row)
        and not _forecast_preflight_invalid_estimate(row)
        and not _forecast_preflight_invalid_currency(row)
        and not _forecast_preflight_blocked_evidence(row)
    )


def _forecast_preflight_assumption_type(row: dict[str, str]) -> str:
    source = (row.get("source") or "").strip().lower()
    quality = (row.get("quality_status") or "").strip().lower()
    if source in MANUAL_FORECAST_SOURCES or quality in MANUAL_FORECAST_SOURCES:
        return "manual_assumption"
    if "manual" in source or "manual" in quality:
        return "manual_assumption"
    return "external_consensus"


def _forecast_preflight_manual_notes_missing(row: dict[str, str]) -> bool:
    if _forecast_preflight_assumption_type(row) != "manual_assumption":
        return False
    return not (row.get("notes") or "").strip()


def _forecast_preflight_invalid_estimate(row: dict[str, str]) -> bool:
    raw_value = (row.get("estimate_eps") or "").strip()
    if not raw_value:
        return False
    try:
        return Decimal(raw_value) <= 0
    except InvalidOperation:
        return True


def _forecast_preflight_invalid_currency(row: dict[str, str]) -> bool:
    currency = (row.get("currency") or "").strip().upper()
    if not currency:
        return False
    return re.fullmatch(r"[A-Z]{3}", currency) is None


def _forecast_preflight_blocked_evidence(row: dict[str, str]) -> bool:
    has_import_candidate_input = any(
        (row.get(field) or "").strip()
        for field in ("estimate_eps", "source", *CONSENSUS_TRACE_ANCHOR_FIELDS)
    )
    if not has_import_candidate_input:
        return False
    quality = (row.get("quality_status") or "").strip().lower()
    if quality in BLOCKED_CONSENSUS_QUALITY_STATUSES:
        return True
    evidence_text = " ".join(
        (row.get(field) or "").strip().lower()
        for field in ("source", "source_url", "source_document_id", "filing_id", "quality_status")
    )
    return any(token in evidence_text for token in BLOCKED_CONSENSUS_SOURCE_TOKENS)


def _priority_bootstrap_action(
    ticker_reports: list[dict[str, Any]],
    missing_by_requirement: dict[str, list[str]],
    *,
    years: str,
) -> dict[str, Any] | None:
    core_requirements = {
        "security",
        "adjusted_earnings",
        "price_bars",
        "financial_metrics",
        "source_evidence",
        "market_cap_evidence",
        "listed_shares_evidence",
    }
    missing_core_requirements = sorted(
        requirement
        for requirement in core_requirements
        if missing_by_requirement.get(requirement)
    )
    if not missing_core_requirements:
        return None

    ticker_order = [row["ticker"] for row in ticker_reports]
    missing_core_tickers = {
        ticker
        for requirement in missing_core_requirements
        for ticker in missing_by_requirement.get(requirement, [])
    }
    markets = [
        market
        for market in ("KR", "US", "JP")
        if any(_ticker_market(row["ticker"]) == market for row in ticker_reports)
    ]
    selected_tickers = [ticker for ticker in ticker_order if ticker in missing_core_tickers]

    if len(markets) < 2 and tuple(ticker_order) != TOP_MARKET_CAP_PRIORITY_TICKERS:
        return None

    markets_arg = ",".join(markets)
    return {
        "id": "run_priority_e2e",
        "priority": 1,
        "requirements": missing_core_requirements,
        "tickers": selected_tickers,
        "description": (
            "Run the priority KR, US, and JP Top 10 source-backed E2E bootstrap "
            "before targeted repair actions."
        ),
        "cli_commands": [
            (
                "python -m services.ingestion_worker.cli run-priority-e2e "
                f"--markets {markets_arg} --years {years} --persist "
                "--continue-on-error --strict"
            )
        ],
        "github_actions": {
            "workflow": "ingestion-worker.yml",
            "command": "run_priority_e2e",
            "priority_e2e_markets": markets_arg,
            "persist": True,
            "force_refresh": False,
        },
        "market_order": markets,
        "coverage_scope": "top_market_cap_priority",
    }


def _remediation_prerequisites(
    ticker_reports: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    *,
    postgres_reachable: bool,
    postgres_error: str | None,
) -> list[dict[str, Any]]:
    prerequisites: list[dict[str, Any]] = []

    def add(name: str, required: bool, detail: str) -> None:
        if any(item["name"] == name for item in prerequisites):
            return
        prerequisites.append({"name": name, "required": required, "detail": detail})

    if not postgres_reachable:
        add(
            "DATA_BACKEND=postgres",
            True,
            "required before source coverage can query persisted Neon/Postgres rows",
        )
        add(
            "DATABASE_URL",
            True,
            (
                "required for --persist ingestion and source coverage"
                if postgres_error == "not_configured"
                else "database must be reachable before --persist ingestion"
            ),
        )

    markets = {_ticker_market(row["ticker"]) for row in ticker_reports}
    action_ids = {str(action["id"]) for action in actions}
    if "US" in markets and (
        "collect_sec_bulk" in action_ids
        or "normalize_us_batch" in action_ids
        or "run_priority_e2e" in action_ids
    ):
        add("SEC_USER_AGENT", True, "required for SEC EDGAR collection and normalization")
    if "KR" in markets and (
        "collect_opendart" in action_ids or "run_priority_e2e" in action_ids
    ):
        add(
            "OPENDART_API_KEY",
            True,
            "required for OpenDART collection; DART_API_KEY is accepted as an alias",
        )
    if "JP" in markets and (
        "collect_jquants" in action_ids or "run_priority_e2e" in action_ids
    ):
        add("JQUANTS credentials", True, "required for J-Quants quote and statement collection")
    if "JP" in markets and (
        "collect_edinet" in action_ids or "run_priority_e2e" in action_ids
    ):
        add("EDINET_API_KEY", True, "required for EDINET filing evidence collection")
    if "import_consensus_csv" in action_ids:
        add(
            "traceable forecast source CSV",
            True,
            "required for 1Y-5Y consensus forecast snapshots; do not synthesize estimates",
        )
    return prerequisites


def _dedupe_actions(actions: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for action in actions:
        key = (str(action["id"]), ",".join(action["tickers"]))
        deduped.setdefault(key, action)
    return list(deduped.values())


def _group_tickers_by_market(tickers: Iterable[str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for ticker in tickers:
        grouped.setdefault(_ticker_market(ticker), []).append(ticker)
    return grouped


def _ticker_market(ticker: str) -> str:
    if ticker.endswith(".KS") or ticker.endswith(".KQ"):
        return "KR"
    if ticker.endswith(".T"):
        return "JP"
    return "US"


def _source_metric_actions(market: str, tickers: list[str], years: str) -> list[dict[str, Any]]:
    if not tickers:
        return []
    joined = ",".join(tickers)
    if market == "US":
        return [
            {
                "id": "collect_sec_bulk",
                "priority": 10,
                "requirements": ["security", "source_evidence"],
                "tickers": tickers,
                "description": "Download SEC companyfacts and submissions archives.",
                "cli_commands": [
                    (
                        "python -m services.ingestion_worker.cli collect-sec-bulk "
                        "--archives companyfacts,submissions --persist"
                    )
                ],
                "github_actions": {
                    "workflow": "ingestion-worker.yml",
                    "command": "collect_sec_bulk",
                    "coverage_tickers": joined,
                    "persist": True,
                },
            },
            {
                "id": "load_sec_bulk_warehouse",
                "priority": 11,
                "requirements": ["security", "financial_metrics", "source_evidence"],
                "tickers": tickers,
                "description": "Parse SEC bulk archives into financial_facts and metric_values.",
                "cli_commands": [
                    (
                        "python -m services.ingestion_worker.cli load-sec-bulk-warehouse "
                        f"--tickers {joined} --persist"
                    )
                ],
                "github_actions": {
                    "workflow": "ingestion-worker.yml",
                    "command": "load_sec_bulk_warehouse",
                    "coverage_tickers": joined,
                    "persist": True,
                },
            },
        ]
    if market == "KR":
        return [
            {
                "id": "collect_opendart",
                "priority": 12,
                "requirements": ["security", "financial_metrics", "source_evidence"],
                "tickers": tickers,
                "description": "Collect OpenDART company financial statements for KR tickers.",
                "cli_commands": [
                    (
                        "python -m services.ingestion_worker.cli collect "
                        f"--market KR --ticker {ticker} --years {years} --persist"
                    )
                    for ticker in tickers
                ],
                "github_actions": {
                    "workflow": "ingestion-worker.yml",
                    "command": "collect_market",
                    "market": "KR",
                    "coverage_tickers": joined,
                    "persist": True,
                },
            },
            {
                "id": "import_fnguide_export",
                "priority": 13,
                "requirements": ["financial_metrics"],
                "tickers": tickers,
                "description": (
                    "Optionally import licensed/user-supplied FnGuide DataGuide exports."
                ),
                "cli_commands": [
                    (
                        "python -m services.ingestion_worker.cli import-fnguide-export "
                        "--path storage/imports/fnguide_dataguide.csv --persist"
                    )
                ],
                "github_actions": {
                    "workflow": "ingestion-worker.yml",
                    "command": "import_fnguide_export",
                    "coverage_tickers": joined,
                    "csv_path": "storage/imports/fnguide_dataguide.csv",
                    "persist": True,
                },
            },
        ]
    return [
        {
            "id": "collect_jquants",
            "priority": 12,
            "requirements": ["security", "financial_metrics", "source_evidence"],
            "tickers": tickers,
            "description": "Collect J-Quants statements, prices, and dividends for JP tickers.",
            "cli_commands": [
                (
                    "python -m services.ingestion_worker.cli collect-jquants "
                    f"--tickers {joined} --years {years} --persist"
                )
            ],
            "github_actions": {
                "workflow": "ingestion-worker.yml",
                "command": "collect_jquants",
                "coverage_tickers": joined,
                "persist": True,
            },
        },
        {
            "id": "collect_edinet",
            "priority": 13,
            "requirements": ["source_evidence"],
            "tickers": tickers,
            "description": "Collect EDINET metadata and CSV/XBRL evidence for JP filings.",
            "cli_commands": [
                (
                    "python -m services.ingestion_worker.cli collect-edinet "
                    f"--tickers {joined} --years {years} --persist"
                )
            ],
            "github_actions": {
                "workflow": "ingestion-worker.yml",
                "command": "collect_edinet",
                "coverage_tickers": joined,
                "persist": True,
            },
        },
    ]


def _adjusted_earnings_actions(
    market: str,
    tickers: list[str],
    years: str,
) -> list[dict[str, Any]]:
    if not tickers:
        return []
    joined = ",".join(tickers)
    if market == "US":
        return [
            {
                "id": "normalize_us_batch",
                "priority": 20,
                "requirements": ["adjusted_earnings"],
                "tickers": tickers,
                "description": "Run S1/S2/S4 adjusted operating EPS normalization.",
                "cli_commands": [
                    (
                        "python -m services.ingestion_worker.cli normalize-us-batch "
                        f"--tickers {joined} --years {years} --persist --continue-on-error"
                    )
                ],
                "github_actions": {
                    "workflow": "ingestion-worker.yml",
                    "command": "normalize_us_batch",
                    "coverage_tickers": joined,
                    "persist": True,
                },
            }
        ]
    return _source_metric_actions(market, tickers, years)


def _price_actions(market: str, tickers: list[str], years: str) -> list[dict[str, Any]]:
    joined = ",".join(tickers)
    if market == "KR":
        return [
            {
                "id": "collect_pykrx_prices",
                "priority": 30,
                "requirements": ["price_bars"],
                "tickers": tickers,
                "description": "Collect KR daily OHLCV price evidence with pykrx.",
                "cli_commands": [
                    (
                        "python -m services.ingestion_worker.cli collect-pykrx-prices "
                        f"--tickers {joined} --years {years} --persist"
                    )
                ],
                "github_actions": {
                    "workflow": "ingestion-worker.yml",
                    "command": "collect_pykrx_prices",
                    "coverage_tickers": joined,
                    "persist": True,
                },
            },
        ]
    stooq_market = "JP" if market == "JP" else "US"
    return [
        {
            "id": f"collect_stooq_prices_{market.lower()}",
            "priority": 30,
            "requirements": ["price_bars"],
            "tickers": tickers,
            "description": f"Collect {market} price bars from Stooq source files.",
            "cli_commands": [
                (
                    "python -m services.ingestion_worker.cli collect-stooq-prices "
                    f"--market {stooq_market} --tickers {joined} --years {years} --persist"
                )
            ],
            "github_actions": {
                "workflow": "ingestion-worker.yml",
                "command": "collect_stooq_prices",
                "market": stooq_market,
                "coverage_tickers": joined,
                "persist": True,
            },
        },
    ]


def _market_structure_actions(
    market: str,
    tickers: list[str],
    years: str,
) -> list[dict[str, Any]]:
    joined = ",".join(tickers)
    if market == "KR":
        return [
            {
                "id": "collect_marcap",
                "priority": 31,
                "requirements": ["price_bars", "market_cap", "listed_shares"],
                "tickers": tickers,
                "description": (
                    "Collect FinanceData marcap yearly parquet archives for KR close "
                    "price, market cap, listed shares, and rank evidence."
                ),
                "cli_commands": [
                    (
                        "python -m services.ingestion_worker.cli collect-marcap "
                        f"--tickers {joined} --years {years} --persist"
                    )
                ],
                "github_actions": {
                    "workflow": "ingestion-worker.yml",
                    "command": "collect_marcap",
                    "coverage_tickers": joined,
                    "persist": True,
                },
            }
        ]
    return [
        {
            "id": f"import_market_structure_csv_{market.lower()}",
            "priority": 31,
            "requirements": ["market_cap_evidence"],
            "tickers": tickers,
            "description": (
                f"Import source-backed {market} market-cap evidence using "
                "import-market-csv with optional market_cap and listed_shares columns."
            ),
            "cli_commands": [
                (
                    "python -m services.ingestion_worker.cli import-market-csv "
                    f"--path storage/imports/{market.lower()}_market_structure.csv --persist"
                )
            ],
            "github_actions": {
                "workflow": "ingestion-worker.yml",
                "command": "import_market_csv",
                "market": market,
                "coverage_tickers": joined,
                "csv_path": f"storage/imports/{market.lower()}_market_structure.csv",
                "persist": True,
            },
        },
    ]


def _int(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)
