"""Fixture-only pool exhaustion and independent projection reader regressions."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, event

from services.market_dashboard import enriched_view
from services.market_dashboard.enriched_view import PREFIX, read, read_item, rebuild
from services.market_dashboard.store import Store


def single_connection_pool(store):
    store.engine.dispose()
    store.engine = create_engine(
        store.url,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.5,
        connect_args={"check_same_thread": False},
    )


@pytest.mark.parametrize("projected", [False, True])
@pytest.mark.parametrize("reader", [read, lambda store: read_item(store, "005930")])
def test_legacy_fallback_returns_connection_before_snapshot_read(tmp_path, projected, reader):
    store = Store(root=tmp_path)
    before = {"005930": {"price": 100, "source": "fixture_non_production"}}
    after = {"005930": {"price": 200, "source": "fixture_non_production"}}
    store.put("enriched", before)
    if projected:
        rebuild(store)
        assert read(store) == before
    store.put("enriched", after)
    single_connection_pool(store)
    try:
        expected = after if reader is read else after["005930"]
        assert reader(store) == expected
    finally:
        store.engine.dispose()


def test_projection_decode_does_not_block_other_reads_with_one_connection(tmp_path, monkeypatch):
    store = Store(root=tmp_path)
    values = {
        "005930": {"price": 100, "projection_gate": True},
        "000660": {"price": 200},
    }
    store.put("enriched", values)
    store.put("unrelated", {"source": "fixture_non_production", "value": 1})
    rebuild(store)
    single_connection_pool(store)
    entered, release = threading.Event(), threading.Event()
    original = enriched_view.json.loads

    def delayed_decode(payload, *args, **kwargs):
        if '"projection_gate": true' in payload:
            entered.set()
            assert release.wait(3), "Projection test did not release the decoder"
        return original(payload, *args, **kwargs)

    monkeypatch.setattr(enriched_view.json, "loads", delayed_decode)
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            projected = pool.submit(read, store)
            try:
                assert entered.wait(2)
                # Both paths need the same one-connection pool. Store.get also
                # needs its cache lock, so neither may wait for the projection.
                assert pool.submit(read_item, store, "000660").result(timeout=1) == values["000660"]
                assert pool.submit(store.get, "unrelated").result(timeout=1)["value"] == 1
            finally:
                release.set()
            assert projected.result(timeout=2) == values
    finally:
        store.engine.dispose()


def test_interleaved_source_change_falls_back_without_caching_old_projection(tmp_path):
    store = Store(root=tmp_path)
    store.put("enriched", {"005930": {"price": 100}, "000660": {"price": 200}})
    rebuild(store)
    after = {"005930": {"price": 400, "source": "fixture_non_production"}}
    changed = False

    def interleave(_conn, _cursor, statement, parameters, _context, _many):
        nonlocal changed
        if (
            not changed
            and statement.startswith("SELECT")
            and "market_snapshots.payload" in statement.split("FROM")[0]
            and parameters
            and all(str(key).startswith(PREFIX) for key in parameters)
        ):
            changed = True
            store.put("enriched", after)

    event.listen(store.engine, "after_cursor_execute", interleave)
    try:
        assert read(store) == after
        assert changed
        assert getattr(store, "_enriched_view", None) is None
        assert read_item(store, "000660") == {}
        assert rebuild(store) == 1
        assert read(store) == after
    finally:
        event.remove(store.engine, "after_cursor_execute", interleave)
        store.engine.dispose()
