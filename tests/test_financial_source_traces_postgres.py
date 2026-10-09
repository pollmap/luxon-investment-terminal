"""Opt-in fixture_non_production tests in a dedicated loopback PostgreSQL DB."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.engine import make_url
from test_financial_source_traces import observations

from services.market_dashboard.fact_store import FactStore
from services.market_dashboard.source_trace_store import FINANCIAL_FORMAT, FORMAT_KEY
from services.market_dashboard.store import Store

pytestmark = pytest.mark.integration


@pytest.fixture
def postgres_ledger(tmp_path, monkeypatch):
    value = os.getenv('MARKET_FINANCIAL_TRACE_TEST_POSTGRES_URL')
    if not value:
        pytest.skip('isolated financial trace PostgreSQL URL is not configured')
    url = make_url(value)
    if (url.host not in ('127.0.0.1', 'localhost')
            or not url.database.startswith('market_trace_test_financial_')):
        pytest.fail('financial trace tests require a dedicated local test database')
    monkeypatch.setenv('MARKET_SHARED_FINANCIAL_TRACES', '1')
    store = Store(value, tmp_path)
    ledger = FactStore(store)
    yield ledger
    store.engine.dispose()


def test_postgres_concurrent_writers_keep_clocks_ids_and_one_shared_trace(postgres_ledger):
    ledger = postgres_ledger
    company = 'fixture_' + uuid4().hex[:18]
    rows = observations(30)
    for row in rows:
        row['source_trace']['fixture_batch'] = company
    barrier = Barrier(4)

    def write(worker):
        store = Store(ledger.store.url, ledger.store.root)
        writer = FactStore(store)
        changed = [{**row, 'source_trace': {**row['source_trace'],
                   'collected_at': f'fixture-worker-{worker}'}} for row in rows]
        try:
            barrier.wait(timeout=10)
            return writer.append(company, changed)
        finally:
            store.engine.dispose()

    with ThreadPoolExecutor(4) as pool:
        inserted = list(pool.map(write, range(4)))
    assert sum(inserted) == 30
    first = ledger.read(company)
    assert len(first) == len({row['fact_id'] for row in first}) == 30
    assert {row['source_trace']['ingested_at'] for row in first} == {
        row['source_trace']['ingested_at'] for row in rows}
    assert len({row['source_trace']['collected_at'] for row in first}) == 1
    assert ledger.append(company, rows) == 0
    assert ledger.read(company) == first
    table = ledger.source_traces.table
    with ledger.store.read_connection() as db:
        assert db.execute(select(func.count()).select_from(table)
                          .where(table.c.payload.contains(company))).scalar_one() == 1
        stored = db.execute(select(ledger.facts.c.payload)
                            .where(ledger.facts.c.company_id == company)).scalars().all()
    assert all(FORMAT_KEY in body and FINANCIAL_FORMAT in body for body in stored)


def test_postgres_mid_batch_failure_rolls_back_facts_and_evidence(postgres_ledger):
    ledger = postgres_ledger
    company = 'fixture_' + uuid4().hex[:18]
    rows = observations(120)
    for row in rows:
        row['source_trace']['fixture_batch'] = company
    writes = 0

    def fail_second(_db, _cursor, statement, _parameters, _context, _many):
        nonlocal writes
        if statement.startswith('INSERT INTO market_financial_facts'):
            writes += 1
            if writes == 2:
                raise RuntimeError('fixture rollback')

    event.listen(ledger.store.engine, 'before_cursor_execute', fail_second)
    try:
        with pytest.raises(RuntimeError, match='fixture rollback'):
            ledger.append(company, rows)
    finally:
        event.remove(ledger.store.engine, 'before_cursor_execute', fail_second)
    assert ledger.read(company) == []
    table = ledger.source_traces.table
    with ledger.store.read_connection() as db:
        assert db.execute(select(func.count()).select_from(table)
                          .where(table.c.payload.contains(company))).scalar_one() == 0
