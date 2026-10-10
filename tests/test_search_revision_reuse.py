"""Fixture-only search head reuse; real SQLite reads, no provider or production I/O."""

import asyncio
import gzip
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from sqlalchemy import event

from services.market_dashboard import app as market
from services.market_dashboard.response_cache import (
    InFlightResponses,
    RevisionReadBatcher,
    RevisionResponseCache,
)
from services.market_dashboard.store import Store

KEYS = ("universe", "enriched", "quotes")


@pytest.fixture
def source(tmp_path, monkeypatch):
    store = Store(root=tmp_path)
    store.put("universe", {"rows": [
        {"code": str(100000 + i), "name": f"fixture_non_production {i}",
         "market": "KOSPI", "price": 100 + i, "value": 1000 - i,
         "as_of": "2026-10-02", "source_trace": {"source": "fixture_non_production"}}
        for i in range(10)
    ]})
    store.put("enriched", {})
    store.put("quotes", {})
    monkeypatch.setattr(market, "get_store", lambda: store)
    monkeypatch.setattr(market, "_rows_cache", {})
    monkeypatch.setattr(market, "_security_universe_cache", None)
    monkeypatch.setattr(market, "_search_responses", RevisionResponseCache())
    monkeypatch.setattr(market, "_search_flights", InFlightResponses())
    monkeypatch.setattr(market, "_revision_reads", RevisionReadBatcher())
    yield store
    store.engine.dispose()


def count_revisions(source, monkeypatch):
    calls = []
    original = source.revisions

    def counted(keys):
        calls.append(tuple(keys))
        return original(keys)

    monkeypatch.setattr(source, "revisions", counted)
    return calls


def test_exact_heads_reuse_rows_without_refreshing_timed_cache(source, monkeypatch):
    expected = market.rows_with_details(check_revision=True)
    heads = source.revisions(KEYS)
    saved = market._rows_cache[id(source)]
    calls = count_revisions(source, monkeypatch)
    assert market.rows_with_details(check_revision=True, known_revisions=heads) is expected
    assert not calls
    assert market._rows_cache[id(source)] is saved
    # A caller without a current observation must still recheck the database.
    assert market.rows_with_details(check_revision=True) is expected
    assert calls == [KEYS]


def test_no_rows_cache_still_reads_database(source, monkeypatch):
    heads = source.revisions(KEYS)
    calls = count_revisions(source, monkeypatch)
    assert market.search_payload("100000", 12, known_revisions=heads)["rows"][0]["price"] == 100
    assert KEYS in calls


@pytest.mark.parametrize("change", ["quote", "empty_quotes", "delete_quotes", "empty_universe"])
def test_changed_or_deleted_inputs_do_not_reuse_old_rows(source, monkeypatch, change):
    source.put("quotes", {"100000": {"price": 150, "as_of": "2026-10-03",
                                     "quote_trace": {"source": "fixture_non_production"}}})
    market.rows_with_details(check_revision=True)
    if change == "quote":
        source.put("quotes", {"100000": {"price": 200, "as_of": "2026-10-04",
                                         "quote_trace": {"source": "fixture_non_production"}}})
    elif change == "empty_quotes":
        source.put("quotes", {})
    elif change == "delete_quotes":
        with source.engine.begin() as db:
            db.execute(source.snapshots.delete().where(source.snapshots.c.key == "quotes"))
    else:
        source.put("universe", {"rows": []})
    heads = source.revisions(KEYS)
    calls = count_revisions(source, monkeypatch)
    result = market.search_payload("100000", 12, known_revisions=heads)
    assert KEYS in calls
    if change == "empty_universe":
        assert result == {"total": 0, "rows": []}
    else:
        assert result["rows"][0]["price"] == (200 if change == "quote" else 100)


def test_old_observation_cannot_replace_a_newer_cached_generation(source, monkeypatch):
    market.rows_with_details(check_revision=True)
    old = source.revisions(KEYS)
    source.put("quotes", {"100000": {"price": 200, "as_of": "2026-10-04",
                                     "quote_trace": {"source": "fixture_non_production"}}})
    current = market.rows_with_details(check_revision=True)
    new_heads = source.revisions(KEYS)
    calls = count_revisions(source, monkeypatch)
    actual = market.rows_with_details(check_revision=True, known_revisions=old)
    assert actual is current
    assert calls == [KEYS]
    assert market._rows_cache[id(source)][2] == new_heads


@pytest.mark.asyncio
@pytest.mark.parametrize("remove", [False, True])
async def test_write_during_search_preserves_post_build_check(source, monkeypatch, remove):
    market.rows_with_details(check_revision=True)
    original = market.search_payload
    entered, release = threading.Event(), threading.Event()

    def paused(*args, **kwargs):
        result = original(*args, **kwargs)
        entered.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(market, "search_payload", paused)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=market.app),
                                 base_url="http://fixture") as client:
        request = asyncio.create_task(client.get(market.BASE + "/search?q=100000"))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            if remove:
                source.put("universe", {"rows": []})
            else:
                source.put("quotes", {"100000": {"price": 200, "as_of": "2026-10-04",
                                                 "quote_trace": {"source": "fixture"}}})
        finally:
            release.set()
        first = await request
        assert first.json()["rows"][0]["price"] == 100  # The original observation.
        assert not market._search_responses.entries  # Never cached as the new revision.
        assert first.headers["cache-control"] == "no-store"
        second = await client.get(market.BASE + "/search?q=100000")
        if remove:
            assert second.json()["rows"] == []
        else:
            assert second.json()["rows"][0]["price"] == 200
        assert len(market._search_responses.entries) == 1


@pytest.mark.asyncio
async def test_route_passes_observed_heads_and_keeps_response_cache_hits(source, monkeypatch):
    market.rows_with_details(check_revision=True)
    original = market.search_payload
    heads_seen = []

    def observed(*args, **kwargs):
        heads_seen.append(kwargs["known_revisions"])
        return original(*args, **kwargs)

    monkeypatch.setattr(market, "search_payload", observed)
    calls = count_revisions(source, monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=market.app),
                                 base_url="http://fixture") as client:
        first = await client.get(market.BASE + "/search?q=100000")
        assert len(calls) == 2  # Initial heads and the post-build validation only.
        second = await client.get(market.BASE + "/search?q=100000")
        assert len(calls) == 3  # A new request still observes current heads.
    assert first.content == second.content
    assert first.headers["cache-control"] == second.headers["cache-control"] == "no-store"
    assert first.json()["rows"][0]["as_of"] == "2026-10-02"
    assert len(heads_seen) == 1


def test_ten_distinct_builds_avoid_ten_locked_revision_queries(source, capsys):
    """One paired local trial; injected 2ms DB delay is not production latency."""
    market.rows_with_details(check_revision=True)
    heads = source.revisions(KEYS)
    observations = []
    queries, guard = [], threading.Lock()

    def count_sql(_conn, _cursor, sql, _parameters, _context, _many):
        if sql.startswith("SELECT key, updated_at FROM market_snapshots"):
            with guard:
                queries.append(sql)
            time.sleep(0.002)  # Deterministic fixture cost on actual SQLite SQL.

    event.listen(source.engine, "before_cursor_execute", count_sql)
    try:
        for reuse in (False, True):
            cache = RevisionResponseCache()
            barrier = threading.Barrier(10)
            queries.clear()

            def build(i, barrier=barrier, cache=cache, reuse=reuse):
                barrier.wait(timeout=5)
                return cache.response(
                    source, ("fixture", i), KEYS,
                    lambda: market.search_payload(str(100000 + i), 12,
                                                   known_revisions=heads if reuse else None),
                    initial_revision=heads,
                )

            started, cpu = time.perf_counter(), time.process_time()
            with ThreadPoolExecutor(max_workers=10) as pool:
                bodies = list(pool.map(build, range(10)))
            observations.append({"reuse": reuse, "revision_sql": len(queries),
                                 "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                                 "cpu_ms": round((time.process_time() - cpu) * 1000, 3),
                                 "bodies": bodies})
    finally:
        event.remove(source.engine, "before_cursor_execute", count_sql)
    assert [item["revision_sql"] for item in observations] == [20, 10]
    assert observations[0]["bodies"] == observations[1]["bodies"]
    for item in observations:
        for body in item.pop("bodies"):
            assert json.loads(gzip.decompress(body))["total"] == 1
    with capsys.disabled():
        print("SEARCH_REUSE_FIXTURE " + json.dumps({"fixture_non_production": True,
              "injected_sql_delay_ms": 2, "results": observations}))
