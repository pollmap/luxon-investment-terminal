"""fixture_non_production: normalized trading data must reach collection status."""
from copy import deepcopy

import pytest

from services.market_dashboard.collection import collection_snapshot
from services.market_dashboard.store import Store
from services.market_dashboard.time_series import put_series
from services.market_dashboard.toss import TossCollector


@pytest.fixture
def store(tmp_path):
    value = Store('sqlite://', tmp_path)
    yield value
    value.engine.dispose()


def seed(store, endpoint, rows, *, complete=False):
    key = f'toss:{endpoint}:005930'
    put_series(store, key, {'rows': rows, 'status': 'ready',
                          'received_at': '2026-10-08T09:00:00+00:00',
                          'backfill_complete': complete,
                          'next_until': None if complete else '2024-01-01'}, reverse=True)
    return key


def project(store, endpoint, status='ready'):
    TossCollector(store, object())._project_trading('005930', endpoint, {'status': status})
    return store.metadata('collection:005930', {}).get('sources', {})


@pytest.mark.parametrize('compact', [False, True])
def test_credit_loan_and_lending_are_not_pending_after_real_normalization(
        store, monkeypatch, compact):
    monkeypatch.setenv('MARKET_COMPACT_TRADING_PROJECTIONS', str(int(compact)))
    seed(store, 'credit-trades', [{'date': '2026-10-07',
                                 'marginLoan': {'balanceQuantity': 0}, 'stockLoan': None}])
    state = project(store, 'credit-trades')
    assert state['credit']['status'] == 'ready'
    assert state['credit']['as_of'] == '2026-10-07'
    assert state['credit']['history_status'] == 'incomplete'
    assert state['stock_loan']['status'] == 'no_data'
    assert state['lending']['status'] == 'pending'
    seed(store, 'securities-lending', [{'date': '2026-10-06', 'balanceQuantity': 25}],
         complete=True)
    state = project(store, 'securities-lending')
    assert state['lending']['status'] == 'ready'
    assert state['lending']['backfill_complete'] is True
    assert state['credit']['as_of'] == '2026-10-07'
    totals = collection_snapshot(store, [{'code': '005930'}])['sources']
    assert totals['credit']['ready'] == totals['lending']['ready'] == 1
    assert totals['stock_loan']['no_data'] == 1
    assert totals['short_balance']['pending'] == 1


@pytest.mark.parametrize('compact', [False, True])
def test_null_fields_do_not_certify_counts_and_short_trade_is_not_balance(
        store, monkeypatch, compact):
    monkeypatch.setenv('MARKET_COMPACT_TRADING_PROJECTIONS', str(int(compact)))
    seed(store, 'credit-trades', [{'date': '2026-10-07',
                                 'marginLoan': {'balanceQuantity': None},
                                 'stockLoan': {'balanceQuantity': 3}}])
    state = project(store, 'credit-trades')
    assert state['credit']['status'] == 'error'
    assert state['credit']['reason'] == 'normalized_values_missing'
    assert state['stock_loan']['status'] == 'ready'
    seed(store, 'short-selling', [{'date': '2026-10-07', 'shortSellingVolume': 0,
                                 'shortSellingAmount': 0}])
    state = project(store, 'short-selling')
    assert state['short_trades']['status'] == 'ready'
    assert state['short_balance']['status'] == 'pending'


@pytest.mark.parametrize('compact', [False, True])
def test_unchanged_projection_repairs_missing_collection_without_history_read(store, monkeypatch,
                                                                            compact):
    monkeypatch.setenv('MARKET_COMPACT_TRADING_PROJECTIONS', str(int(compact)))
    seed(store, 'credit-trades', [{'date': '2026-10-07',
                                 'marginLoan': {'balanceQuantity': 10}}])
    before = project(store, 'credit-trades')
    store.put('collection:005930', {'sources': {}})
    monkeypatch.setattr(store.time_series, 'rows', lambda *_a, **_k: pytest.fail('History read'))
    after = project(store, 'credit-trades')
    for field in ('status', 'as_of', 'received_at', 'history_status'):
        assert after['credit'][field] == before['credit'][field]


@pytest.mark.asyncio
@pytest.mark.parametrize('compact', [False, True])
async def test_failed_page_keeps_last_good_dates_and_reports_stale(store, monkeypatch, compact):
    monkeypatch.setenv('MARKET_COMPACT_TRADING_PROJECTIONS', str(int(compact)))
    key = seed(store, 'securities-lending', [{'date': '2026-10-07', 'balanceQuantity': 10}])
    before = deepcopy(project(store, 'securities-lending')['lending'])
    original = store.get(key)

    class Failed:
        async def get(self, *_):
            raise TimeoutError('fixture_non_production')

    with pytest.raises(TimeoutError):
        await TossCollector(store, Failed()).fundamentals_of_trading(
            '005930', ('securities-lending',))
    after = store.metadata('collection:005930')['sources']['lending']
    assert after['status'] == 'stale'
    assert after['failed_at'] and after['last_error_at']
    for field in ('as_of', 'period', 'received_at', 'last_success_at'):
        assert after[field] == before[field]
    assert store.get(key) == original


@pytest.mark.parametrize('compact', [False, True])
def test_status_write_failure_preserves_data_and_retries_same_revision(store, monkeypatch, compact):
    monkeypatch.setenv('MARKET_COMPACT_TRADING_PROJECTIONS', str(int(compact)))
    key = seed(store, 'credit-trades', [{'date': '2026-10-07',
                                       'marginLoan': {'balanceQuantity': 10}}])
    source_before = store.get(key)
    original = store.put

    def fail(key, *args, **kwargs):
        if key == 'collection:005930':
            raise OSError('fixture interrupted collection receipt')
        return original(key, *args, **kwargs)

    monkeypatch.setattr(store, 'put', fail)
    with pytest.raises(OSError):
        project(store, 'credit-trades')
    assert store.get(key) == source_before
    assert store.metadata('collection:005930') is None
    if compact:
        # Both mirrors and marker share the failed receipt transaction.
        assert store.metadata('financing:005930') is None
        assert store.metadata('toss-projection:credit-trades:005930') is None
    monkeypatch.setattr(store, 'put', original)
    assert project(store, 'credit-trades')['credit']['status'] == 'ready'


@pytest.mark.asyncio
@pytest.mark.parametrize('compact', [False, True])
async def test_empty_latest_response_preserves_last_good_clock_and_not_history_completion(
        store, monkeypatch, compact):
    monkeypatch.setenv('MARKET_COMPACT_TRADING_PROJECTIONS', str(int(compact)))
    seed(store, 'securities-lending', [{'date': '2026-10-07', 'balanceQuantity': 10}],
         complete=True)
    before = deepcopy(project(store, 'securities-lending')['lending'])

    class Empty:
        async def get(self, *_):
            return {'records': [], 'nextUntil': None}, {'source': 'fixture_non_production'}

    collector = TossCollector(store, Empty())
    await collector.fundamentals_of_trading('005930', ('securities-lending',))
    after = store.metadata('collection:005930')['sources']['lending']
    assert after['status'] == 'stale' and after['backfill_complete'] is True
    for field in ('as_of', 'received_at', 'last_success_at'):
        assert after[field] == before[field]
    assert store.get('financing:005930')['lending']['rows'][0]['balance'] == 10


@pytest.mark.parametrize('compact', [False, True])
def test_section_dates_and_global_error_history_are_preserved(store, monkeypatch, compact):
    monkeypatch.setenv('MARKET_COMPACT_TRADING_PROJECTIONS', str(int(compact)))
    seed(store, 'credit-trades', [
        {'date': '2025-12-01', 'marginLoan': None, 'stockLoan': {'balanceQuantity': 12}},
        {'date': '2026-10-07', 'marginLoan': {'balanceQuantity': 2}, 'stockLoan': None},
    ])
    store.put('collection:005930', {'last_success_at': '2026-01-01',
                                   'last_error': 'OtherProviderError', 'attempts': 9})
    state = project(store, 'credit-trades')
    assert state['credit']['as_of'] == '2026-10-07'
    assert state['stock_loan']['as_of'] == '2025-12-01'
    current = store.metadata('collection:005930')
    assert current['last_error'] == 'OtherProviderError'
    assert current['last_success_at'] == '2026-01-01' and current['attempts'] == 9


def test_credit_without_balance_is_partial_not_ready_and_invalid_date_is_not_covered(store):
    seed(store, 'credit-trades', [{'date': '2026-10-07',
                                 'marginLoan': {'newQuantity': 2},
                                 'stockLoan': {'balanceQuantity': None}}])
    state = project(store, 'credit-trades')
    assert state['credit']['status'] == 'partial'
    assert state['stock_loan']['status'] == 'error'
    from services.market_dashboard.collection import trading_collection_receipt
    receipt = trading_collection_receipt('securities-lending', {
        'lending': {'status': 'ready', 'rows': [{'date': 'invalid', 'balance': 10}]}})
    assert receipt['lending']['status'] == 'error'
    assert receipt['lending']['validated_rows'] == 0


@pytest.mark.parametrize('compact', [False, True])
def test_pre_upgrade_marker_repairs_status_from_verified_output_only(store, monkeypatch, compact):
    monkeypatch.setenv('MARKET_COMPACT_TRADING_PROJECTIONS', str(int(compact)))
    seed(store, 'investor-trading', [{'date': '2026-10-07',
                                    'individual': {'netBuyVolume': 0},
                                    'foreigner': {'netBuyVolume': -3},
                                    'institution': {'netBuyVolume': 3}}])
    project(store, 'investor-trading')
    key = 'toss-projection:investor-trading:005930'
    marker = store.metadata(key)
    marker.pop('collection_receipt')
    store.put(key, marker)
    store.put('collection:005930', {'sources': {}})
    monkeypatch.setattr(store.time_series, 'rows', lambda *_a, **_k: pytest.fail('History read'))
    assert project(store, 'investor-trading')['flows']['status'] == 'ready'
    assert store.metadata(key)['collection_receipt']['flows']['validated_rows'] == 1


@pytest.mark.asyncio
async def test_later_page_failure_keeps_successful_prefix_but_does_not_report_ready(store):
    class Partial:
        calls = 0

        async def get(self, *_):
            self.calls += 1
            if self.calls == 2:
                raise TimeoutError('fixture_non_production')
            return {'records': [{'date': '2026-10-07', 'balanceQuantity': 8}],
                    'nextUntil': '2026-10-06'}, {'source': 'fixture_non_production'}

    with pytest.raises(TimeoutError):
        await TossCollector(store, Partial()).fundamentals_of_trading(
            '005930', ('securities-lending',), pages=2)
    state = store.metadata('collection:005930')['sources']['lending']
    assert state['status'] == 'stale' and state['validated_rows'] == 1
    assert state['history_status'] == 'incomplete'
    assert store.metadata('toss:securities-lending:005930')['next_until'] == '2026-10-06'
