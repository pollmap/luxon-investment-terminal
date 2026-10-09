"""Contract fixtures only; not production market observations."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from services.market_dashboard import app as market_app
from services.market_dashboard.providers import normalize_stock, trace
from services.market_dashboard.short_selling import (
    ShortSellingCollector,
    parse_rows,
    public_short_selling,
)
from services.market_dashboard.store import Store, now


def evidence():
    return trace("fixture_non_production", "https://example.invalid", "a" * 64, "2026-09-23")


def test_trades_preserve_zero_missing_and_units():
    rows = parse_rows(
        [
            {
                "TRD_DD": "2026/09/23",
                "CVSRTSELL_TRDVOL": "0",
                "CVSRTSELL_TRDVAL": "0",
                "TRDVOL_WT": "0",
            },
            {
                "TRD_DD": "2026/09/22",
                "CVSRTSELL_TRDVOL": "1,500",
                "CVSRTSELL_TRDVAL": "120,000,000",
                "TRDVOL_WT": "-",
            },
        ],
        "trades",
        evidence(),
    )
    assert rows[0]["volume"] == 1500
    assert rows[0]["value"] == 120000000
    assert rows[0]["ratio"] is None
    assert rows[1]["volume"] == rows[1]["ratio"] == 0


def test_balance_keeps_report_day_and_does_not_use_trade_volume():
    rows = parse_rows(
        [
            {
                "RPT_DUTY_OCCR_DD": "2026/09/21",
                "BAL_QTY": "20,000",
                "BAL_AMT": "400000000",
                "BAL_RTO": "0.4",
            }
        ],
        "balance",
        evidence(),
    )
    assert rows[0]["date"] == "2026-09-21"
    assert rows[0]["volume"] == 20000
    assert rows[0]["ratio"] == 0.4
    assert rows[0]["source_trace"]["period"] == "2026-09-21"


@pytest.mark.parametrize(
    "item",
    [
        {"TRD_DD": "invalid", "CVSRTSELL_TRDVOL": "1"},
        {"TRD_DD": "20260923", "CVSRTSELL_TRDVOL": "-1"},
        {"TRD_DD": "20260923", "CVSRTSELL_TRDVOL": "1", "TRDVOL_WT": "120"},
        {"TRD_DD": "20260923", "renamed_field": "1"},
    ],
)
def test_malformed_response_is_error_not_empty_success(item):
    with pytest.raises(ValueError):
        parse_rows([item], "trades", evidence())




@pytest.fixture
def collector(tmp_path):
    store = Store(url="sqlite://", root=tmp_path)
    return ShortSellingCollector(
        SimpleNamespace(store=store), None
    )






def test_empty_balances_do_not_erase_last_good(collector):
    collector.store.put(
        "short-selling:005930", {"balance": {"rows": [{"date": "2026-09-21", "volume": 123}]}}
    )
    collector.collect_krx = AsyncMock(return_value=([], "KRX"))
    result = asyncio.run(collector.refresh("005930"))
    assert "trades" not in result
    assert result["balance"]["status"] == "stale"
    assert result["balance"]["rows"][0]["volume"] == 123


def test_periods_do_not_forward_fill_balance_dates():
    result = public_short_selling(
        {
            "trades": {"rows": [{"date": "2026-09-23", "volume": 1}]},
            "balance": {"rows": [{"date": "2026-09-21", "volume": 7}]},
        },
        "month",
    )
    assert result["balance"]["rows"][0]["date"] == "2026-09-21"
    assert result["trades"]["rows"][0]["volume"] == 1


def test_endpoint_queues_known_symbols_and_rejects_bad_period(collector, monkeypatch):
    store = collector.store
    row = normalize_stock(
        {"itemCode": "005930", "stockName": "fixture_non_production"}, "KOSPI", evidence()
    )
    store.put("universe", {"rows": [row], "received_at": now()})
    monkeypatch.setattr(market_app, "get_store", lambda: store)
    market_app._rows_cache.clear()
    client = TestClient(market_app.app)
    url = "/api/market/v1/securities/005930/short-selling"
    assert client.get(url).json()["balance"]["status"] == "pending"
    assert "005930" in store.active()
    assert client.get(url + "?period=bad").status_code == 422
    assert client.get(url.replace("005930", "999999")).status_code == 404
