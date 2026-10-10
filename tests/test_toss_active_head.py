"""fixture_non_production: active refresh clocks must exclude old-page receipts."""
import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from services.market_dashboard.store import Store
from services.market_dashboard.toss import TossCollector, head_age_seconds


@pytest.mark.asyncio
@pytest.mark.parametrize('old_head', [True, False])
async def test_active_old_head_refreshes_despite_a_recent_history_page(
        tmp_path, monkeypatch, old_head):
    clock = datetime.now(UTC).replace(microsecond=0)
    stamp = clock.isoformat()
    old = (clock - timedelta(days=2) if old_head else clock).isoformat()
    store = Store('sqlite://', tmp_path)
    code = '035900'
    source = {'status': 'ready', 'received_at': stamp, 'head_received_at': old,
              'history_received_at': stamp, 'backfill_complete': False}
    daily_key = f'bars:{code}:통합:day'
    investor_key = f'toss:investor-trading:{code}'
    store.put(daily_key, {**source, 'next_before': 'old-price-page'})
    store.put(investor_key, {**source, 'next_until': 'old-investor-page'})
    store.put(f'bars:{code}:통합:raw', {'received_at': stamp, 'backfill_complete': True})
    before = {key: store.metadata(key) for key in (daily_key, investor_key)}
    calls = []
    collector = TossCollector(store, object())
    monkeypatch.setattr(store, 'active', lambda: [code])
    monkeypatch.setattr('services.market_dashboard.toss.now', lambda: stamp)
    original_get = store.get

    def small_reads(key, *args, **kwargs):
        assert key != investor_key, 'Active refresh hydrated the full investor history'
        return original_get(key, *args, **kwargs)

    monkeypatch.setattr(store, 'get', small_reads)

    async def history(symbol, interval='1d', **kwargs):
        calls.append(('history', symbol, interval, kwargs))

    async def trading(symbol, **kwargs):
        calls.append(('trading', symbol, kwargs))

    async def snapshot(*_args, **_kwargs):
        pass

    class Finished(BaseException):
        pass

    async def sleep(seconds):
        assert seconds == 15
        raise Finished

    async def only_refresh(*tasks):
        for index, task in enumerate(tasks):
            if index != 1:
                task.close()
        await tasks[1]

    monkeypatch.setattr(collector, 'history', history)
    monkeypatch.setattr(collector, 'fundamentals_of_trading', trading)
    monkeypatch.setattr(collector, 'snapshot', snapshot)
    monkeypatch.setattr(asyncio, 'gather', only_refresh)
    monkeypatch.setattr(asyncio, 'sleep', sleep)
    try:
        with pytest.raises(Finished):
            await collector.run()
        assert (('history', code, '1d', {'pages': 1, 'compact': True}) in calls) is old_head
        assert (('trading', code, {}) in calls) is old_head
        assert {key: store.metadata(key) for key in before} == before
    finally:
        store.engine.dispose()


@pytest.mark.parametrize(('snapshot', 'age'), [
    ({'received_at': '2026-10-10T10:59:00+00:00'}, 60),
    ({'received_at': '2026-10-10T11:00:00+00:00',
      'head_received_at': '2026-10-10T10:00:00+00:00',
      'history_received_at': '2026-10-10T11:00:00+00:00'}, 3600),
    ({'received_at': '2026-10-10T11:00:00+00:00',
      'head_received_at': None}, float('inf')),
    ({'received_at': '2026-10-10T11:00:00+00:00',
      'history_received_at': '2026-10-10T11:00:00+00:00'}, float('inf')),
    ({'head_received_at': 'invalid'}, float('inf')),
    ({'head_received_at': '2026-10-10T10:00:00'}, float('inf')),
    ({'head_received_at': '2026-10-11T11:00:00+00:00'}, float('inf')),
    ({'status': 'stale', 'head_received_at': '2026-10-10T10:59:00+00:00'}, 60),
    ({'head_received_at': '2026-10-10T19:59:00+09:00'}, 60),
    ({}, float('inf')),
])
def test_head_age_legacy_fallback_and_unknown_head_are_distinct(snapshot, age):
    before = dict(snapshot)
    assert head_age_seconds(snapshot, '2026-10-10T11:00:00+00:00') == age
    assert snapshot == before  # A receipt never promotes data status or source dates.
