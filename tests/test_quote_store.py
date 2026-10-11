"""fixture_non_production: cross-connection quote atomicity and compatibility."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from services.market_dashboard.store import Store


def quote(price=100, stamp='2026-10-08T15:30:00+09:00', source='toss'):
    return {'price': price, 'as_of': stamp, 'quote_trace': {'source': source},
            'venue': '통합', 'change': 1, 'received_at': stamp}


def test_legacy_preserved_incremental_reads_and_independent_revision(tmp_path):
    store = Store('sqlite://', tmp_path)
    legacy = {'005930': quote(), '000660': quote(200)}
    store.put('quotes', legacy)
    assert store.get_quote('005930') == legacy['005930']
    old = store.revisions(('quotes', 'quote:000660'))
    store.update_quote('005930', quote(110, '2026-10-08T15:31:00+09:00'))
    heads = store.revisions(('quotes', 'quote:000660'))
    assert heads['quotes'] != old['quotes']
    assert heads['quote:000660'] == old['quote:000660']
    assert store.get('quotes')['005930']['price'] == 110
    assert store.get('quotes')['000660']['price'] == 200
    with store.read_connection() as db:
        raw = db.execute(select(store.snapshots.c.payload).where(
            store.snapshots.c.key == 'quotes')).scalar()
        assert json.loads(raw) == legacy


def test_independent_connections_first_insert_and_partial_updates(tmp_path):
    url = 'sqlite:///' + str(tmp_path / 'market.db')
    stores = [Store(url, tmp_path) for _ in range(8)]
    barrier = threading.Barrier(len(stores))
    def update(index):
        barrier.wait()
        stores[index].update_quote('005930', {'daily_facts': {
            f'metric{index}': {'value': index, 'period': '2026-10-08'}}})
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(update, range(8)))
    assert len(stores[0].get_quote('005930')['daily_facts']) == 8
    barrier = threading.Barrier(2)
    def concurrent(index):
        barrier.wait()
        stores[index].update_quote('005930', quote(110) if index == 0 else {
            'daily_facts': {'volume': {'value': 500, 'period': '2026-10-08'}}})
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(concurrent, range(2)))
    final = stores[2].get_quote('005930')
    assert final['price'] == 110
    assert len(final['daily_facts']) == 9
    assert stores[3].get('quotes')['005930'] == final
    for store in stores:
        store.engine.dispose()


def test_stale_price_invalid_zero_and_fallback_cannot_overwrite(tmp_path):
    store = Store('sqlite://', tmp_path)
    store.update_quote('005930', quote())
    before = store.revisions(('quotes', 'quote:005930'))
    for incoming in [quote(0), quote(float('nan')), quote(500, '2026-10-07T15:30:00+09:00'),
                     quote(500, '2026-10-09T15:30:00+09:00', 'naver')]:
        store.update_quote('005930', incoming)
    assert store.get_quote('005930')['price'] == 100
    assert store.revisions(('quotes', 'quote:005930')) == before
    store.update_quote('005930', {**quote(), 'received_at': 'later collection'})
    assert store.revisions(('quotes', 'quote:005930')) == before


def test_daily_fact_period_and_other_fact_preservation(tmp_path):
    store = Store('sqlite://', tmp_path)
    store.update_quote('005930', {**quote(), 'daily_facts': {
        'volume': {'value': 10, 'period': '2026-10-08'},
        'value': {'value': 200, 'period': '2026-10-08'}}})
    store.update_quote('005930', {'daily_facts': {
        'volume': {'value': 5, 'period': '2026-10-07'}}})
    store.update_quote('005930', quote(110, '2026-10-08T15:31:00+09:00'))
    result = store.get_quote('005930')
    assert result['price'] == 110
    assert result['daily_facts']['volume']['value'] == 10
    assert result['daily_facts']['value']['value'] == 200


def test_failed_batch_rolls_back_rows_and_revision(tmp_path):
    store = Store('sqlite://', tmp_path)
    store.update_quote('005930', quote())
    before = store.revisions(('quotes', 'quote:005930'))
    with pytest.raises(ValueError):
        store.update_quotes({'000660': quote(), '005930': {'invalid_json': float('nan')}})
    assert store.get_quote('000660') == {}
    assert store.revisions(('quotes', 'quote:005930')) == before


def test_composite_revision_survives_future_legacy_clock(tmp_path):
    store = Store('sqlite://', tmp_path)
    store.put('quotes', {'005930': quote()})
    with store.engine.begin() as db:
        db.execute(store.snapshots.update().where(store.snapshots.c.key == 'quotes')
                   .values(updated_at='2099-01-01T00:00:00+00:00'))
    store.update_quote('005930', quote(110, '2026-10-08T15:31:00+09:00'))
    revision = store.revisions(('quotes',))
    assert store.get('quotes')['005930']['price'] == 110
    store.update_quote('005930', quote(120, '2026-10-08T15:32:00+09:00'))
    assert store.revisions(('quotes',)) != revision
    assert store.get('quotes')['005930']['price'] == 120


def test_rollback_whole_book_writer_uses_atomic_merge(tmp_path):
    store = Store('sqlite://', tmp_path)
    store.put('quotes', {'005930': quote()})
    store.update_quote('005930', quote(110, '2026-10-08T15:31:00+09:00'))
    stale_book = store.get('quotes').copy()
    store.update_quote('000660', quote(200))
    stale_book['005930'] = quote(120, '2026-10-08T15:32:00+09:00')
    assert store.put('quotes', stale_book)
    assert store.get_quote('005930')['price'] == 120
    assert store.get_quote('000660')['price'] == 200
    assert store.metadata('quotes')['005930']['price'] == 100


def test_same_quote_first_collector_touch_seeds_legacy_only_once(tmp_path):
    store = Store('sqlite://', tmp_path)
    original = quote()
    store.put('quotes', {'005930': original})
    # Read-only lookups must not create the new read model.
    assert store.get_quote('005930') == original
    assert store.metadata('quote:005930') is None
    store.update_quote('005930', {})
    assert store.metadata('quote:005930') == original
    assert store.metadata('quotes')['005930'] == original
    heads = store.revisions(('quotes', 'quote:005930'))
    store.update_quote('005930', {})
    assert store.revisions(('quotes', 'quote:005930')) == heads


@pytest.mark.asyncio
async def test_toss_identical_rest_batch_seeds_without_new_source_clock(tmp_path):
    from services.market_dashboard.toss import TossCollector
    store = Store('sqlite://', tmp_path)
    original = {**quote(), 'change': None, 'market_status': None}
    store.put('quotes', {'005930': original})
    collector = TossCollector(store)
    batch = {}
    collector.save_quote({'symbol': '005930', 'lastPrice': '100', 'currency': 'KRW',
                          'timestamp': original['as_of']}, {'source': 'toss', 'new': True},
                         batch=batch)
    store.update_quotes(batch)
    assert store.metadata('quote:005930') == original
    await collector.client.close()
