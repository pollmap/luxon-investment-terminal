"""Synthetic fixtures only: requested market history, cursor proof and recovery."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.market_dashboard import toss_market
from services.market_dashboard.store import Store, now
from services.market_dashboard.toss_market import TossMarket, request_market


TRACE = {'source': 'fixture_non_production'}


def record(day, buy='100', sell='30'):
    return {'date': day, 'individual': {'buyAmount': buy, 'sellAmount': sell}}


def fixture_market(tmp_path):
    store = Store('sqlite://', tmp_path)
    client = SimpleNamespace(get=AsyncMock())
    return TossMarket(SimpleNamespace(store=store, client=client)), store, client


def seed(store, interval='1w', *, code='KOSPI'):
    key = f'toss-index-investors:{code}:{interval}'
    store.put(key, {'rows': [{'date': '2026-09-28', 'individual': {'buy': 100, 'sell': 30, 'net': 70}}],
                    'next_until': '2026-09-27', 'backfill_complete': False,
                    'status': 'ready', 'received_at': now(), 'source_trace': TRACE})
    return key


@pytest.mark.asyncio
async def test_only_requested_intervals_rotate_one_history_page_per_scheduled_turn(tmp_path, monkeypatch):
    market, store, _ = fixture_market(tmp_path)
    for interval in ('1w', '1mo', '1y'):
        key = seed(store, interval)
        request_market(store, key, 'index_investors', code='KOSPI', interval=interval)
    # A stored but never requested combination must not fan out collection.
    seed(store, '1w', code='KOSDAQ')
    # An earlier actual request remains a backfill intent after the tab is closed.
    key = 'toss-index-investors:KOSPI:1y'
    request = store.get('toss-market-request:' + key)
    store.put('toss-market-request:' + key, {**request, 'requested_at': '2000-01-01T00:00:00+00:00'})
    calls, turns = [], []

    class Finished(Exception):
        pass

    async def run_job(key, ttl, method, **params):
        if params.get('resume'):
            calls.append((key, ttl, method, params))

    async def end_turn(_):
        turns.append(len(calls))
        if len(turns) == 3:
            raise Finished

    monkeypatch.setattr(market, 'run_job', run_job)
    monkeypatch.setattr(toss_market.asyncio, 'sleep', end_turn)
    with pytest.raises(Finished):
        await market.run_scheduled()
    assert turns == [1, 2, 3]
    assert {params['interval'] for _, _, _, params in calls} == {'1w', '1mo', '1y'}
    assert all(params['code'] == 'KOSPI' and params['resume'] for _, _, _, params in calls)
    assert all(ttl == 5 and method == 'index_investors' for _, ttl, method, _ in calls)


@pytest.mark.asyncio
async def test_head_refresh_keeps_deep_cursor_and_explicit_final_page_closes_it(tmp_path):
    market, store, client = fixture_market(tmp_path)
    key = seed(store)
    client.get.side_effect = [
        ({'records': [record('2026-10-05')], 'nextUntil': '2026-10-04'}, TRACE),
        ({'records': [record('2026-09-21')], 'nextUntil': None}, TRACE),
    ]
    await market.index_investors('KOSPI', '1w')
    assert store.get(key)['next_until'] == '2026-09-27'
    await market.index_investors('KOSPI', '1w', resume=True)
    result = store.get(key)
    assert [r['date'] for r in result['rows']] == ['2026-09-21', '2026-09-28', '2026-10-05']
    assert result['rows'][0]['individual']['net'] == 70
    assert result['backfill_complete'] and result['history_status'] == 'exhausted'
    assert result['next_until'] is None
    assert client.get.call_args_list[1].args[1] == {'interval': '1w', 'count': 100, 'until': '2026-09-27'}
    await market.index_investors('KOSPI', '1w', resume=True)
    assert client.get.await_count == 2  # Exhaustion never restarts at today's page.


@pytest.mark.asyncio
async def test_empty_head_retains_values_but_empty_resumed_page_confirms_end(tmp_path):
    market, store, client = fixture_market(tmp_path)
    key = seed(store)
    client.get.return_value = ({'records': [], 'nextUntil': None}, TRACE)
    await market.index_investors('KOSPI', '1w')
    assert store.get(key)['status'] == 'stale'
    assert not store.get(key)['backfill_complete']
    assert store.get(key)['next_until'] == '2026-09-27'
    await market.index_investors('KOSPI', '1w', resume=True)
    assert store.get(key)['status'] == 'ready'
    assert store.get(key)['backfill_complete']
    assert len(store.get(key)['rows']) == 1


@pytest.mark.asyncio
async def test_initial_empty_result_is_no_data_and_does_not_block_later_available_history(tmp_path):
    market, store, client = fixture_market(tmp_path)
    key = 'toss-index-investors:KOSPI:1mo'
    client.get.side_effect = [
        ({'records': [], 'nextUntil': None}, TRACE),
        ({'records': [record('2026-09-30')], 'nextUntil': '2026-09-29'}, TRACE),
    ]
    await market.index_investors('KOSPI', '1mo')
    assert store.get(key)['status'] == 'no_data'
    await market.index_investors('KOSPI', '1mo')
    assert store.get(key)['status'] == 'ready'
    assert not store.get(key)['backfill_complete']
    assert store.get(key)['next_until'] == '2026-09-29'


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', [
    {'records': None, 'nextUntil': None},
    {'records': [], 'nextUntil': '2026-09-20'},
    {'records': [record('2026-09-21')], 'nextUntil': '2026-09-27'},
    {'records': [record('2026-10-05')], 'nextUntil': None},
    {'records': [record('2026-09-21'), record('2026-09-21')], 'nextUntil': None},
])
async def test_bad_history_page_preserves_last_good_rows_and_cursor(tmp_path, bad):
    market, store, client = fixture_market(tmp_path)
    key = seed(store)
    previous = store.get(key)
    client.get.return_value = (bad, TRACE)
    await market.run_job(key + ':history', 5, 'index_investors', code='KOSPI', interval='1w', resume=True)
    assert store.get(key) == previous
    assert store.get('toss-market-job:' + key + ':history')['status'] == 'error'


@pytest.mark.asyncio
async def test_optional_missing_cursor_is_not_evidence_of_last_page(tmp_path):
    market, store, client = fixture_market(tmp_path)
    key = seed(store)
    client.get.return_value = ({'records': [record('2026-09-21')]}, TRACE)
    await market.index_investors('KOSPI', '1w', resume=True)
    result = store.get(key)
    assert len(result['rows']) == 2
    assert not result['backfill_complete']
    assert result['next_until'] == '2026-09-27'
    assert result['history_status'] == 'cursor_unconfirmed'


@pytest.mark.asyncio
async def test_head_and_backfill_serialize_their_merge_without_losing_either_page(tmp_path):
    market, store, client = fixture_market(tmp_path)
    key = seed(store)
    history_started, release = asyncio.Event(), asyncio.Event()

    async def get(path, params):
        if params.get('until'):
            history_started.set()
            await release.wait()
            return {'records': [record('2026-09-21')], 'nextUntil': None}, TRACE
        return {'records': [record('2026-10-05')], 'nextUntil': '2026-10-04'}, TRACE

    client.get.side_effect = get
    history = asyncio.create_task(market.index_investors('KOSPI', '1w', resume=True))
    await asyncio.wait_for(history_started.wait(), 1)
    head = asyncio.create_task(market.index_investors('KOSPI', '1w'))
    release.set()
    await asyncio.wait_for(asyncio.gather(history, head), 1)
    result = store.get(key)
    assert [r['date'] for r in result['rows']] == ['2026-09-21', '2026-09-28', '2026-10-05']
    assert result['backfill_complete'] and result['next_until'] is None
