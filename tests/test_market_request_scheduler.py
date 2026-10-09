"""No provider/network calls: verify requested data cannot wait behind startup work."""
import asyncio
from types import SimpleNamespace

import pytest

from services.market_dashboard import toss_market
from services.market_dashboard.store import Store, now
from services.market_dashboard.toss_market import TossMarket, public_snapshot, request_market


def fixture_market(tmp_path):
    store = Store('sqlite://', tmp_path)
    store.put('universe', {'rows': [{'code': '005930', 'market': 'KOSPI', 'name': 'fixture_non_production',
                                   'source_trace': {'source': 'fixture_non_production'}}]})
    return TossMarket(SimpleNamespace(store=store, client=object())), store


@pytest.mark.asyncio
async def test_new_sector_request_runs_while_scheduled_batch_is_waiting(tmp_path, monkeypatch):
    market, store = fixture_market(tmp_path)
    scheduled_started, requested_finished, never = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cancelled = []
    monkeypatch.setattr(toss_market, 'REQUEST_POLL_SECONDS', 0.01)

    async def slow_scheduled_batch():
        scheduled_started.set()
        try:
            await never.wait()
        finally:
            cancelled.append(True)

    async def sector_detail(sector_id):
        assert sector_id == '169'
        market.save('toss-sector:169', {'id': sector_id, 'name': 'fixture'}, {})
        requested_finished.set()

    monkeypatch.setattr(market, 'run_scheduled', slow_scheduled_batch)
    monkeypatch.setattr(market, 'sector_detail', sector_detail)
    task = asyncio.create_task(market.run())
    try:
        await asyncio.wait_for(scheduled_started.wait(), 1)
        assert request_market(store, 'toss-sector:169', 'sector_detail', sector_id='169')
        await asyncio.wait_for(requested_finished.wait(), 1)
        assert not never.is_set()
        assert public_snapshot(store, 'toss-sector:169')['status'] == 'ready'
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert cancelled == [True]


@pytest.mark.asyncio
async def test_requested_and_scheduled_same_job_share_one_provider_call(tmp_path, monkeypatch):
    market, store = fixture_market(tmp_path)
    calls = []

    async def ranking(**params):
        calls.append(params)
        await asyncio.sleep(0.01)
        market.save('toss-sector-ranking:MARKET_TRADING_AMOUNT:1d', {'rows': []}, {})

    monkeypatch.setattr(market, 'sector_ranking', ranking)
    await asyncio.gather(*[
        market.run_job('toss-sector-ranking:MARKET_TRADING_AMOUNT:1d', 60, 'sector_ranking')
        for _ in range(2)
    ])
    assert len(calls) == 1
    assert store.get('toss-market-job:toss-sector-ranking:MARKET_TRADING_AMOUNT:1d')['status'] == 'ready'


@pytest.mark.asyncio
async def test_timeout_stops_pending_and_preserves_last_good(tmp_path, monkeypatch):
    market, store = fixture_market(tmp_path)
    monkeypatch.setattr(toss_market, 'JOB_TIMEOUT_SECONDS', 0.01)

    async def stalled(**params):
        await asyncio.Event().wait()

    monkeypatch.setattr(market, 'sector_detail', stalled)
    store.put('toss-sector:169', {'id': '169', 'name': 'last-good', 'status': 'ready'})
    await market.run_job('toss-sector:169', 3600, 'sector_detail', sector_id='169')
    assert public_snapshot(store, 'toss-sector:169') == {'id': '169', 'name': 'last-good', 'status': 'stale'}
    assert store.get('toss-market-job:toss-sector:169')['error'] == 'TimeoutError'
    await market.run_job('toss-sector:170', 3600, 'sector_detail', sector_id='170')
    assert public_snapshot(store, 'toss-sector:170') == {'status': 'error'}


def test_bad_or_expired_request_does_not_hide_valid_sector_request(tmp_path):
    market, store = fixture_market(tmp_path)
    for key, payload in {
        'bad': {'key': 'bad', 'kind': [], 'params': {}, 'requested_at': now()},
        'private': {'key': 'private', 'kind': 'orders', 'params': {}, 'requested_at': now()},
        'expired': {'key': 'expired', 'kind': 'sector_detail', 'params': {}, 'requested_at': '2000-01-01T00:00:00+00:00'},
        'outside': {'key': 'outside', 'kind': 'warnings', 'params': {'code': '999999'}, 'requested_at': now()},
        '169': {'key': 'toss-sector:169', 'kind': 'sector_detail', 'params': {'sector_id': '169'}, 'requested_at': now()},
    }.items():
        store.put('toss-market-request:' + key, payload)
    assert market.requested_jobs() == [('toss-sector:169', 3600, 'sector_detail', {'sector_id': '169'})]


@pytest.mark.asyncio
async def test_active_summary_prefetch_does_not_schedule_invisible_warning_calls(tmp_path, monkeypatch):
    market, store = fixture_market(tmp_path)
    store.request('005930')  # Same interest marker as summary reads / prefetch.
    assert store.active() == ['005930']
    called = []

    class TurnFinished(Exception):
        pass

    async def record(key, ttl, method, **params):
        called.append((key, ttl, method, params))

    async def end_turn(*args):
        raise TurnFinished

    monkeypatch.setattr(market, 'run_job', record)
    monkeypatch.setattr(toss_market.asyncio, 'sleep', end_turn)
    with pytest.raises(TurnFinished):
        await market.run_scheduled()
    assert not any(method == 'warnings' for _, _, method, _ in called)
    assert ('toss-limits:005930', 3600, 'limits', {'code': '005930'}) in called
    assert market.requested_jobs() == []


@pytest.mark.asyncio
async def test_visible_warnings_endpoint_queues_read_and_preserves_ttl_and_failure(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from services.market_dashboard import app as market_app

    market, store = fixture_market(tmp_path)
    monkeypatch.setattr(market_app, 'get_store', lambda: store)
    with TestClient(market_app.app) as client:
        result = client.get('/api/market/v1/securities/005930/warnings')
    assert result.status_code == 200
    jobs = market.requested_jobs()
    assert jobs == [('toss-warnings:005930', 60, 'warnings', {'code': '005930'})]
    calls = []

    async def warnings(code):
        calls.append(code)
        market.save('toss-warnings:' + code, {'rows': [{'name': '투자주의'}]}, {})

    monkeypatch.setattr(market, 'warnings', warnings)
    key, ttl, method, params = jobs[0]
    await market.run_job(key, ttl, method, **params)
    await market.run_job(key, ttl, method, **params)
    assert calls == ['005930']  # Fresh 60-second result is reused.
    assert public_snapshot(store, key)['rows'] == [{'name': '투자주의'}]

    async def failing(code):
        raise ValueError('fixture_provider_failure')

    store.put('toss-market-job:' + key, {'status': 'ready', 'attempted_at': '2000-01-01T00:00:00+00:00'})
    monkeypatch.setattr(market, 'warnings', failing)
    await market.run_job(key, ttl, method, **params)
    assert public_snapshot(store, key) == {'rows': [{'name': '투자주의'}],
        'status': 'stale', 'received_at': store.get(key)['received_at']}
    assert store.get('toss-market-job:' + key)['status'] == 'error'
