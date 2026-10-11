"""The real ASGI read routes share revision SQL, without freezing later updates."""

import asyncio
import threading

import httpx
import pytest

from services.market_dashboard import app as market
from services.market_dashboard.response_cache import (
    InFlightResponses,
    RevisionReadBatcher,
    RevisionResponseCache,
)
from services.market_dashboard.store import Store


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["search", "summary"])
async def test_hundred_requests_for_ten_keys_share_one_revision_query(tmp_path, monkeypatch, kind):
    store = Store(root=tmp_path)
    codes = [str(100000 + index) for index in range(10)]
    store.put(
        "universe",
        {
            "rows": [
                {
                    "code": code,
                    "name": "fixture_non_production" + code,
                    "market": "KOSPI",
                    "price": 100,
                    "as_of": "2026-10-02",
                    "source_trace": {},
                }
                for code in codes
            ]
        },
    )
    monkeypatch.setattr(market, "get_store", lambda: store)
    monkeypatch.setattr(market, "_revision_reads", RevisionReadBatcher())
    monkeypatch.setattr(market, "_search_responses", RevisionResponseCache())
    monkeypatch.setattr(market, "_detail_responses", RevisionResponseCache())
    monkeypatch.setattr(market, "_search_flights", InFlightResponses())
    monkeypatch.setattr(market, "_detail_flights", InFlightResponses())
    original, calls, lock = store.revisions, [], threading.Lock()

    def counted(keys):
        with lock:
            calls.append(tuple(keys))
        return original(keys)

    monkeypatch.setattr(store, "revisions", counted)
    paths = [
        market.BASE
        + ("/search?q=" + code if kind == "search" else "/securities/" + code + "?view=summary")
        for code in codes
    ]

    def selected(response):
        value = response.json()
        return value["rows"][0] if kind == "search" else value["security"]

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=market.app), base_url="http://fixture"
        ) as client:
            for path in paths:
                assert (await client.get(path)).status_code == 200
            calls.clear()
            results = await asyncio.gather(*(client.get(paths[index % 10]) for index in range(100)))
            assert all(response.status_code == 200 for response in results)
            assert len(calls) == 1
            if kind == "summary":
                # Summary caches follow only each requested security's facts and
                # quote; an unrelated security must not invalidate all summaries.
                expected_keys = {"universe"} | {
                    prefix + code
                    for code in codes
                    for prefix in ("detail-summary:", "enriched-item:", "quote:")
                }
            else:
                expected_keys = {"universe", "enriched", "quotes"}
            assert set(calls[0]) == expected_keys
            for index, result in enumerate(results):
                row = selected(result)
                assert (row["code"], row["price"], row["as_of"]) == (
                    codes[index % 10],
                    100,
                    "2026-10-02",
                )
            store.put(
                "quotes", {codes[0]: {"price": 200, "as_of": "2026-10-03", "quote_trace": {}}}
            )
            changed = selected(await client.get(paths[0]))
            assert (changed["price"], changed["as_of"]) == (200, "2026-10-03")
    finally:
        store.engine.dispose()
