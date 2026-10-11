"""Opt-in integration against an explicitly isolated local PostgreSQL database."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from services.market_dashboard.source_trace_store import FORMAT, encode_json
from services.market_dashboard.store import Store
from services.market_dashboard.time_series import put_series

pytestmark = pytest.mark.integration


@pytest.fixture
def postgres_store(tmp_path, monkeypatch):
    value = os.getenv('MARKET_TRACE_TEST_POSTGRES_URL')
    if not value:
        pytest.skip('isolated local PostgreSQL URL is not configured')
    url = make_url(value)
    # Do not allow this test to run against the real market DB by accident.
    if (url.host not in ('127.0.0.1', 'localhost')
            or not url.database.startswith('market_trace_test_')):
        pytest.fail('source-trace integration requires a dedicated local test database')
    monkeypatch.setenv('MARKET_SHARED_SOURCE_TRACES', '1')
    store = Store(value, tmp_path)
    yield store
    store.engine.dispose()


def evidence(marker):
    return {'source': 'fixture_non_production', 'document': marker,
            'available_at': None, 'unit': '원', 'exact_string': '000001.23000',
            'source_url': 'https://example.invalid/정정',
            'input_fact_ids': [f'fixture-{i:04d}' for i in range(100)]}


def test_postgres_concurrent_dedup_roundtrip_and_inline_dual_read(postgres_store):
    store = postgres_store
    prefix = 'fixture:' + uuid4().hex
    a, b = evidence(prefix + ':a'), evidence(prefix + ':b')
    barrier = Barrier(4)

    def write(worker):
        writer = Store(store.url, store.root)
        rows = [{'date': f'2026-{i:06d}', 'close': i,
                 'source_trace': a if (worker + i) % 2 else b} for i in range(50)]
        try:
            barrier.wait(timeout=10)
            assert writer.time_series.upsert(f'{prefix}:{worker}', rows) == 50
            assert writer.time_series.rows(f'{prefix}:{worker}') == rows
            return rows
        finally:
            writer.engine.dispose()

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(4)))

    table = store.time_series.source_traces.table
    with store.read_connection() as db:
        counts = db.execute(select(func.count()).select_from(table)
                            .where(table.c.payload.contains(prefix))).scalar_one()
    assert counts == 2

    inline = {'date': '2027-01-01', 'close': 10, 'source_trace': a}
    store.time_series.shared_source_traces = False
    store.time_series.upsert(prefix, [inline])
    store.time_series.shared_source_traces = True
    referenced = {**inline, 'date': '2027-01-02'}
    store.time_series.upsert(prefix, [referenced])
    assert store.time_series.rows(prefix) == [inline, referenced]
    with store.read_connection() as db:
        stored = db.execute(select(store.time_series.table.c.payload)
            .where(store.time_series.table.c.series == prefix)
            .order_by(store.time_series.table.c.time)).scalars().all()
    assert stored[0] == encode_json(inline)
    assert FORMAT in stored[1]


def test_postgres_trace_and_row_rollback_together(postgres_store, monkeypatch):
    store = postgres_store
    prefix = 'fixture:' + uuid4().hex
    def fail(*_args, **_kwargs):
        raise RuntimeError('fixture cursor rollback')
    monkeypatch.setattr(store, 'put', fail)
    with pytest.raises(RuntimeError, match='fixture cursor rollback'):
        put_series(store, prefix, {'rows': [
            {'date': '2026-01-01', 'close': 1, 'source_trace': evidence(prefix)}]})
    assert store.time_series.rows(prefix) == []
    table = store.time_series.source_traces.table
    with store.read_connection() as db:
        assert db.execute(select(func.count()).select_from(table)
                          .where(table.c.payload.contains(prefix))).scalar_one() == 0
