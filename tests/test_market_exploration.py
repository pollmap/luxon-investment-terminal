"""Isolated source fixtures: alignment, null handling, export and worker recovery."""

import asyncio
import csv
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from services.market_dashboard import app as market_app
from services.market_dashboard.exploration import compare_prices, screener_csv, sector_summary
from services.market_dashboard.history_worker import refresh_history
from services.market_dashboard.providers import normalize_stock, trace
from services.market_dashboard.store import Store, now


@pytest.fixture
def store(tmp_path):
    return Store(url="sqlite://", root=tmp_path)


def stock(code="005930", **values):
    row = normalize_stock(
        {"itemCode": code, "stockName": "fixture_non_production", "closePriceRaw": "100"},
        "KOSPI",
        trace("fixture", "https://example.invalid", "a" * 64, "2026-09-24"),
    )
    return {**row, **values}


def test_sector_includes_companies_with_missing_quotes():
    result = sector_summary(
        [
            stock(sector="반도체", change=10),
            stock("000660", sector="반도체", change=None),
            stock("100000", sector="철강", change=None),
        ]
    )
    by_name = {r["name"]: r for r in result}
    assert by_name["반도체"]["count"] == 2
    assert by_name["반도체"]["change"] == 10
    assert by_name["철강"]["change"] is None


def test_comparison_uses_exact_common_dates_and_no_fill():
    histories = {
        "005930": {
            "rows": [
                {"date": "2026-09-21", "close": 100},
                {"date": "2026-09-22", "close": 150},
                {"date": "2026-09-23", "close": 200},
            ]
        },
        "000660": {
            "rows": [{"date": "2026-09-22", "close": 50}, {"date": "2026-09-23", "close": 100}]
        },
    }
    result = compare_prices([stock(), stock("000660")], histories)
    assert result["start"] == "2026-09-22"
    assert result["status"] == "ready"
    assert result["series"][0]["points"][0]["value"] == 0
    assert result["series"][0]["points"][-1]["value"] == pytest.approx(100 / 3)
    assert result["series"][1]["points"][-1]["value"] == 100
    assert result["series"][0]["source_trace"]["input_fact_ids"]
    partial = compare_prices([stock(), stock("000660")], {"005930": histories["005930"]})
    assert partial["status"] == "partial"
    assert partial["pending"] == ["000660"]


def test_csv_preserves_numeric_values_and_neutralizes_formulas():
    payload = screener_csv([stock(name=" =HYPERLINK(1)", change=-3.2, cap=None)])
    rows = list(csv.reader(io.StringIO(payload.decode("utf-8-sig"))))
    assert rows[1][0] == "005930"
    assert rows[1][1].startswith("'")
    assert "-3.2" in rows[1]
    assert "" in rows[1]


def test_export_ignores_pagination_but_preserves_filters(store, monkeypatch):
    monkeypatch.setattr(market_app, "get_store", lambda: store)
    rows = [stock(f"{n:06d}", change=1, sector="반도체") for n in range(205)]
    rows.append(stock("999999", change=-1, sector="철강"))
    store.put("universe", {"rows": rows})
    with TestClient(market_app.app) as client:
        response = client.post(
            market_app.BASE + "/screener/export",
            json={"sector": "반도체", "offset": 100, "limit": 1},
        )
        assert response.status_code == 200
        assert len(list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))) == 206
        assert client.get(market_app.BASE + "/sectors?market=INVALID").status_code == 422
        assert client.get(market_app.BASE + "/compare?codes=000000").status_code == 422
        quote = client.get(market_app.BASE + "/securities/000000/quote").json()
        assert quote["code"] == "000000" and "prices" not in quote


def test_history_failure_preserves_old_data_and_backs_off(store):
    previous = {
        "rows": [{"date": "2020-01-01", "close": 100}],
        "received_at": "2020-01-01T00:00:00+00:00",
    }
    store.put("prices:005930", previous)
    provider = SimpleNamespace(prices=AsyncMock(side_effect=RuntimeError("fixture failure")))
    assert asyncio.run(refresh_history(store, provider, "005930")) == "error"
    assert store.get("prices:005930") == previous
    assert store.get("collection:005930")["sources"]["history"]["status"] == "stale"
    assert asyncio.run(refresh_history(store, provider, "005930")) == "backoff"
    assert provider.prices.await_count == 1


def test_history_success_is_not_refetched_or_archived_again(store, monkeypatch):
    snapshot = {"rows": [{"date": "2026-09-23", "close": 100}], "received_at": now(), "requested_count": 20000}

    async def fetch(code):
        store.put("prices:" + code, snapshot)
        return snapshot

    provider = SimpleNamespace(prices=AsyncMock(side_effect=fetch))
    monkeypatch.setattr(store, "archive", lambda *args: None)
    assert asyncio.run(refresh_history(store, provider, "005930")) == "ready"
    assert asyncio.run(refresh_history(store, provider, "005930")) == "current"
    assert provider.prices.await_count == 1


def test_snapshot_cache_is_bounded_and_never_hides_a_new_revision(store):
    store.cache_max_bytes = 1000
    for n in range(50):
        store.put(str(n), {"text": "a" * 200})
        assert store.get(str(n))["text"] == "a" * 200
    assert store.cache_bytes <= 1000
    assert len(store.cache) < 5
    assert store.get("0")["text"] == "a" * 200
    store.put("0", {"text": "changed"})
    assert store.get("0")["text"] == "changed"
    assert store.cache_bytes == sum(store.cache_sizes.values())


def test_market_read_model_reuses_large_inputs_but_observes_new_quotes(store, monkeypatch):
    store.put("universe", {"rows": [stock(price=100)], "received_at": now()})
    monkeypatch.setattr(market_app, "get_store", lambda: store)
    market_app._rows_cache.clear()
    original_get = store.get
    fetched = []

    def counted(key, default=None, **kwargs):
        fetched.append(key)
        return original_get(key, default, **kwargs)

    monkeypatch.setattr(store, "get", counted)
    first = market_app.rows_with_details()[0]
    assert first[0]["price"] == 100
    record = market_app._rows_cache[id(store)]
    market_app._rows_cache[id(store)] = (0, *record[1:])
    fetched.clear()
    assert market_app.rows_with_details()[0] is first
    assert fetched == []
    store.put(
        "quotes",
        {"005930": {"price": 123, "as_of": "2026-09-25", "quote_trace": first[0]["source_trace"]}},
    )
    record = market_app._rows_cache[id(store)]
    market_app._rows_cache[id(store)] = (0, *record[1:])
    assert market_app.rows_with_details()[0][0]["price"] == 123
    assert fetched == ["quotes"]


def test_sqlite_revision_reads_use_covering_index_and_parameterized_keys(store):
    key = "quote'); DROP TABLE market_snapshots;--"
    store.put(key, {"price": 100})
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("SELECT"):
            statements.append((statement, parameters))

    event.listen(store.engine, "before_cursor_execute", capture)
    try:
        previous = store.revisions([key])
        assert store.revisions([]) == {}
        assert store.get(key) == {"price": 100}
    finally:
        event.remove(store.engine, "before_cursor_execute", capture)
    revision_queries = [(sql, params) for sql, params in statements if "INDEXED BY" in sql]
    assert len(revision_queries) == 2
    with store.engine.connect() as db:
        for sql, params in revision_queries:
            assert key not in sql
            plan = db.exec_driver_sql("EXPLAIN QUERY PLAN " + sql, params).all()
            assert any("COVERING INDEX market_snapshot_revision_idx" in row[3] for row in plan)
    store.put(key, {"price": 123})
    assert store.revisions([key])[key] != previous[key]
    assert store.get(key) == {"price": 123}


def test_background_queues_share_identity_cache_and_notice_new_listings(store, monkeypatch):
    store.put("universe", {"rows": [{"code": "005930"}]})
    original_get = store.get
    fetched = []

    def counted(key, default=None):
        fetched.append(key)
        return original_get(key, default)

    monkeypatch.setattr(store, "get", counted)
    assert store.listed_codes() == ("005930",)
    assert store.listed_codes() == ("005930",)
    assert fetched == ["universe"]
    store.put("universe", {"rows": [{"code": "005930"}, {"code": "000660"}]})
    assert store.listed_codes() == ("000660", "005930")
    assert fetched == ["universe", "universe"]
