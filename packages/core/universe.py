from __future__ import annotations

from copy import deepcopy
from typing import Any


MARKET_TOP_MARKET_CAP_PRIORITY_CONFIG: dict[str, dict[str, Any]] = {
    "KR": {
        "universe_id": "kr-top-market-cap-priority-v2",
        "label": "KR top-market-cap priority universe",
        "currency": "KRW",
        "rank_source_url": "https://companiesmarketcap.com/south-korea/largest-companies-in-south-korea-by-market-cap/",
        "rank_source_observed_at": "2026-06-28",
        "rank_source_note": (
            "Initial E2E collection priority from a public market-cap ranking reference. "
            "Production rank must be recomputed from source-backed KRX or marcap rows."
        ),
        "tickers": (
            ("005930.KS", "Samsung Electronics"),
            ("000660.KS", "SK hynix"),
            ("402340.KS", "SK Square"),
            ("005380.KS", "Hyundai Motor"),
            ("028260.KS", "Samsung C&T"),
            ("032830.KS", "Samsung Life Insurance"),
            ("373220.KS", "LG Energy Solution"),
            ("207940.KS", "Samsung Biologics"),
            ("329180.KS", "HD Hyundai Heavy Industries"),
            ("009155.KS", "Samsung Electro-Mechanics Preferred"),
        ),
    },
    "US": {
        "universe_id": "us-top-market-cap-priority-v1",
        "label": "US top-market-cap priority universe",
        "currency": "USD",
        "rank_source_url": "https://companiesmarketcap.com/usa/largest-companies-in-the-usa-by-market-cap/",
        "rank_source_observed_at": "2026-06-28",
        "rank_source_note": (
            "Initial listed-equity E2E collection priority from a public market-cap ranking reference. "
            "Non-listed or non-primary instruments are excluded from the product priority list."
        ),
        "tickers": (
            ("NVDA", "NVIDIA"),
            ("AAPL", "Apple"),
            ("GOOG", "Alphabet"),
            ("MSFT", "Microsoft"),
            ("AMZN", "Amazon"),
            ("AVGO", "Broadcom"),
            ("TSLA", "Tesla"),
            ("META", "Meta Platforms"),
            ("MU", "Micron Technology"),
            ("LLY", "Eli Lilly"),
        ),
    },
    "JP": {
        "universe_id": "jp-top-market-cap-priority-v1",
        "label": "JP top-market-cap priority universe",
        "currency": "JPY",
        "rank_source_url": "https://companiesmarketcap.com/japan/largest-companies-in-japan-by-market-cap/",
        "rank_source_observed_at": "2026-06-28",
        "rank_source_note": (
            "Initial E2E collection priority from a public market-cap ranking reference. "
            "ADR references are normalized to primary Tokyo listings where practical."
        ),
        "tickers": (
            ("285A.T", "Kioxia Holdings"),
            ("8306.T", "Mitsubishi UFJ Financial"),
            ("9984.T", "SoftBank Group"),
            ("8035.T", "Tokyo Electron"),
            ("7203.T", "Toyota Motor"),
            ("9983.T", "Fast Retailing"),
            ("8316.T", "Sumitomo Mitsui Financial Group"),
            ("6857.T", "Advantest"),
            ("6501.T", "Hitachi"),
            ("6981.T", "Murata Manufacturing"),
        ),
    },
}

SUPPORTED_PRIORITY_MARKETS = tuple(MARKET_TOP_MARKET_CAP_PRIORITY_CONFIG)
KR_TOP_MARKET_CAP_PRIORITY_TICKERS = tuple(
    ticker for ticker, _ in MARKET_TOP_MARKET_CAP_PRIORITY_CONFIG["KR"]["tickers"]
)
US_TOP_MARKET_CAP_PRIORITY_TICKERS = tuple(
    ticker for ticker, _ in MARKET_TOP_MARKET_CAP_PRIORITY_CONFIG["US"]["tickers"]
)
JP_TOP_MARKET_CAP_PRIORITY_TICKERS = tuple(
    ticker for ticker, _ in MARKET_TOP_MARKET_CAP_PRIORITY_CONFIG["JP"]["tickers"]
)
TOP_MARKET_CAP_PRIORITY_TICKERS = (
    KR_TOP_MARKET_CAP_PRIORITY_TICKERS
    + US_TOP_MARKET_CAP_PRIORITY_TICKERS
    + JP_TOP_MARKET_CAP_PRIORITY_TICKERS
)

KR_TOP_MARKET_CAP_PRIORITY_NAMES = dict(MARKET_TOP_MARKET_CAP_PRIORITY_CONFIG["KR"]["tickers"])
KR_TOP_MARKET_CAP_UNIVERSE_NOTE = MARKET_TOP_MARKET_CAP_PRIORITY_CONFIG["KR"]["rank_source_note"]
KR_TOP_MARKET_CAP_PRIORITY_UNIVERSE_ID = MARKET_TOP_MARKET_CAP_PRIORITY_CONFIG["KR"]["universe_id"]


def comma_join(values: tuple[str, ...] | list[str]) -> str:
    return ",".join(values)


def top_market_cap_priority_source_trace(market: str) -> dict[str, Any]:
    market_key = market.upper()
    config = _priority_market_config(market_key)
    source_document_id = f"nexus-{market_key.lower()}-top-market-cap-priority-universe-v1"
    if market_key == "KR":
        source_document_id = "nexus-kr-top-market-cap-priority-universe-v2"
    return {
        "source": "NEXUS_PRODUCT_PRIORITY_CONTRACT",
        "source_type": "product_priority_universe_contract",
        "source_document_id": source_document_id,
        "filing_id": f"NEXUS-{market_key}-TOP-MARKET-CAP-PRIORITY",
        "period": "initial_coverage",
        "available_at": f"{config['rank_source_observed_at']}T00:00:00+09:00",
        "unit": "ticker_list",
        "currency": "N/A",
        "method": "coverage_priority_contract",
        "formula": (
            "coverage_priority_order is a deterministic product collection order; "
            "production market-cap rank must be recomputed from source-backed "
            "price, listed-share, market-cap, or exchange rows before display as rank."
        ),
        "quality_status": "coverage_contract_not_financial_data",
        "quality_flags": [
            "not_live_market_cap_rank",
            "requires_source_backed_rank_recompute",
        ],
        "rank_source_url": config["rank_source_url"],
        "rank_source_observed_at": config["rank_source_observed_at"],
    }


def top_market_cap_priority_universe(market: str = "KR") -> dict[str, Any]:
    """Return a market collection-priority contract without financial values."""

    market_key = market.upper()
    config = _priority_market_config(market_key)
    source_trace = top_market_cap_priority_source_trace(market_key)
    rank_limit = len(config["tickers"])
    tickers = []
    for index, (ticker, name) in enumerate(config["tickers"], start=1):
        trace = deepcopy(source_trace)
        trace["fact_id"] = f"{config['universe_id']}:{ticker}"
        trace["ticker"] = ticker
        tickers.append(
            {
                "ticker": ticker,
                "name": name,
                "market": market_key,
                "currency": config["currency"],
                "coverage_priority_order": index,
                "rank_policy": "not_a_live_market_cap_rank",
                "rank_coverage_status": "coverage_contract_only",
                "rank_count": 0,
                "rank_limit": rank_limit,
                "missing_rank_slots": rank_limit,
                "source_trace": trace,
            }
        )

    return {
        "universe_id": config["universe_id"],
        "label": config["label"],
        "market": market_key,
        "currency": config["currency"],
        "data_mode": "source_backed_required",
        "rank_coverage_status": "coverage_contract_only",
        "rank_count": 0,
        "rank_limit": rank_limit,
        "missing_rank_slots": rank_limit,
        "note": config["rank_source_note"],
        "source_trace": deepcopy(source_trace),
        "tickers": tickers,
    }


def all_top_market_cap_priority_universes() -> dict[str, Any]:
    universes = [top_market_cap_priority_universe(market) for market in SUPPORTED_PRIORITY_MARKETS]
    rank_limit = sum(int(universe["rank_limit"]) for universe in universes)
    source_trace = {
        "source": "NEXUS_PRODUCT_PRIORITY_CONTRACT",
        "source_type": "product_priority_universe_contract",
        "source_document_id": "nexus-global-top-market-cap-priority-universe-v1",
        "filing_id": "NEXUS-GLOBAL-TOP-MARKET-CAP-PRIORITY-V1",
        "period": "initial_coverage",
        "available_at": "2026-06-28T00:00:00+09:00",
        "unit": "ticker_list",
        "currency": "N/A",
        "method": "coverage_priority_contract",
        "formula": (
            "global_priority_universe = deterministic concatenation of KR, US, and JP "
            "coverage-priority contracts; production ranks must be recomputed from "
            "source-backed market-cap rows."
        ),
        "quality_status": "coverage_contract_not_financial_data",
        "quality_flags": [
            "not_live_market_cap_rank",
            "requires_source_backed_rank_recompute",
        ],
    }
    return {
        "universe_id": "global-top-market-cap-priority-v1",
        "label": "KR/US/JP top-market-cap priority universes",
        "markets": list(SUPPORTED_PRIORITY_MARKETS),
        "data_mode": "source_backed_required",
        "rank_coverage_status": "coverage_contract_only",
        "rank_count": 0,
        "rank_limit": rank_limit,
        "missing_rank_slots": rank_limit,
        "note": "Thirty-stock E2E priority contract. No financial rank is displayed until source-backed market-cap rows are loaded.",
        "source_trace": source_trace,
        "universes": universes,
    }


def top_market_cap_rank_coverage_meta(
    rank_count: int,
    rank_limit: int = 10,
) -> dict[str, Any]:
    """Describe whether a source-backed market-cap rank fully covers its target size."""

    bounded_count = max(0, int(rank_count))
    bounded_limit = max(1, int(rank_limit))
    missing_rank_slots = max(0, bounded_limit - bounded_count)
    is_complete = missing_rank_slots == 0
    return {
        "rank_coverage_status": (
            "complete_top_market_cap_rank"
            if is_complete
            else "partial_top_market_cap_rank"
        ),
        "rank_count": bounded_count,
        "rank_limit": bounded_limit,
        "missing_rank_slots": missing_rank_slots,
        "quality_status": (
            "source_backed_market_cap_rank"
            if is_complete
            else "partial_source_backed_market_cap_rank"
        ),
        "quality_flags": [] if is_complete else [
            "partial_market_cap_rank",
            "missing_rank_slots",
        ],
    }


def kr_top_market_cap_priority_universe() -> dict[str, Any]:
    return top_market_cap_priority_universe("KR")


def priority_tickers_for_market(market: str) -> tuple[str, ...]:
    config = _priority_market_config(market.upper())
    return tuple(ticker for ticker, _ in config["tickers"])


def is_top_market_cap_priority_ticker(ticker: str) -> bool:
    return ticker.upper() in TOP_MARKET_CAP_PRIORITY_TICKERS


def priority_universe_for_ticker(ticker: str) -> dict[str, Any] | None:
    normalized = ticker.upper()
    for market in SUPPORTED_PRIORITY_MARKETS:
        universe = top_market_cap_priority_universe(market)
        if any(row["ticker"] == normalized for row in universe["tickers"]):
            return universe
    return None


def priority_universe_row_for_ticker(ticker: str) -> dict[str, Any] | None:
    normalized = ticker.upper()
    universe = priority_universe_for_ticker(normalized)
    if universe is None:
        return None
    return next(row for row in universe["tickers"] if row["ticker"] == normalized)


def _priority_market_config(market: str) -> dict[str, Any]:
    try:
        return MARKET_TOP_MARKET_CAP_PRIORITY_CONFIG[market]
    except KeyError as exc:
        allowed = ", ".join(SUPPORTED_PRIORITY_MARKETS)
        raise ValueError(f"Unsupported priority universe market: {market}. Allowed: {allowed}") from exc
