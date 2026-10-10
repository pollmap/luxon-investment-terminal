"""fixture_non_production: independent latest-page refresh; no provider traffic."""
import asyncio
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from services.market_dashboard.store import Store
from services.market_dashboard.toss import TossCollector

KINDS = ('investor-trading', 'program-trades', 'short-selling',
         'credit-trades', 'securities-lending')
CODE = '035900'


@pytest.fixture
def setup(tmp_path, monkeypatch):
    store = Store('sqlite://', tmp_path)
    collector = TossCollector(store, object())
    stamp = datetime.now(UTC).isoformat()
    old = (datetime.now(UTC)-timedelta(days=2)).isoformat()
    monkeypatch.setattr('services.market_dashboard.toss.now', lambda: stamp)
    monkeypatch.setattr(store, 'active', lambda: [CODE])
    store.put(f'bars:{CODE}:통합:day', {'head_received_at': stamp})
    store.put(f'bars:{CODE}:통합:raw', {'received_at': stamp, 'backfill_complete': True})
    for endpoint in KINDS:
        store.put(f'toss:{endpoint}:{CODE}', {'head_received_at': old,
            'history_received_at': stamp, 'received_at': stamp, 'next_until': 'old-page',
            'backfill_complete': False, 'status': 'ready'})
    calls = []
    async def history(*args, **kwargs):
        calls.append(('history', args, kwargs))
    async def snapshot(*args, **kwargs):
        calls.append(('snapshot', args, kwargs))
    async def trading(*args, **kwargs):
        calls.append(('trading', args, kwargs))
    monkeypatch.setattr(collector, 'history', history)
    monkeypatch.setattr(collector, 'snapshot', snapshot)
    monkeypatch.setattr(collector, 'fundamentals_of_trading', trading)
    yield store, collector, calls, stamp
    store.engine.dispose()


async def once(collector, monkeypatch):
    class Done(BaseException):
        pass
    async def sleep(seconds):
        assert seconds == 15
        raise Done
    async def only_refresh(*tasks):
        for index, task in enumerate(tasks):
            if index != 1:
                task.close()
        await tasks[1]
    monkeypatch.setattr(asyncio, 'gather', only_refresh)
    monkeypatch.setattr(asyncio, 'sleep', sleep)
    with pytest.raises(Done):
        await collector.run()


@pytest.mark.asyncio
async def test_fresh_investor_does_not_hide_four_stale_endpoint_heads(setup, monkeypatch):
    store, collector, calls, stamp = setup
    key = f'toss:investor-trading:{CODE}'
    store.put(key, {**store.metadata(key), 'head_received_at': stamp})
    before = {ep: store.metadata(f'toss:{ep}:{CODE}') for ep in KINDS}
    await once(collector, monkeypatch)
    assert [kwargs['endpoints'] for name, _, kwargs in calls if name == 'trading'] == [
        (ep,) for ep in KINDS if ep != 'investor-trading']
    assert {ep: store.metadata(f'toss:{ep}:{CODE}') for ep in KINDS} == before


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['history', 'snapshot', 'endpoint'])
async def test_one_stage_error_cannot_block_other_trading_sections(setup, monkeypatch, stage):
    store, collector, calls, _ = setup
    async def failed(*args, **kwargs):
        raise ValueError('fixture raw error never record')
    if stage != 'endpoint':
        monkeypatch.setattr(collector, stage, failed)
    else:
        async def trading(*args, **kwargs):
            calls.append(('trading', args, kwargs))
            if kwargs.get('endpoints') == ('program-trades',):
                raise ValueError('fixture raw error never record')
        monkeypatch.setattr(collector, 'fundamentals_of_trading', trading)
    await once(collector, monkeypatch)
    assert [kwargs['endpoints'] for name, _, kwargs in calls if name == 'trading'] == [
        (ep,) for ep in KINDS]
    assert store.metadata('provider-toss')['status'] == 'error'
    assert 'fixture raw error' not in str(store.metadata('provider-toss'))


def http_failure(status):
    request = httpx.Request('GET', 'https://fixture.invalid/secret-path')
    return httpx.HTTPStatusError('sensitive fixture message', request=request,
                                 response=httpx.Response(status, request=request))


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [404, 500])
async def test_single_snapshot_http_error_keeps_each_due_trading_endpoint(
        setup, monkeypatch, status):
    store, collector, calls, _ = setup

    async def snapshot(*args, **kwargs):
        calls.append(('snapshot', args, kwargs))
        raise http_failure(status)

    monkeypatch.setattr(collector, 'snapshot', snapshot)
    await collector.refresh_active_once()
    assert [kwargs['endpoints'] for name, _, kwargs in calls if name == 'trading'] == [
        (ep,) for ep in KINDS]
    assert store.metadata('provider-toss')['failed_parts'] == ['snapshot']
    assert 'sensitive' not in str(store.metadata('provider-toss'))
    assert 'secret-path' not in str(store.metadata('provider-toss'))


@pytest.mark.asyncio
async def test_endpoint_404_does_not_hide_other_endpoints_or_next_security(setup, monkeypatch):
    store, collector, calls, _ = setup
    codes = [CODE, '005930']
    monkeypatch.setattr(store, 'active', lambda: codes)

    async def trading(code, **kwargs):
        calls.append(('trading', (code,), kwargs))
        if kwargs['endpoints'] == ('credit-trades',):
            raise http_failure(404)

    monkeypatch.setattr(collector, 'fundamentals_of_trading', trading)
    await collector.refresh_active_once()
    actual = [(args[0], kwargs['endpoints']) for name, args, kwargs in calls if name == 'trading']
    assert actual == [
        (code, (ep,)) for code in codes for ep in KINDS]
    assert store.metadata('provider-toss')['failed_parts'] == ['credit-trades']


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [401, 403, 429])
async def test_auth_and_rate_limit_failures_stop_this_pass_without_other_calls(
        setup, monkeypatch, status):
    store, collector, calls, _ = setup
    monkeypatch.setattr(store, 'active', lambda: [CODE, '005930'])

    async def history(*args, **kwargs):
        calls.append(('history', args, kwargs))
        raise http_failure(status)

    monkeypatch.setattr(collector, 'history', history)
    await collector.refresh_active_once()
    assert calls == [('history', (CODE, '1m'), {'pages': 1, 'cover_session': True})]
    assert store.metadata('provider-toss')['failed_parts'] == ['history']


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [500, 503, 'timeout', 'connect'])
async def test_recent_history_and_endpoints_cannot_reset_consecutive_provider_failures(
        setup, monkeypatch, failure):
    store, collector, calls, stamp = setup
    codes = [CODE, '005930', '000660']
    monkeypatch.setattr(store, 'active', lambda: codes)
    for code in codes:
        collector.last_history[code] = time.monotonic()
        for ep in KINDS:
            store.put(f'toss:{ep}:{code}', {'head_received_at': stamp})

    async def snapshot(code, **kwargs):
        calls.append(('snapshot', (code,), kwargs))
        if isinstance(failure, int):
            raise http_failure(failure)
        error = httpx.ReadTimeout if failure == 'timeout' else httpx.ConnectError
        raise error('sensitive fixture message')

    monkeypatch.setattr(collector, 'snapshot', snapshot)
    await collector.refresh_active_once()
    assert calls == [('snapshot', (code,), {'include_price': False}) for code in codes[:2]]
    assert store.metadata('provider-toss')['status'] == 'error'
    assert 'sensitive' not in str(store.metadata('provider-toss'))


@pytest.mark.asyncio
async def test_successful_endpoint_resets_provider_failure_count(setup, monkeypatch):
    store, collector, calls, _ = setup
    failures = {'investor-trading', 'short-selling', 'securities-lending'}

    async def trading(code, **kwargs):
        endpoint, = kwargs['endpoints']
        calls.append(('trading', (code,), kwargs))
        if endpoint in failures:
            raise http_failure(503)

    monkeypatch.setattr(collector, 'fundamentals_of_trading', trading)
    await collector.refresh_active_once()
    assert [kwargs['endpoints'] for name, _, kwargs in calls if name == 'trading'] == [
        (ep,) for ep in KINDS]
    assert store.metadata('provider-toss')['failed_parts'] == [ep for ep in KINDS if ep in failures]


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['history', 'snapshot', 'investor-trading'])
async def test_cancellation_never_refreshes_the_remaining_sections_or_overwrites_state(
        setup, monkeypatch, stage):
    store, collector, calls, _ = setup
    original = {'status': 'error', 'received_at': '2026-10-01T00:00:00+00:00'}
    store.put('provider-toss', original)

    async def cancelled(*args, **kwargs):
        calls.append(('cancelled', args, kwargs))
        raise asyncio.CancelledError

    attribute = 'fundamentals_of_trading' if stage == 'investor-trading' else stage
    monkeypatch.setattr(collector, attribute, cancelled)
    with pytest.raises(asyncio.CancelledError):
        await collector.refresh_active_once()
    assert calls[-1][0] == 'cancelled'
    assert len(calls) == ['history', 'snapshot', 'investor-trading'].index(stage) + 1
    assert store.metadata('provider-toss') == original


@pytest.mark.asyncio
async def test_capacity_pause_and_old_page_cursors_survive_latest_endpoint_refresh(setup):
    store, collector, calls, _ = setup
    pause = {'status': 'capacity_limited', 'cursor': 173, 'attempts': 54749,
             'updated_at': '2026-10-06T00:00:00+00:00'}
    store.put('toss-backfill-progress', pause)
    keys = [f'toss:{ep}:{CODE}' for ep in KINDS]
    before = {key: store.metadata(key) for key in keys}
    await collector.refresh_active_once()
    assert store.metadata('toss-backfill-progress') == pause
    assert {key: store.metadata(key) for key in keys} == before
    assert [kwargs for name, _, kwargs in calls if name == 'trading'] == [
        {'endpoints': (ep,)} for ep in KINDS]


@pytest.mark.asyncio
async def test_each_recent_empty_head_is_a_retry_clock_not_data_readiness(setup):
    store, collector, calls, stamp = setup
    for ep in KINDS:
        key = f'toss:{ep}:{CODE}'
        store.put(key, {**store.metadata(key), 'head_received_at': stamp, 'status': 'stale',
                        'empty_head_received_at': stamp})
    before = {ep: store.metadata(f'toss:{ep}:{CODE}') for ep in KINDS}
    await collector.refresh_active_once()
    assert not [call for call in calls if call[0] == 'trading']
    assert {ep: store.metadata(f'toss:{ep}:{CODE}') for ep in KINDS} == before
