"""Fixture-only: single-security/connection reads must not build all quotes."""

import asyncio
import json
import threading

import pytest
from fastapi.testclient import TestClient

from services.market_dashboard import app as market
from services.market_dashboard.response_cache import RevisionResponseCache
from services.market_dashboard.store import Store


@pytest.fixture
def store(tmp_path, monkeypatch):
    value = Store("sqlite://", tmp_path)
    value.put(
        "universe",
        {
            "rows": [
                {
                    "code": "005930",
                    "name": "fixture_non_production",
                    "market": "KOSPI",
                    "price": 100,
                    "source_trace": {},
                },
                {
                    "code": "000660",
                    "name": "fixture_non_production",
                    "market": "KOSPI",
                    "price": 200,
                    "source_trace": {},
                },
                {
                    "code": "999999",
                    "name": "fixture_excluded",
                    "market": "KONEX",
                    "price": 300,
                    "source_trace": {},
                },
            ]
        },
    )
    monkeypatch.setattr(market, "get_store", lambda: value)
    monkeypatch.setattr(market, "_detail_responses", RevisionResponseCache())
    monkeypatch.setattr(market, "_body_cache", {})
    monkeypatch.setattr(market, "_rows_cache", {})
    monkeypatch.setattr(market, "_security_rows", market.OrderedDict())
    monkeypatch.delenv("MARKET_PUBLIC_ORIGIN", raising=False)
    yield value
    value.engine.dispose()


def reject_full_assembly(*args, **kwargs):
    raise AssertionError("single-security read assembled the whole universe")


def test_quote_matches_row_and_assembles_only_selected_security(store, monkeypatch):
    store.put("quotes", {"005930": {"price": 120, "as_of": "2026-10-08", "quote_trace": {}}})
    expected = market.assemble_rows(store)[0][0]
    sizes = []
    original = market.assemble_rows

    def selected(source, inputs=None):
        sizes.append(len(inputs[0]["rows"]))
        return original(source, inputs)

    monkeypatch.setattr(market, "assemble_rows", selected)
    monkeypatch.setattr(market, "rows_with_details", reject_full_assembly)
    response = TestClient(market.app).get(market.BASE + "/securities/005930/quote")
    assert response.status_code == 200 and response.json() == expected
    assert sizes == [1]


def test_financial_index_and_unknown_codes_do_not_require_market_quotes(store, monkeypatch):
    monkeypatch.setattr(market, "rows_with_details", reject_full_assembly)
    queued = []
    monkeypatch.setattr(store, "request", queued.append)
    # Without lifespan: this tests the request; initial shared stream population
    # is legitimately an all-market operation and is covered separately.
    client = TestClient(market.app)
    path = market.BASE + "/securities/005930/financials"
    response = client.get(path)
    assert response.status_code == 200 and response.json()["reports"] == []
    assert queued == ["005930"]
    for code, expected in [("999999", 404), ("123456", 404), ("not-a-code", 422)]:
        assert client.get(market.BASE + f"/securities/{code}/financials").status_code == expected
    assert queued == ["005930"]


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["quote", "financials"])
async def test_refresh_wait_occurs_after_body_sent(store, monkeypatch, endpoint):
    entered, release = threading.Event(), threading.Event()

    def blocked(code):
        assert code == "005930"
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(store, "request", blocked)
    monkeypatch.setattr(market, "rows_with_details", reject_full_assembly)
    messages = []

    async def receive():
        await asyncio.sleep(10)
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "method": "GET",
        "scheme": "http",
        "path": market.BASE + f"/securities/005930/{endpoint}",
        "query_string": b"",
        "headers": [],
        "server": ("fixture", 80),
        "client": ("127.0.0.1", 1),
        "root_path": "",
        "http_version": "1.1",
    }
    task = asyncio.create_task(market.app(scope, receive, send))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        assert messages[0]["status"] == 200
        assert any(
            m["type"] == "http.response.body" and not m.get("more_body", False) for m in messages
        )
        body = json.loads(b"".join(m.get("body", b"") for m in messages))
        assert body["price"] == 100 if endpoint == "quote" else body["reports"] == []
        assert not task.done()
    finally:
        release.set()
        await asyncio.wait_for(task, 3)


def test_new_stream_connection_uses_identity_filter_not_all_quote_assembly(store, monkeypatch):
    with TestClient(market.app) as client:
        monkeypatch.setattr(market, "rows_with_details", reject_full_assembly)
        with client.websocket_connect(market.BASE + "/stream") as ws:
            ws.send_json(
                {"codes": ["005930", "999999", "123456", "005930"], "channels": ["quotes"]}
            )
            first = ws.receive_json()
            assert first["type"] == "snapshot"
            assert [r["code"] for r in first["rows"]] == ["005930"]
