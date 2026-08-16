import pytest

from packages.core.universe import (
    JP_TOP_MARKET_CAP_PRIORITY_TICKERS,
    KR_TOP_MARKET_CAP_PRIORITY_TICKERS,
    SUPPORTED_PRIORITY_MARKETS,
    US_TOP_MARKET_CAP_PRIORITY_TICKERS,
    all_top_market_cap_priority_universes,
    priority_tickers_for_market,
    top_market_cap_rank_coverage_meta,
    top_market_cap_priority_universe,
)


@pytest.mark.parametrize(
    ("market", "expected_tickers", "currency"),
    [
        ("KR", KR_TOP_MARKET_CAP_PRIORITY_TICKERS, "KRW"),
        ("US", US_TOP_MARKET_CAP_PRIORITY_TICKERS, "USD"),
        ("JP", JP_TOP_MARKET_CAP_PRIORITY_TICKERS, "JPY"),
    ],
)
def test_market_priority_universe_is_contract_not_financial_rank(market, expected_tickers, currency):
    payload = top_market_cap_priority_universe(market)

    assert payload["market"] == market
    assert payload["currency"] == currency
    assert payload["data_mode"] == "source_backed_required"
    assert payload["rank_coverage_status"] == "coverage_contract_only"
    assert payload["rank_count"] == 0
    assert payload["rank_limit"] == 10
    assert payload["missing_rank_slots"] == 10
    assert len(payload["tickers"]) == 10

    tickers = [row["ticker"] for row in payload["tickers"]]
    assert tickers == list(expected_tickers)
    assert tickers == list(priority_tickers_for_market(market))
    assert len(set(tickers)) == 10

    for index, row in enumerate(payload["tickers"], start=1):
        assert row["coverage_priority_order"] == index
        assert row["rank_policy"] == "not_a_live_market_cap_rank"
        assert row["rank_coverage_status"] == "coverage_contract_only"
        assert row["rank_count"] == 0
        assert row["rank_limit"] == 10
        assert row["missing_rank_slots"] == 10
        assert "market_cap" not in row
        trace = row["source_trace"]
        assert trace["source_document_id"].startswith(f"nexus-{market.lower()}-top-market-cap-priority-universe")
        assert trace["method"] == "coverage_priority_contract"
        assert trace["currency"] == "N/A"
        assert trace["quality_status"] == "coverage_contract_not_financial_data"
        assert "requires_source_backed_rank_recompute" in trace["quality_flags"]
        assert "price, listed-share, market-cap, or exchange rows" in trace["formula"]
        assert trace["rank_source_url"].startswith("https://companiesmarketcap.com/")


def test_all_priority_universes_cover_three_markets_and_thirty_tickers():
    payload = all_top_market_cap_priority_universes()

    assert payload["markets"] == list(SUPPORTED_PRIORITY_MARKETS)
    assert payload["data_mode"] == "source_backed_required"
    assert payload["rank_coverage_status"] == "coverage_contract_only"
    assert payload["rank_count"] == 0
    assert payload["rank_limit"] == 30
    assert payload["missing_rank_slots"] == 30
    assert len(payload["universes"]) == 3
    assert sum(len(universe["tickers"]) for universe in payload["universes"]) == 30


def test_top_market_cap_rank_coverage_meta_marks_partial_rank():
    meta = top_market_cap_rank_coverage_meta(rank_count=3, rank_limit=10)

    assert meta["rank_coverage_status"] == "partial_top_market_cap_rank"
    assert meta["rank_count"] == 3
    assert meta["rank_limit"] == 10
    assert meta["missing_rank_slots"] == 7
    assert meta["quality_status"] == "partial_source_backed_market_cap_rank"
    assert meta["quality_flags"] == [
        "partial_market_cap_rank",
        "missing_rank_slots",
    ]


def test_top_market_cap_rank_coverage_meta_marks_complete_rank():
    meta = top_market_cap_rank_coverage_meta(rank_count=10, rank_limit=10)

    assert meta["rank_coverage_status"] == "complete_top_market_cap_rank"
    assert meta["rank_count"] == 10
    assert meta["rank_limit"] == 10
    assert meta["missing_rank_slots"] == 0
    assert meta["quality_status"] == "source_backed_market_cap_rank"
    assert meta["quality_flags"] == []
