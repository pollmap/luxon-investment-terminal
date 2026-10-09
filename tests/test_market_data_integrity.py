"""Isolated fixtures: never promote partial or missing observations to zero."""

import asyncio

import httpx
import pytest

from services.market_dashboard.analysis import trailing_twelve_months
from services.market_dashboard.dart_periods import normalize_period_accounts
from services.market_dashboard.fact_store import FactStore
from services.market_dashboard.providers import PublicProvider
from services.market_dashboard.store import Store, now
from services.market_dashboard.trading import public_trading


def test_one_sided_broker_rank_is_not_zero_or_net():
    snapshot = {
        "brokers": [
            {"buy_name": "A", "buy_volume": 100, "sell_name": "B", "sell_volume": 30},
            {"buy_name": "C", "buy_volume": 0, "sell_name": "A", "sell_volume": 20},
        ]
    }
    result = {r["name"]: r for r in public_trading(snapshot)["brokers"]}
    assert result["A"]["net"] == 80
    assert result["B"]["buy"] is None and result["B"]["net"] is None
    assert result["C"]["buy"] == 0 and result["C"]["sell"] is None
    assert public_trading({})["program"] is None


def test_quote_fallback_preserves_last_good_and_does_not_duplicate_stream(tmp_path):
    async def check():
        store = Store(url="sqlite://", root=tmp_path)
        provider = PublicProvider(store)
        previous = {
            "price": 100,
            "change": 1,
            "received_at": now(),
            "quote_trace": {"source": "toss_websocket"},
        }
        store.put("quotes", {"005930": previous})
        calls = []

        async def fetch(url):
            calls.append(url)
            return httpx.Response(
                200, json={"itemCode": "005930", "closePrice": "0", "fluctuationsRatio": "0"}
            ), "fixture"

        provider.fetch = fetch
        try:
            assert await provider.quote("005930") == previous
            assert calls == []
            previous["quote_trace"] = {"source": "fixture_non_production"}
            store.put("quotes", {"005930": previous})
            with pytest.raises(ValueError, match="Invalid quote"):
                await provider.quote("005930")
            assert store.get("quotes")["005930"]["price"] == 100
        finally:
            await provider.client.aclose()

    asyncio.run(check())


def test_large_amounts_stay_exact_through_normalization_storage_and_ttm(tmp_path):
    source = {
        "sj_div": "IS",
        "fs_div": "CFS",
        "account_nm": "순이익",
        "currency": "KRW",
        "rcept_no": "20260314000001",
        "thstrm_dt": "2025.01.01 ~ 2025.12.31",
        "thstrm_amount": "123,456,789,123,456,789",
    }
    annual = normalize_period_accounts([source], "https://example.invalid/fixture", "fixture")[0]
    assert annual["exact_value"] == "123456789123456789"
    store = Store(url="sqlite://", root=tmp_path)
    facts = FactStore(store)
    facts.append("fixture", [annual])
    assert facts.read("fixture")[0]["value"] == "123456789123456789"
    assert (
        facts.append(
            "fixture",
            [{**annual, "source_trace": {**annual["source_trace"], "received_at": "2099-01-01"}}],
        )
        == 0
    )
    dates = [("01-01", "03-31"), ("04-01", "06-30"), ("07-01", "09-30"), ("10-01", "12-31")]
    quarters = [
        {
            **annual,
            "fiscal_start": "2025-01-01",
            "period_start": "2025-" + start,
            "period_end": "2025-" + end,
            "cumulative": False,
        }
        for start, end in dates
    ]
    result = trailing_twelve_months(quarters)["rows"][0]
    assert result["exact_value"] == "493827156493827156"
