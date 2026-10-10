"""fixture_non_production: collection receipts, never real market coverage."""
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from copy import deepcopy
from threading import Event

import pytest

from services.market_dashboard.collection import (
    record_trading_collection,
    trading_collection_receipt,
    update_collection,
)
from services.market_dashboard.store import Store


def toss_receipt(store, code='035900'):
    receipt = trading_collection_receipt('investor-trading', {'rows': [
        {'date': '2026-10-08', 'individual': 3, 'foreign': -1, 'institution': -2},
    ]})
    record_trading_collection(store, code, 'investor-trading', receipt, source={
        'status': 'ready', 'head_received_at': '2026-10-10T04:52:54+00:00',
        'series_revision': 'fixture_non_production_revision',
        'next_until': '2026-05-07', 'backfill_complete': False,
    })


@pytest.fixture
def store(tmp_path):
    value = Store('sqlite://', tmp_path)
    yield value
    value.engine.dispose()


def test_late_legacy_flow_observation_cannot_relabel_a_validated_toss_receipt(store):
    toss_receipt(store)
    expected = deepcopy(store.get('collection:035900')['sources']['flows'])
    update_collection(store, '035900', {'flows': {
        'source': '네이버 증권 공개 투자자 집계', 'status': 'ready',
        'received_at': '2026-10-06T08:04:05+00:00', 'as_of': '2026-10-02',
    }, 'fundamentals': {'source': 'fixture_non_production', 'status': 'partial'}})
    actual = store.get('collection:035900')['sources']
    assert actual['flows'] == expected
    assert actual['fundamentals']['status'] == 'partial'


@pytest.mark.parametrize('existing', [False, True])
@pytest.mark.parametrize('trading_first', [False, True])
def test_parallel_generic_write_does_not_lose_toss_receipt(
        tmp_path, monkeypatch, existing, trading_first):
    # Two Store instances model separate processes. Pause the generic writer at
    # put(): the old get -> put lets the other writer commit then erases it.
    url = 'sqlite:///' + str(tmp_path / 'collection.sqlite')
    generic, trading = Store(url, tmp_path), Store(url, tmp_path)
    if existing:
        generic.put('collection:035900', {'sources': {}, 'attempts': 2})
    at_write, release = Event(), Event()
    first_store = trading if trading_first else generic
    original_put = first_store.put

    def paused_put(key, *args, **kwargs):
        if key == 'collection:035900':
            at_write.set()
            assert release.wait(5)
        return original_put(key, *args, **kwargs)

    monkeypatch.setattr(first_store, 'put', paused_put)
    def generic_update():
        return update_collection(generic, '035900', {
            'fundamentals': {'status': 'ready', 'source': 'fixture_non_production'},
        }, attempted=True)

    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            first = (workers.submit(toss_receipt, trading) if trading_first
                     else workers.submit(generic_update))
            assert at_write.wait(3)
            second = (workers.submit(generic_update) if trading_first
                      else workers.submit(toss_receipt, trading))
            try:
                second.result(timeout=0.5)
            except FutureTimeout:
                pass  # A correct transaction serializes this writer.
            finally:
                release.set()
            first.result(timeout=5)
            second.result(timeout=5)
        actual = generic.get('collection:035900', use_redis=False)
        assert actual['sources']['flows']['source'] == 'toss'
        assert actual['sources']['flows']['validated_rows'] == 1
        assert actual['sources']['fundamentals']['status'] == 'ready'
        assert actual['attempts'] == (3 if existing else 1)
    finally:
        release.set()
        generic.engine.dispose()
        trading.engine.dispose()


@pytest.mark.parametrize('status', ['ready', 'stale', 'error'])
def test_generic_copy_of_a_trading_source_cannot_certify_or_downgrade_it(store, status):
    toss_receipt(store)
    before = deepcopy(store.get('collection:035900'))
    update_collection(store, '035900', {'flows': {
        'source': 'toss', 'status': status, 'received_at': None, 'as_of': None,
    }}, error='GenericEnrichmentFailure' if status == 'error' else None)
    after = store.get('collection:035900')
    assert after['sources']['flows'] == before['sources']['flows']
    assert after.get('last_success_at') == before.get('last_success_at')
    if status == 'error':
        assert after['last_error'] == 'GenericEnrichmentFailure'
    # The owning writer can still record a real failure and then recover.
    record_trading_collection(store, '035900', 'investor-trading', failure=TimeoutError())
    failed = store.get('collection:035900')['sources']['flows']
    assert failed['status'] == 'stale'
    assert failed['as_of'] == '2026-10-08'
    toss_receipt(store)
    assert store.get('collection:035900')['sources']['flows']['status'] == 'ready'


@pytest.mark.parametrize('initial', ['pending', 'no_data'])
def test_unverified_or_empty_toss_status_does_not_hide_naver_only_coverage(store, initial):
    if initial == 'no_data':
        receipt = trading_collection_receipt('investor-trading', {
            'rows': [], 'status': 'no_data',
        })
        record_trading_collection(store, '035900', 'investor-trading',
                                 receipt, source={'status': 'no_data'})
    value = {'source': '네이버 증권 공개 투자자 집계', 'status': 'ready',
             'received_at': '2026-10-06T08:04:05+00:00', 'as_of': '2026-10-02'}
    update_collection(store, '035900', {'flows': value})
    assert store.get('collection:035900')['sources']['flows'] == value
    assert store.metadata('toss:investor-trading:035900') is None


def test_older_observation_is_not_a_new_success_but_same_date_correction_is_accepted(store):
    latest = {'source': 'OpenDART', 'status': 'ready', 'as_of': '2026-06-30',
              'received_at': '2026-10-10T04:00:00+00:00', 'note': 'fixture corrected'}
    update_collection(store, '035900', {'official': latest})
    before = store.get('collection:035900')
    update_collection(store, '035900', {'official': {
        **latest, 'received_at': '2026-10-10T12:59:00+09:00', 'note': 'fixture older',
    }})
    old = store.get('collection:035900')
    assert old['sources']['official'] == latest
    assert old['last_success_at'] == before['last_success_at']
    update_collection(store, '035900', {'official': {
        **latest, 'received_at': '2026-10-10T04:01:00+00:00', 'note': 'fixture new correction',
    }})
    assert store.get('collection:035900')['sources']['official']['note'] == 'fixture new correction'


def test_failed_same_provider_keeps_known_dates_without_claiming_new_success(store):
    latest = {'source': 'OpenDART', 'status': 'ready', 'as_of': '2026-06-30',
              'received_at': '2026-10-10T04:00:00+00:00'}
    update_collection(store, '035900', {'official': latest})
    before = store.get('collection:035900')
    update_collection(store, '035900', {'official': {
        'source': 'OpenDART', 'status': 'error', 'as_of': None, 'received_at': None,
    }}, error='FixtureTimeout')
    after = store.get('collection:035900')
    assert after['sources']['official']['status'] == 'error'
    assert after['sources']['official']['as_of'] == latest['as_of']
    assert after['sources']['official']['received_at'] == latest['received_at']
    assert after['sources']['official']['failed_at']
    assert after['last_success_at'] == before['last_success_at']
    assert after['last_error'] == 'FixtureTimeout'


def test_mixed_legacy_receipt_is_not_automatically_promoted_to_toss(store):
    mixed = {'source': '네이버 증권 공개 투자자 집계', 'status': 'ready',
             'as_of': '2026-10-02', 'endpoint': 'investor-trading',
             'validated_rows': 1, 'period_scope': 'checked_normalized_rows'}
    store.put('collection:035900', {'sources': {'flows': mixed}})
    update_collection(store, '035900', {'filings': {'source': 'OpenDART', 'status': 'pending'}})
    assert store.get('collection:035900')['sources']['flows'] == mixed
    assert store.metadata('toss:investor-trading:035900') is None


def test_failed_first_write_rolls_back_reservation_and_does_not_notify(store, monkeypatch):
    notified = []
    monkeypatch.setattr(store, 'notify_changed', notified.append)
    def failed_write(*_args, **_kwargs):
        raise OSError('fixture failure')
    monkeypatch.setattr(store, 'put', failed_write)
    with pytest.raises(OSError):
        update_collection(store, '035900', attempted=True)
    assert store.get('collection:035900') is None
    assert notified == []


def test_concurrent_generic_attempts_preserve_both_sections_and_counts(tmp_path):
    url = 'sqlite:///' + str(tmp_path / 'attempts.sqlite')
    first, second = Store(url, tmp_path), Store(url, tmp_path)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            jobs = [workers.submit(update_collection, current, '035900', {
                field: {'status': 'ready', 'source': 'fixture_non_production'},
            }, attempted=True) for current, field in [(first, 'official'), (second, 'brokers')] * 4]
            for job in jobs:
                job.result(timeout=5)
        result = first.get('collection:035900', use_redis=False)
        assert result['attempts'] == 8
        assert result['sources']['official']['status'] == 'ready'
        assert result['sources']['brokers']['status'] == 'ready'
    finally:
        first.engine.dispose()
        second.engine.dispose()
