"""fixture_non_production: source dates, not receipt dates, qualify prior closes."""
import asyncio
import gzip
import json

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


class CalendarFixtureClient:
    async def close(self):
        pass


def calendar_fixture(store, key, today='2026-10-12', before='2026-10-08',
                     after='2026-10-13', closed=False, url=None):
    def day(stamp, is_closed=False):
        return {'date': stamp, 'integrated': None if is_closed else {
            name: {'startTime': stamp + 'T' + start + '+09:00',
                   'endTime': stamp + 'T' + end + '+09:00'}
            for name, start, end in [('preMarket', '08:00:00', '09:00:00'),
                                     ('regularMarket', '09:00:00', '15:30:00'),
                                     ('afterMarket', '15:30:00', '20:00:00')]}}
    calendar = {'today': day(today, closed), 'previousBusinessDay': day(before),
                'nextBusinessDay': day(after)}
    source_url = url or 'https://openapi.tossinvest.com/api/v1/market-calendar/KR'
    content = json.dumps({'fixture_non_production': True, 'result': calendar}).encode()
    digest = store.raw('toss', source_url, content)
    snapshot = {'status': 'ready', 'source_trace': {'source': 'toss',
        'source_url': source_url, 'source_document_id': digest, 'filing_id': digest}}
    if key.startswith('market-session:'):
        snapshot['session'] = next(value for value in calendar.values()
                                   if value['date'] == key.split(':', 1)[1])
    else:
        snapshot['calendar'] = calendar
    store.put(key, snapshot)
    return snapshot, calendar, digest


def holiday_quote_fixture(tmp_path, normalized=True):
    store = Store('sqlite://', tmp_path)
    collector = TossCollector(store, client=CalendarFixtureClient())
    payload = {'rows': [{'date': '2026-10-08', 'close': 200000},
                        {'date': '2026-10-12', 'close': 220000}], 'venue': '통합'}
    key = 'bars:066570:통합:day'
    (put_series if normalized else lambda s, k, v: s.put(k, v))(store, key, payload)
    item = {'symbol': '066570', 'currency': 'KRW', 'lastPrice': '220000',
            'timestamp': '2026-10-12T19:59:58+09:00'}
    return store, collector, item


@pytest.mark.parametrize('normalized', [False, True])
@pytest.mark.parametrize('source_key', ['toss-calendar:KR:2026-10-12',
                                      'market-session:2026-10-08', 'market-session:2026-10-09'])
def test_archived_official_business_relation_qualifies_holiday_gap(
    tmp_path, normalized, source_key,
):
    store, collector, item = holiday_quote_fixture(tmp_path, normalized)
    if source_key.endswith('2026-10-09'):
        calendar_fixture(store, source_key, today='2026-10-09', after='2026-10-12', closed=True)
    else:
        calendar_fixture(store, source_key)
    # A newer calendar can reference this date without proving its previous day.
    calendar_fixture(store, 'market-session:2026-10-12', today='2026-10-13',
                     before='2026-10-12', after='2026-10-14')
    try:
        collector.save_quote(item, {'source': 'fixture_non_production'})
        quote = store.get_quote('066570')
        assert quote['price'] == 220000 and quote['change'] == pytest.approx(10)
    finally:
        asyncio.run(collector.client.close())
        store.engine.dispose()


@pytest.mark.parametrize('invalid', ['snapshot_mismatch', 'missing_raw', 'corrupt_raw',
                                    'hash_alias', 'wrong_source', 'us_url', 'wrong_previous',
                                    'missing_integrated', 'offset_missing', 'closed_trade_day'])
def test_calendar_requires_matching_original_market_period_and_sessions(tmp_path, invalid):
    store, collector, item = holiday_quote_fixture(tmp_path)
    key = 'market-session:2026-10-08'
    if invalid == 'us_url':
        snapshot, _, _ = calendar_fixture(store, key,
            url='https://openapi.tossinvest.com/api/v1/market-calendar/US')
    elif invalid == 'wrong_previous':
        # The saved quote day names another previous business day, not this bar.
        key = 'toss-calendar:KR:2026-10-12'
        snapshot, _, _ = calendar_fixture(store, key, before='2026-10-07')
    elif invalid in ('missing_integrated', 'offset_missing'):
        snapshot, calendar, _ = calendar_fixture(store, key)
        if invalid == 'missing_integrated':
            del calendar['previousBusinessDay']['integrated']
        else:
            calendar['previousBusinessDay']['integrated']['regularMarket']['startTime'] = (
                '2026-10-08T09:00:00')
        content = json.dumps({'fixture_non_production': True, 'result': calendar}).encode()
        digest = store.raw('toss', snapshot['source_trace']['source_url'], content)
        snapshot['source_trace'].update(source_document_id=digest, filing_id=digest)
        snapshot['session'] = calendar['previousBusinessDay']
        store.put(key, snapshot)
    elif invalid == 'closed_trade_day':
        snapshot, _, _ = calendar_fixture(store, key, closed=True)
    else:
        snapshot, _, digest = calendar_fixture(store, key)
        if invalid == 'snapshot_mismatch':
            snapshot['session']['integrated'] = None
        elif invalid == 'missing_raw':
            snapshot['source_trace'].update(source_document_id='f' * 64, filing_id='f' * 64)
        elif invalid == 'corrupt_raw':
            (store.root / 'raw/toss' / (digest + '.bin.gz')).write_bytes(gzip.compress(b'{}'))
        elif invalid == 'hash_alias':
            snapshot['source_trace']['sha256'] = 'f' * 64
        elif invalid == 'wrong_source':
            snapshot['source_trace']['source'] = 'fixture_unknown_provider'
        store.put(key, snapshot)
    try:
        collector.save_quote(item, {'source': 'fixture_non_production'})
        quote = store.get_quote('066570')
        assert quote['price'] == 220000 and quote['change'] is None
    finally:
        asyncio.run(collector.client.close())
        store.engine.dispose()


def test_new_calendar_evidence_rechecks_same_bar_and_rejects_conflicting_session(
    tmp_path, monkeypatch,
):
    store, collector, item = holiday_quote_fixture(tmp_path)
    reads = []
    read = store.time_series.rows
    def bounded(series, **kwargs):
        assert kwargs.get('limit') == 1
        reads.append(series)
        return read(series, **kwargs)
    monkeypatch.setattr(store.time_series, 'rows', bounded)
    try:
        collector.save_quote(item, {'source': 'fixture_non_production'})
        assert store.get_quote('066570')['change'] is None
        calendar_fixture(store, 'market-session:2026-10-08')
        collector.save_quote(item, {'source': 'fixture_non_production'})
        assert store.get_quote('066570')['change'] == pytest.approx(10)
        collector.save_quote(item, {'source': 'fixture_non_production'})
        assert len(reads) == 2
        # An official open session inside the gap invalidates an older relation.
        calendar_fixture(store, 'market-session:2026-10-09', today='2026-10-09',
                         before='2026-10-08', after='2026-10-12')
        collector.save_quote(item, {'source': 'fixture_non_production'})
        assert store.get_quote('066570')['change'] is None
        assert len(reads) == 3
    finally:
        asyncio.run(collector.client.close())
        store.engine.dispose()


def test_retracted_calendar_proof_invalidates_cached_change_without_changing_price(tmp_path):
    store, collector, item = holiday_quote_fixture(tmp_path)
    key = 'market-session:2026-10-08'
    original, _, _ = calendar_fixture(store, key)
    try:
        collector.save_quote(item, {'source': 'fixture_non_production'})
        assert store.get_quote('066570')['change'] == pytest.approx(10)
        store.put(key, {'status': 'unverified', 'session': original['session']})
        collector.save_quote(item, {'source': 'fixture_non_production'})
        quote = store.get_quote('066570')
        assert quote['price'] == 220000 and quote['as_of'] == item['timestamp']
        assert quote['change'] is None
    finally:
        asyncio.run(collector.client.close())
        store.engine.dispose()


def test_status_only_calendar_retraction_keeps_raw_trace_but_invalidates_cached_change(tmp_path):
    store, collector, item = holiday_quote_fixture(tmp_path)
    key = 'market-session:2026-10-08'
    original, _, _ = calendar_fixture(store, key)
    try:
        collector.save_quote(item, {'source': 'fixture_non_production'})
        assert store.get_quote('066570')['change'] == pytest.approx(10)
        for status in ('unverified', 'retracted', 'invalid', 'error', 'partial', 'no_data'):
            store.put(key, {**original, 'status': status})
            collector.save_quote(item, {'source': 'fixture_non_production'})
            quote = store.get_quote('066570')
            assert quote['price'] == 220000 and quote['as_of'] == item['timestamp']
            assert quote['change'] is None, status
            assert store.metadata(key)['source_trace'] == original['source_trace']
        # Retained legacy snapshots without a status are still valid evidence.
        legacy = {field: value for field, value in original.items() if field != 'status'}
        store.put(key, legacy)
        collector.save_quote(item, {'source': 'fixture_non_production'})
        assert store.get_quote('066570')['change'] == pytest.approx(10)
    finally:
        asyncio.run(collector.client.close())
        store.engine.dispose()
