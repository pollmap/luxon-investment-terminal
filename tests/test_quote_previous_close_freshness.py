"""fixture_non_production: source dates, not receipt dates, qualify prior closes."""
import asyncio

import pytest

from services.market_dashboard.store import Store
from services.market_dashboard.time_series import put_series
from services.market_dashboard.toss import TossCollector


@pytest.mark.parametrize("normalized", [False, True])
def test_old_price_head_cannot_be_used_as_previous_close(tmp_path, normalized):
    store = Store('sqlite://', tmp_path)
    collector = TossCollector(store)
    key = 'bars:066570:통합:day'
    data = {'rows': [{'date': '2026-10-02', 'close': 215500}],
            'head_received_at': '2026-10-12T09:00:00+09:00',
            'received_at': '2026-10-12T09:00:00+09:00', 'venue': '통합'}
    (put_series if normalized else lambda s, k, v: s.put(k, v))(store, key, data)
    collector.save_quote({'symbol': '066570', 'currency': 'KRW', 'lastPrice': '200500',
                         'timestamp': '2026-10-08T19:59:58+09:00'},
                        {'source': 'fixture_non_production'})
    quote = store.get_quote('066570')
    assert quote['price'] == 200500
    assert quote['as_of'] == '2026-10-08T19:59:58+09:00'
    assert quote['change'] is None
    asyncio.run(collector.client.close())


def test_new_prior_observation_recomputes_change_without_full_history_read(tmp_path, monkeypatch):
    store = Store('sqlite://', tmp_path)
    collector = TossCollector(store)
    key = 'bars:066570:통합:day'
    put_series(store, key, {'rows': [{'date': '2026-10-02', 'close': 215500}]})
    item = {'symbol': '066570', 'currency': 'KRW', 'lastPrice': '200500',
            'timestamp': '2026-10-08T19:59:58+09:00'}
    collector.save_quote(item, {'source': 'fixture_non_production'})
    assert store.get_quote('066570')['change'] is None
    put_series(store, key, {'rows': [{'date': '2026-10-07', 'close': 209500}]})
    original = store.get
    def narrow(key_to_read, *args, **kwargs):
        assert key_to_read != key, 'Do not materialize complete price history'
        return original(key_to_read, *args, **kwargs)
    monkeypatch.setattr(store, 'get', narrow)
    collector.save_quote(item, {'source': 'fixture_non_production'})
    assert store.get_quote('066570')['change'] == pytest.approx(-4.295942720763724)
    asyncio.run(collector.client.close())


@pytest.mark.parametrize('normalized', [False, True])
@pytest.mark.parametrize('head_date', ['2026-10-08', '2026-10-09'])
def test_current_or_future_head_cannot_certify_missing_previous_session(
    tmp_path, normalized, head_date,
):
    store = Store('sqlite://', tmp_path)
    collector = TossCollector(store)
    key = 'bars:066570:통합:day'
    data = {'rows': [{'date': '2026-10-02', 'close': 215500},
                     {'date': head_date, 'close': 200500}],
            'last_time': head_date, 'venue': '통합'}
    (put_series if normalized else lambda s, k, v: s.put(k, v))(store, key, data)
    try:
        collector.save_quote({'symbol': '066570', 'currency': 'KRW',
                              'lastPrice': '200500',
                              'timestamp': '2026-10-08T19:59:58+09:00'},
                             {'source': 'fixture_non_production'})
        quote = store.get_quote('066570')
        assert quote['price'] == 200500
        assert quote['as_of'] == '2026-10-08T19:59:58+09:00'
        assert quote['change'] is None
    finally:
        asyncio.run(collector.client.close())
        store.engine.dispose()


@pytest.mark.parametrize('normalized', [False, True])
def test_friday_close_can_qualify_for_monday_without_a_current_head(tmp_path, normalized):
    store = Store('sqlite://', tmp_path)
    collector = TossCollector(store)
    key = 'bars:066570:통합:day'
    data = {'rows': [{'date': '2026-10-02', 'close': 200000}], 'venue': '통합'}
    (put_series if normalized else lambda s, k, v: s.put(k, v))(store, key, data)
    try:
        collector.save_quote({'symbol': '066570', 'currency': 'KRW',
                              'lastPrice': '210000',
                              'timestamp': '2026-10-05T09:01:00+09:00'},
                             {'source': 'fixture_non_production'})
        assert store.get_quote('066570')['change'] == pytest.approx(5.0)
    finally:
        asyncio.run(collector.client.close())
        store.engine.dispose()
