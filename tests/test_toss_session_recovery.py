"""fixture_non_production: a resumed worker must recover the visible trading day."""
import pytest
from services.market_dashboard.store import Store
from services.market_dashboard.toss import TossCollector
from services.market_dashboard.quote_metrics import select_daily_measures


def candle(stamp, volume=10):
    return {'timestamp': stamp, 'openPrice': '100', 'highPrice': '110',
            'lowPrice': '90', 'closePrice': '105', 'volume': str(volume), 'currency': 'KRW'}


@pytest.mark.asyncio
async def test_same_last_trade_does_not_invalidate_market_cache(tmp_path):
    store = Store('sqlite://', tmp_path)
    collector = TossCollector(store)
    quote = {'symbol':'005930', 'currency':'KRW', 'lastPrice':'105',
             'timestamp':'2026-10-08T20:00:00+09:00'}
    collector.save_quote(quote, {'source':'toss', 'sha256':'first'})
    revision = store.revisions(('quotes',))
    collector.save_quote(quote, {'source':'toss', 'sha256':'second'})
    assert store.revisions(('quotes',)) == revision
    assert store.get('quotes')['005930']['quote_trace']['sha256'] == 'first'
    collector.save_quote({**quote, 'timestamp':'2026-10-09T08:00:00+09:00'}, {'source':'toss'})
    assert store.revisions(('quotes',)) != revision
    await collector.client.close()


@pytest.mark.asyncio
async def test_new_session_walks_to_prior_day_then_refreshes_only_head(tmp_path, monkeypatch):
    store = Store('sqlite://', tmp_path)
    collector = TossCollector(store)
    responses = [
        ([candle('2026-10-08T16:41:00+09:00'), candle('2026-10-08T20:00:00+09:00')], 'p2'),
        ([candle('2026-10-08T12:00:00+09:00')], 'p3'),
        ([candle('2026-10-07T20:00:00+09:00'), candle('2026-10-08T08:00:00+09:00')], 'p4'),
    ]
    calls = []
    async def get(path, params):
        calls.append(params)
        rows, cursor = responses.pop(0)
        return {'symbol':'005930', 'candles':rows, 'nextBefore':cursor}, {'source':'fixture'}
    monkeypatch.setattr(collector.client, 'get', get)
    await collector.history('005930', '1m', cover_session=True)
    assert [c.get('before') for c in calls] == [None, 'p2', 'p3']
    chart = store.get('toss-chart:005930')
    assert chart['points'][0]['time'] == '20261008080000'
    assert all(p['time'].startswith('20261008') for p in chart['points'])
    assert store.metadata('bars:005930:통합:1min')['covered_session'] == '2026-10-08'
    responses.append(([candle('2026-10-08T19:59:00+09:00')], 'unused'))
    calls.clear()
    await collector.history('005930', '1m', cover_session=True)
    assert len(calls) == 1
    assert len(store.get('toss-chart:005930')['points']) == 5
    await collector.client.close()


@pytest.mark.asyncio
async def test_session_recovery_has_five_page_budget_and_keeps_cursor(tmp_path, monkeypatch):
    store = Store('sqlite://', tmp_path)
    collector = TossCollector(store)
    calls = []
    async def get(path, params):
        calls.append(params)
        return {'candles':[candle(f'2026-10-08T12:0{5-len(calls)}:00+09:00')],
                'nextBefore':f'page{len(calls)}'}, {}
    monkeypatch.setattr(collector.client, 'get', get)
    await collector.history('005930', '1m', cover_session=True)
    assert len(calls) == 5
    meta = store.metadata('bars:005930:통합:1min')
    assert meta['covered_session'] is None
    assert meta['next_before'] == 'page5'
    assert not meta['backfill_complete']
    await collector.client.close()


@pytest.mark.asyncio
async def test_daily_candle_volume_preserves_its_date_not_collection_clock(tmp_path, monkeypatch):
    store = Store('sqlite://', tmp_path)
    store.put('quotes', {'005930': {'price':105, 'as_of':'2026-10-08T20:00:00+09:00'}})
    collector = TossCollector(store)
    async def get(path, params):
        return {'candles':[candle('2026-10-08T00:00:00+09:00', volume=12345)]}, {'source':'fixture'}
    monkeypatch.setattr(collector.client, 'get', get)
    await collector.history('005930')
    quote = store.get('quotes')['005930']
    assert quote['price'] == 105
    trace = quote['daily_facts']['volume']['source_trace']
    assert trace['unit'] == '주'
    assert trace['period'] == '2026-10-08'
    assert trace['field'] == 'volume'
    assert trace['venue'] == '통합'
    result = select_daily_measures(dict(quote), [quote['daily_facts']])
    assert result['volume'] == 12345
    assert result['value'] is None  # Turnover is not price times total volume.
    later = select_daily_measures({**quote, 'as_of':'2026-10-09T09:00:00+09:00'}, [quote['daily_facts']])
    assert later['volume'] is None
    await collector.client.close()
