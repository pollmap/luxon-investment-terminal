"""Fixture-only regressions for bounded reads and independent refresh writes."""
import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from sqlalchemy import event
from starlette.requests import Request

from services.market_dashboard import app as market
from services.market_dashboard.enriched_view import read_item, rebuild
from services.market_dashboard.response_cache import RevisionResponseCache
from services.market_dashboard.store import Store
from services.market_dashboard.time_series import put_series


def row(code, price=100, market_name='KOSPI'):
    return {'code': code, 'name': 'fixture_non_production', 'market': market_name,
            'price': price, 'as_of': '2026-10-02', 'source_trace': {}}


@pytest.fixture
def store(tmp_path, monkeypatch):
    value = Store('sqlite://', tmp_path)
    value.put('universe', {'rows': [row('005930'), row('000660', 200)]})
    monkeypatch.setattr(market, 'get_store', lambda: value)
    monkeypatch.setattr(market, '_detail_responses', RevisionResponseCache())
    yield value
    value.engine.dispose()


def test_one_security_assembles_one_row_and_matches_market_list(store, monkeypatch):
    store.put('enriched', {'005930': {'per': 12, 'facts': {'per': {'value': 12}}}})
    store.put('quotes', {'005930': {'price': 120, 'as_of': '2026-10-03', 'quote_trace': {}}})
    expected = market.assemble_rows(store)[0][0]
    assembled = []
    original = market.assemble_rows

    def bounded(source, inputs=None):
        assembled.append(len(inputs[0]['rows']))
        return original(source, inputs)

    monkeypatch.setattr(market, 'assemble_rows', bounded)
    assert market.detail_snapshot(store, '005930')['security'] == expected
    assert assembled == [1]
    store.put('universe', {'rows': [row('005930', market_name='KONEX')]})
    with pytest.raises(market.HTTPException) as error:
        market.detail_snapshot(store, '005930')
    assert error.value.status_code == 404


def test_large_universe_is_decoded_once_for_distinct_cold_symbols_and_search(store, monkeypatch):
    # Production universe exceeded the 32 MiB decoded-snapshot LRU limit.
    # A deliberately tiny limit reproduces that boundary without a 39 MB fixture.
    codes = [f'{100000 + i:06d}' for i in range(10)]
    store.put('universe', {'rows': [row(code) for code in codes]})
    store.cache_max_bytes = 64
    reads = []
    original = store.get
    lock = threading.Lock()

    def counted(key, *args, **kwargs):
        if key == 'universe':
            with lock:
                reads.append(key)
        return original(key, *args, **kwargs)

    monkeypatch.setattr(store, 'get', counted)
    barrier = threading.Barrier(10)

    def cold_read(code):
        barrier.wait(timeout=3)
        return market.security_row(store, code)['code']

    with ThreadPoolExecutor(max_workers=10) as pool:
        assert list(pool.map(cold_read, codes)) == codes
    assert 'universe' not in store.cache  # General LRU still respects its memory budget.
    assert market.search_payload('fixture', 12)['total'] == 10
    assert len(reads) == 1
    cache = market._security_universe_cache
    assert cache[2]['rows'][0] is cache[3][codes[0]]  # No second universe copy.

    store.put('universe', {'rows': [row(codes[0], 200)]})
    assert market.security_row(store, codes[0])['price'] == 200
    with pytest.raises(market.HTTPException) as error:
        market.security_row(store, codes[1])
    assert error.value.status_code == 404
    assert market.search_payload('fixture', 12)['total'] == 1
    assert len(reads) == 2
    assert len(market._security_universe_cache[3]) == 1


@pytest.mark.parametrize('source', ['quotes', 'enriched', 'universe'])
def test_other_security_changes_do_not_rebuild_selected_summary(store, monkeypatch, source):
    builds = []
    original = market.detail_snapshot

    def counted(*args, **kwargs):
        builds.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(market, 'detail_snapshot', counted)
    with TestClient(market.app) as client:
        path = market.BASE + '/securities/005930?view=summary'
        first = client.get(path)
        if source == 'universe':
            store.put(source, {'rows': [row('005930'), row('000660', 900)]})
        else:
            store.put(source, {'000660': {'price': 900, 'quote_trace': {}, 'as_of': '2026-10-03'}})
        same = client.get(path)
        assert same.json() == first.json()
        assert len(builds) == 1
        if source == 'universe':
            store.put(source, {'rows': [row('005930', 110), row('000660', 900)]})
        else:
            store.put(source, {'005930': {'price': 110, 'quote_trace': {}, 'as_of': '2026-10-03'}})
        changed = client.get(path)
        assert changed.json()['security']['price'] == 110
        assert changed.headers['etag'] != first.headers['etag']
        assert len(builds) == 2


def test_enrichment_item_avoids_wide_snapshot_but_legacy_writes_remain_fresh(store, monkeypatch):
    store.put('enriched', {'005930': {'per': 10}, '000660': {'per': 20}})
    rebuild(store)
    original = store.get

    def no_wide_read(key, *args, **kwargs):
        assert key != 'enriched'
        return original(key, *args, **kwargs)

    monkeypatch.setattr(store, 'get', no_wide_read)
    assert read_item(store, '005930') == {'per': 10}
    assert read_item(store, '999999') == {}
    monkeypatch.setattr(store, 'get', original)
    store.put('enriched', {'005930': {'per': 30}})
    assert read_item(store, '005930') == {'per': 30}
    assert read_item(store, '000660') == {}


def test_refresh_waiting_on_db_does_not_block_snapshot_cache(tmp_path, monkeypatch):
    # File SQLite uses independent connections, like PostgreSQL. In-memory
    # StaticPool must retain its shared-connection guard and is tested separately.
    source = Store(root=tmp_path)
    source.put('fixture', {'last_good': 1})
    assert source.get('fixture') == {'last_good': 1}
    entered, release = threading.Event(), threading.Event()
    original = source.engine.begin

    @contextmanager
    def slow_write():
        entered.set()
        assert release.wait(3)
        with original() as db:
            yield db

    monkeypatch.setattr(source.engine, 'begin', slow_write)
    with ThreadPoolExecutor(max_workers=3) as pool:
        request = pool.submit(source.request, '005930')
        try:
            assert entered.wait(2)
            assert pool.submit(source.get, 'fixture').result(timeout=1) == {'last_good': 1}
            assert pool.submit(source.request, '005930').result(timeout=1) is None
        finally:
            release.set()
        assert request.result(timeout=2) is True
    source.engine.dispose()


def test_read_releases_pool_connection_before_waiting_for_cache_lock(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    source = Store(root=tmp_path)
    source.put('fixture', {'last_good': 1})
    source.engine.dispose()
    source.engine = create_engine(source.url, pool_size=1, max_overflow=0, pool_timeout=1,
                                  connect_args={'check_same_thread': False})
    waiting = threading.Event()
    original = source._cached

    def cached(key):
        waiting.set()
        return original(key)

    monkeypatch.setattr(source, '_cached', cached)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with source.lock:
            read = pool.submit(source.get, 'fixture')
            assert waiting.wait(2)
            # The reader is waiting for this lock, but must have returned the
            # sole connection so this opposite-order caller can make progress.
            with source.read_connection() as db:
                assert db.exec_driver_sql('SELECT 1').scalar_one() == 1
        assert read.result(timeout=2) == {'last_good': 1}
    source.engine.dispose()


def test_concurrent_process_refresh_upserts_do_not_race(tmp_path):
    stores = [Store(root=tmp_path) for _ in range(4)]
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert all(pool.map(lambda source: source.request('005930'), stores))
        with stores[0].read_connection() as db:
            assert len(db.execute(stores[0].jobs.select()).all()) == 1
    finally:
        for source in stores:
            source.engine.dispose()


@pytest.mark.asyncio
async def test_warm_detail_checks_all_dependencies_in_one_small_query(store):
    request = Request({'type': 'http', 'method': 'GET', 'path': '/', 'headers': []})
    await market.security('005930', request, BackgroundTasks(), view='summary')
    statements = []

    def record(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    event.listen(store.engine, 'before_cursor_execute', record)
    try:
        result = await market.security('005930', request, BackgroundTasks(), view='summary')
        assert result.status_code == 200
    finally:
        event.remove(store.engine, 'before_cursor_execute', record)
    assert len(statements) == 1
    assert 'updated_at' in statements[0] and 'payload' not in statements[0]


@pytest.mark.asyncio
async def test_summary_body_is_sent_before_slow_refresh_finishes(store, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    sent = asyncio.Event()
    messages = []

    def slow_refresh(code):
        assert code == '005930'
        entered.set()
        assert release.wait(3)

    monkeypatch.setattr(store, 'request', slow_refresh)
    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
             'method': 'GET', 'scheme': 'http', 'path': market.BASE + '/securities/005930',
             'raw_path': (market.BASE + '/securities/005930').encode(),
             'query_string': b'view=summary', 'headers': [],
             'client': ('127.0.0.1', 1), 'server': ('testserver', 80)}

    async def receive():
        await asyncio.Event().wait()

    async def send(message):
        messages.append(message)
        if message['type'] == 'http.response.body' and not message.get('more_body', False):
            sent.set()

    task = asyncio.create_task(market.app(scope, receive, send))
    try:
        await asyncio.wait_for(sent.wait(), 2)
        assert await asyncio.to_thread(entered.wait, 1)
        assert not task.done()
        assert messages[0]['status'] == 200
        body = b''.join(m.get('body', b'') for m in messages)
        assert json.loads(body)['security']['price'] == 100
    finally:
        release.set()
        await asyncio.wait_for(task, 2)


def test_period_prices_reads_only_requested_history(store, monkeypatch):
    key = 'bars:005930:통합:day'
    values = [{'date': '1990-01-02', 'time': '1990-01-02T09:00:00+09:00', 'close': 1},
              {'date': '2026-09-25', 'time': '2026-09-25T09:00:00+09:00', 'close': 100},
              {'date': '2026-10-02', 'time': '2026-10-02T09:00:00+09:00', 'close': 110}]
    put_series(store, key, {'rows': values, 'venue': '통합', 'adjustment': 'split'})
    reads = []
    original = store.time_series.rows

    def bounded(series, reverse=False, **options):
        assert options.get('start') == '2026-09-25'
        result = original(series, reverse, **options)
        reads.append(len(result))
        return result

    monkeypatch.setattr(store.time_series, 'rows', bounded)
    result = market.prices('005930', 'week')
    assert result['rows'] == values[1:]
    assert result['venue'] == '통합' and result['adjustment'] == 'split'
    assert result['row_count'] == 3 and reads == [2]  # Coverage remains the full history.


def test_in_memory_refresh_preserves_existing_snapshot_transaction(store):
    statements = []

    def record(_conn, _cursor, statement, _params, _context, _many):
        if 'market_jobs' in statement:
            statements.append(statement)

    event.listen(store.engine, 'before_cursor_execute', record)
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: store.request('005930'), range(30)))
        assert len(statements) == 1 and statements[0].startswith('INSERT')
        assert store.active() == ['005930']
        assert store.get('universe')['rows'][0]['price'] == 100
    finally:
        event.remove(store.engine, 'before_cursor_execute', record)
