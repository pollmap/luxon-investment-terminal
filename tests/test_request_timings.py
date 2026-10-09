"""Request-local diagnostics: fixture-only, no load or production access."""

import asyncio
import gzip
import importlib
import json
import re
import threading

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import Response

from services.market_dashboard import request_timings as timings
from services.market_dashboard.response_cache import (
    InFlightResponses,
    RevisionReadBatcher,
    RevisionResponseCache,
)
from services.market_dashboard.store import Store

market = importlib.import_module("services.market_dashboard.app")


class RevisionStore:
    def __init__(self):
        self.calls = 0

    def revisions(self, keys):
        self.calls += 1
        return {key: "r1" for key in keys}


def test_context_isolation_reset_and_thread_propagation():
    async def one(amount):
        with timings.collect() as record:
            await asyncio.sleep(0)
            await timings.to_thread(timings.count, "rows_built", amount)
            assert record.counts["rows_built"] == amount
            assert record.counts["executor_wait"] == 1
        assert timings._current.get() is None
        return record

    async def scenario():
        records = await asyncio.gather(one(2), one(9))
        assert [record.counts["rows_built"] for record in records] == [2, 9]

    asyncio.run(scenario())
    with pytest.raises(RuntimeError), timings.collect() as failed:
        with timings.stage("response_build"):
            raise RuntimeError("fixture")
    assert failed.counts == {"response_build": 1}
    assert timings._current.get() is None


def test_fixed_headers_never_include_identifiers_and_are_bounded():
    record = timings.RequestTimings()
    with pytest.raises(ValueError):
        record.add("SELECT secret FROM table", 1)
    with pytest.raises(ValueError):
        record.count("005930", 1)
    with pytest.raises(ValueError):
        record.count("rows_built", -1)
    record.add("revision_db", 10**10)
    record.count("rows_built", 10**20)
    assert record.headers() == (
        "revision_db;dur=99999999.00", "revision_db=1, rows_built=999999999"
    )


def test_lock_timer_excludes_work_inside_lock(monkeypatch):
    clock = [1.0]
    monkeypatch.setattr(timings.time, "perf_counter", lambda: clock[0])

    class Lock:
        released = False

        def acquire(self):
            clock[0] += 0.025

        def release(self):
            self.released = True

    lock = Lock()
    with timings.collect() as record:
        with pytest.raises(RuntimeError), timings.timed_lock(lock, "rows_lock"):
            clock[0] += 5
            raise RuntimeError("fixture")
    assert lock.released
    assert record.durations["rows_lock"] == pytest.approx(25)


def test_shared_revision_work_belongs_only_to_creator():
    async def scenario():
        batcher, store = RevisionReadBatcher(), RevisionStore()

        async def read():
            with timings.collect() as record:
                result = await batcher.read(store, ("private-key",))
                assert result == {"private-key": "r1"}
                return record

        records = await asyncio.gather(*(read() for _ in range(20)))
        assert store.calls == 1
        assert sum(r.counts.get("revision_db", 0) for r in records) == 1
        assert sum(r.counts.get("executor_wait", 0) for r in records) == 1
        assert sum(r.counts.get("revision_join", 0) for r in records) == 19
        assert all(r.counts["revision_wait"] == 1 for r in records)
        assert all("private-key" not in str(r.headers()) for r in records)
        assert all("revision_db" not in r.durations for r in records[1:])

    asyncio.run(scenario())


def test_cancelled_creator_does_not_leak_shared_work_to_follower():
    async def scenario():
        cache = InFlightResponses()
        entered, release = threading.Event(), threading.Event()
        records = {}

        def build():
            with timings.stage("response_build"):
                entered.set()
                assert release.wait(3)
                timings.count("rows_built", 7)
                return b"unchanged"

        async def read(name):
            with timings.collect() as record:
                records[name] = record
                return await cache.run("private-key", build)

        first = asyncio.create_task(read("creator"))
        assert await asyncio.to_thread(entered.wait, 2)
        second = asyncio.create_task(read("follower"))
        await asyncio.sleep(0)
        first.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await first
        finally:
            release.set()
        assert await second == b"unchanged"
        assert records["creator"].counts["rows_built"] == 7
        assert records["follower"].counts == {"flight_join": 1, "flight_wait": 1}
        assert timings._current.get() is None
        assert not cache.tasks

    asyncio.run(scenario())


def test_response_cache_keeps_bytes_and_rechecks_without_replaying_build_timings():
    store, cache = RevisionStore(), RevisionResponseCache()
    payload = {"price": 100, "as_of": "2026-10-02", "fixture_non_production": True}
    expected = gzip.compress(json.dumps(
        payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode(), compresslevel=6, mtime=0)
    with timings.collect() as first:
        assert cache.response(store, "summary", ("private-key",), lambda: payload) == expected
    with timings.collect() as second:
        def unexpected():
            raise AssertionError("cached content must be reused")
        assert cache.response(store, "summary", ("private-key",), unexpected) == expected
    assert first.counts["response_build"] == first.counts["response_encode"] == 1
    assert second.counts == {"response_lock": 1, "revision_db": 1, "response_hit": 1}
    assert "response_encode" not in second.durations


def test_failed_build_records_stage_but_never_enters_cache():
    store, cache = RevisionStore(), RevisionResponseCache()

    def failed():
        raise RuntimeError("fixture")

    with timings.collect() as record:
        with pytest.raises(RuntimeError, match="fixture"):
            cache.response(store, "summary", ("private-key",), failed)
    assert record.counts["response_build"] == 1
    assert "response_encode" not in record.counts
    assert not cache.entries
    assert timings._current.get() is None


def test_row_counters_distinguish_assembly_from_reuse():
    universe = {"rows": [
        {"code": code, "name": "fixture_non_production", "price": 100,
         "as_of": "2026-10-02", "source_trace": {}}
        for code in ("005930", "000660")
    ]}
    inputs = (universe, {}, {})
    with timings.collect() as first:
        rows, _ = market.assemble_rows(None, inputs)
    assert first.counts["rows_built"] == 2
    assert first.counts["rows_reused"] == 0
    with timings.collect() as second:
        changed, _ = market.assemble_rows(
            None, (universe, {"000660": {"per": 15}}, {}), previous=(inputs, rows)
        )
    assert second.counts["rows_built"] == second.counts["rows_reused"] == 1
    assert changed[0] is rows[0]
    assert changed[1]["per"] == 15


@pytest.mark.parametrize("path,method,enabled", [
    ("/search", "GET", True),
    ("/securities/005930?view=summary", "GET", True),
    ("/securities/005930?view=summary", "HEAD", True),
    ("/search", "POST", False),
    ("/search-index", "GET", False),
    ("/securities/005930", "GET", False),
    ("/securities/005930?view=fundamentals", "GET", False),
    ("/securities/005930/analysis/valuation", "GET", False),
    ("/health", "GET", False),
])
def test_only_search_and_summary_collect_work(path, method, enabled):
    async def scenario():
        route, _, query = path.partition("?")
        request = Request({"type": "http", "method": method, "path": market.BASE + route,
                           "query_string": query.encode(), "headers": []})

        async def endpoint(_):
            await timings.to_thread(timings.count, "rows_built", 3)
            return Response(b'{"price":100}', headers={"ETag": 'W/"fixture"'})

        response = await market.public_validation_clock(request, endpoint)
        assert response.body == b'{"price":100}'
        assert response.headers["etag"] == 'W/"fixture"'
        assert ("x-market-work" in response.headers) == enabled
        assert ("executor_wait" in response.headers.get("server-timing", "")) == enabled
        assert timings._current.get() is None

    asyncio.run(scenario())


def test_websocket_never_enters_http_measurement():
    app = FastAPI()
    app.middleware("http")(market.public_validation_clock)

    @app.websocket(market.BASE + "/stream")
    async def websocket(socket: WebSocket):
        await socket.accept()
        assert timings._current.get() is None
        await timings.to_thread(timings.count, "rows_built", 7)
        await socket.send_text("fixture")
        await socket.close()

    with TestClient(app) as client:
        with client.websocket_connect(market.BASE + "/stream") as socket:
            assert socket.receive_text() == "fixture"


def test_actual_routes_keep_data_and_etag_with_fresh_request_timings(tmp_path, monkeypatch):
    from contextlib import contextmanager

    store = Store(url="sqlite://", root=tmp_path)
    store.put("universe", {"rows": [{"code": "005930", "name": "fixture_non_production",
                                    "price": 100, "as_of": "2026-10-02", "source_trace": {}}]})
    monkeypatch.setattr(market, "get_store", lambda: store)
    monkeypatch.setattr(market, "_detail_responses", RevisionResponseCache())
    monkeypatch.setattr(market, "_search_responses", RevisionResponseCache())
    monkeypatch.setattr(market, "_rows_cache", {})

    @contextmanager
    def disabled(_enabled=True):
        yield None

    try:
        with TestClient(market.app) as client:
            for path in ("/search?q=005930", "/securities/005930?view=summary"):
                first = client.get(market.BASE + path)
                second = client.get(market.BASE + path)
                assert first.status_code == second.status_code == 200
                assert "response_build=1" in first.headers["x-market-work"]
                assert "response_hit=1" in second.headers["x-market-work"]
                assert "response_build=" not in second.headers["x-market-work"]
                assert "response_encode;" not in second.headers["server-timing"]
                assert re.fullmatch(r"[a-z0-9_=, ]+", second.headers["x-market-work"])
                with monkeypatch.context() as patch:
                    patch.setattr(timings, "collect", disabled)
                    unmeasured = client.get(market.BASE + path)
                assert first.content == second.content == unmeasured.content
                assert first.headers.get("etag") == unmeasured.headers.get("etag")
                assert first.headers["cache-control"] == unmeasured.headers["cache-control"]
                assert "x-market-work" not in unmeasured.headers
    finally:
        store.engine.dispose()
