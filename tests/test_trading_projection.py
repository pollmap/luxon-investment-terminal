"""fixture_non_production: compact mirrors never become the history source."""
from copy import deepcopy
from datetime import date, timedelta

import pytest

from services.market_dashboard.financing import public_financing
from services.market_dashboard.short_selling import public_short_selling
from services.market_dashboard.source_trace_store import SourceTraceIntegrityError
from services.market_dashboard.store import Store
from services.market_dashboard.time_series import put_series
from services.market_dashboard.toss import TossCollector
from services.market_dashboard.trading_projection import (
    REFERENCE,
    TradingProjectionUnavailable,
    compact_projection,
    read_financing,
    read_flows,
    read_short_selling,
)


@pytest.fixture
def store(tmp_path):
    value = Store('sqlite://', tmp_path)
    yield value
    value.engine.dispose()


def seed(store, endpoint, rows, code='005930'):
    put_series(store, f'toss:{endpoint}:{code}', {
        'rows': [{**row, 'source_trace': {'source': 'toss', 'fixture_non_production': True}}
                 for row in rows], 'status': 'ready', 'backfill_complete': True,
    }, reverse=True)
    TossCollector(store, object())._project_trading(code, endpoint, {'status': 'ready'})


def credit_rows(count=2000):
    return [{'date': (date(2020, 1, 1) + timedelta(days=i)).isoformat(),
             'marginLoan': {'balanceQuantity': i, 'newQuantity': None, 'balanceRate': '0.125'},
             'stockLoan': {'balanceQuantity': i * 2} if i < count - 400 else None}
            for i in range(count)]


def test_financing_bounded_range_uses_each_sections_actual_last_date(store, monkeypatch):
    seed(store, 'credit-trades', credit_rows())
    expected = public_financing(store.get('financing:005930'), 'month')
    compact_projection(store, '005930', 'credit-trades', {'status': 'ready'})
    original = store.time_series.rows
    calls = []
    def measured(*args, **kwargs):
        rows = original(*args, **kwargs)
        calls.append({'rows': len(rows), **kwargs})
        return rows
    monkeypatch.setattr(store.time_series, 'rows', measured)
    actual = read_financing(store, '005930', 'month')
    assert actual == expected
    assert len(calls) == 2 and sum(c['rows'] for c in calls) <= 64
    assert all(c['start'] and c['end'] for c in calls)
    assert actual['credit']['rows'][-1]['date'] != actual['stock_loan']['rows'][-1]['date']


@pytest.mark.parametrize('endpoint', ['investor-trading', 'credit-trades',
                                      'securities-lending', 'short-selling'])
def test_compaction_preserves_complete_public_result_and_does_not_create_series(
        store, monkeypatch, endpoint):
    rows = [
        {'date': '2024-02-29', 'individual': {'netBuyVolume': 0},
         'marginLoan': {'balanceQuantity': 0}, 'balanceQuantity': 0,
         'shortSellingVolume': 0},
        {'date': '2024-03-31', 'individual': {'netBuyVolume': None},
         'marginLoan': {'balanceQuantity': 10}, 'balanceQuantity': 10,
         'shortSellingVolume': 10},
    ]
    seed(store, endpoint, rows)
    store.put('detail:005930', {**store.get('detail:005930', {}), 'company': {'name': 'keep'}})
    short = store.get('short-selling:005930', {})
    short['balance'] = {'status': 'ready', 'rows': [
        {'date': '2023-01-01', 'volume': 7, 'source_trace': {'source': 'krx'}}]}
    store.put('short-selling:005930', short)
    readers = {'investor-trading': lambda: read_flows(store, '005930'),
               'credit-trades': lambda: read_financing(store, '005930', 'all'),
               'securities-lending': lambda: read_financing(store, '005930', 'all'),
               'short-selling': lambda: read_short_selling(store, '005930', 'all')}
    before = readers[endpoint]()
    source_before = store.metadata(f'toss:{endpoint}:005930')
    with store.engine.connect() as db:
        count_before = db.exec_driver_sql('SELECT COUNT(*) FROM market_time_series').scalar()
    monkeypatch.setenv('MARKET_COMPACT_TRADING_PROJECTIONS', '1')
    TossCollector(store, object())._project_trading('005930', endpoint, {'status': 'ready'})
    assert readers[endpoint]() == before
    assert store.metadata(f'toss:{endpoint}:005930') == source_before
    assert store.metadata(f'toss-compaction:{endpoint}:005930')['status'] == 'compacted'
    assert store.get('detail:005930')['company']['name'] == 'keep'
    assert store.get('short-selling:005930')['balance'] == short['balance']
    with store.engine.connect() as db:
        count_after = db.exec_driver_sql('SELECT COUNT(*) FROM market_time_series').scalar()
        assert count_after == count_before


def test_unmatched_legacy_is_retained_without_false_compaction(store, monkeypatch):
    seed(store, 'credit-trades', credit_rows(2))
    key = 'financing:005930'
    altered = deepcopy(store.get(key))
    altered['credit']['rows'][0]['balance'] = 123456
    store.put(key, altered)
    before = store.metadata(key)
    monkeypatch.setenv('MARKET_COMPACT_TRADING_PROJECTIONS', '1')
    TossCollector(store, object())._project_trading('005930', 'credit-trades', {'status': 'ready'})
    assert store.metadata(key) == before
    assert REFERENCE not in store.metadata(key)
    assert store.metadata('toss-compaction:credit-trades:005930')['status'] == 'legacy_preserved'
    source_key = 'toss:credit-trades:005930'
    store.put(source_key, {**store.metadata(source_key), 'received_at': 'later receipt only'})
    def forbidden(*_args, **_kwargs):
        pytest.fail('An unchanged rejected candidate should not read all history again')
    monkeypatch.setattr(store.time_series, 'rows', forbidden)
    TossCollector(store, object())._project_trading('005930', 'credit-trades', {'status': 'ready'})


def test_new_lending_preserves_compacted_credit_reference(store, monkeypatch):
    seed(store, 'credit-trades', credit_rows(3))
    monkeypatch.setenv('MARKET_COMPACT_TRADING_PROJECTIONS', '1')
    collector = TossCollector(store, object())
    collector._project_trading('005930', 'credit-trades', {'status': 'ready'})
    credit = deepcopy(store.metadata('financing:005930')['credit'])
    seed(store, 'securities-lending', [{'date': '2026-01-01', 'balanceQuantity': 55}])
    snapshot = store.metadata('financing:005930')
    assert snapshot['credit'] == credit
    assert set(snapshot[REFERENCE]['fields']) == {'credit', 'stock_loan', 'lending'}
    assert read_financing(store, '005930', 'all')['credit']['rows'][0]['balance'] == 0


def test_source_correction_updates_compact_reference_without_full_history(store, monkeypatch):
    seed(store, 'credit-trades', credit_rows())
    monkeypatch.setenv('MARKET_COMPACT_TRADING_PROJECTIONS', '1')
    collector = TossCollector(store, object())
    collector._project_trading('005930', 'credit-trades', {'status': 'ready'})
    put_series(store, 'toss:credit-trades:005930', {'status': 'ready', 'rows': [
        {'date': '2020-01-01', 'marginLoan': {'balanceQuantity': 77},
         'source_trace': {'source': 'toss', 'fixture_non_production': True}}
    ]}, reverse=True)
    calls = []
    original = store.time_series.rows
    def measured(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(len(result))
        return result
    monkeypatch.setattr(store.time_series, 'rows', measured)
    collector._project_trading('005930', 'credit-trades', {'status': 'ready'})
    assert sum(calls) <= 2
    result = read_financing(store, '005930', 'all')
    assert result['credit']['rows'][0]['balance'] == 77
    assert len(store.metadata('financing:005930')['credit']['rows']) == 1


def test_company_write_between_proof_and_commit_defers_without_overwriting(store, monkeypatch):
    seed(store, 'investor-trading', [
        {'date': '2026-01-01', 'individual': {'netBuyVolume': 1}},
        {'date': '2026-01-02', 'individual': {'netBuyVolume': 2}},
    ])
    from services.market_dashboard import trading_projection as module
    real = module._summary
    def raced(*args):
        value = real(*args)
        store.put('detail:005930', {**store.get('detail:005930'), 'company': {'name': 'new'}})
        return value
    monkeypatch.setattr(module, '_summary', raced)
    compact_projection(store, '005930', 'investor-trading', {'status': 'ready'})
    assert store.get('detail:005930')['company'] == {'name': 'new'}
    assert REFERENCE not in store.metadata('detail:005930')
    assert REFERENCE not in store.metadata('toss-normalized:flows:005930')


def test_compaction_partial_failure_rolls_back_both_mirrors(store, monkeypatch):
    seed(store, 'credit-trades', credit_rows(2))
    before = store.metadata('toss-normalized:financing:005930')
    put = store.put
    def fail(key, *args, **kwargs):
        if key == 'financing:005930':
            raise RuntimeError('fixture interrupted compaction')
        return put(key, *args, **kwargs)
    monkeypatch.setattr(store, 'put', fail)
    with pytest.raises(RuntimeError):
        compact_projection(store, '005930', 'credit-trades', {'status': 'ready'})
    assert store.metadata('toss-normalized:financing:005930') == before
    assert store.metadata('toss-compaction:credit-trades:005930') is None


@pytest.mark.parametrize('damage', ['source', 'kind', 'normalizer', 'field', 'format'])
def test_foreign_or_unknown_compact_reference_rejected_even_with_all_sources(
        store, monkeypatch, damage):
    seed(store, 'credit-trades', credit_rows(2))
    seed(store, 'securities-lending', [{'date': '2026-01-01', 'balanceQuantity': 1}])
    compact_projection(store, '005930', 'credit-trades', {'status': 'ready'})
    payload = store.metadata('financing:005930')
    ref = payload[REFERENCE]
    if damage == 'format':
        ref['format'] = 'unknown'
    elif damage == 'field':
        ref['fields']['secret'] = ref['fields'].pop('credit')
    elif damage == 'normalizer':
        ref['fields']['credit']['normalizer'] = 'unknown'
    else:
        ref['fields']['credit']['source_key'] = ('toss:credit-trades:000270' if damage == 'source'
                                                else 'toss:short-selling:005930')
    store.put('financing:005930', payload)
    with pytest.raises(TradingProjectionUnavailable):
        read_financing(store, '005930', 'all')


def test_removed_source_rows_and_corrupt_evidence_do_not_become_empty_success(store, monkeypatch):
    seed(store, 'investor-trading', [
        {'date': '2026-01-01', 'individual': {'netBuyVolume': 1}}
    ])
    compact_projection(store, '005930', 'investor-trading', {'status': 'ready'})
    original = store.time_series.rows
    def corrupt(*args, **kwargs):
        raise SourceTraceIntegrityError('fixture broken evidence')
    monkeypatch.setattr(store.time_series, 'rows', corrupt)
    with pytest.raises(TradingProjectionUnavailable):
        read_flows(store, '005930')
    monkeypatch.setattr(store.time_series, 'rows', original)
    with store.engine.begin() as db:
        db.execute(store.time_series.table.delete().where(
            store.time_series.table.c.series == 'toss:investor-trading:005930'))
    with pytest.raises(TradingProjectionUnavailable):
        read_flows(store, '005930')


def test_dual_reader_has_no_flag_dependency_and_keeps_legacy_balances(store):
    credit = {'code': '005930', 'credit': {'status': 'ready', 'rows': [
        {'date': '2026-01-01', 'balance': 0}]}}
    short = {'code': '005930', 'balance': {'status': 'stale', 'rows': [
        {'date': '2026-01-01', 'volume': None}]}}
    store.put('financing:005930', credit)
    store.put('short-selling:005930', short)
    assert read_financing(store, '005930', 'all') == public_financing(credit, 'all')
    assert read_short_selling(store, '005930', 'all') == public_short_selling(short, 'all')


def test_new_source_can_create_compact_mirrors_atomically(store):
    put_series(store, 'toss:investor-trading:005930', {'status': 'ready', 'rows': [
        {'date': '2026-01-01', 'individual': {'netBuyVolume': 1},
         'source_trace': {'source': 'toss', 'fixture_non_production': True}}
    ]}, reverse=True)
    compact_projection(store, '005930', 'investor-trading', {'status': 'ready'})
    assert read_flows(store, '005930')[0]['individual'] == 1
    assert store.metadata('detail:005930')[REFERENCE]['format']
    assert store.metadata('detail-summary:005930')['present']


def test_new_key_reservation_does_not_overwrite_a_racing_writer(store):
    from services.market_dashboard.trading_projection import _CompactionChanged, _reserve_absent
    store.put('financing:005930', {'independent': {'value': 'keep'}})
    with store.engine.begin() as db, pytest.raises(_CompactionChanged):
        _reserve_absent(store, db, ('financing:005930',), {})
    assert store.metadata('financing:005930') == {'independent': {'value': 'keep'}}


@pytest.mark.parametrize('endpoint', ['investor-trading', 'credit-trades', 'short-selling'])
@pytest.mark.parametrize('empty_source', [False, True])
def test_unverified_raw_never_hides_legacy_history(store, endpoint, empty_source):
    seed(store, endpoint, [{'date': '2026-01-02', 'individual': {'netBuyVolume': 3},
                           'marginLoan': {'balanceQuantity': 3}, 'shortSellingVolume': 3}])
    from services.market_dashboard.toss import TRADING_PROJECTION_FIELDS
    for prefix, fields in TRADING_PROJECTION_FIELDS[endpoint].items():
        if prefix.startswith('toss-normalized:'):
            continue  # One mirror alone retains the independent historical row.
        payload = store.metadata(prefix + '005930')
        for field in fields:
            if field == 'status':
                continue
            rows = payload[field] if isinstance(payload[field], list) else payload[field]['rows']
            if not rows:
                continue
            older = {**rows[0], 'date': '2010-01-01',
                     'source_trace': {'source': 'krx', 'fixture_non_production': True}}
            rows.append(older) if endpoint == 'investor-trading' else rows.insert(0, older)
        store.put(prefix + '005930', payload)
    if endpoint == 'investor-trading':
        expected = store.get('detail:005930')['flows']
        def reader():
            return read_flows(store, '005930')
    elif endpoint == 'credit-trades':
        expected = public_financing(store.get('financing:005930'), 'all')
        def reader():
            return read_financing(store, '005930', 'all')
    else:
        expected = public_short_selling(store.get('short-selling:005930'), 'all')
        def reader():
            return read_short_selling(store, '005930', 'all')
    if empty_source:
        key = f'toss:{endpoint}:005930'
        with store.engine.begin() as db:
            db.execute(store.time_series.table.delete().where(
                store.time_series.table.c.series == key))
        store.put(key, {**store.metadata(key), 'row_count': 0, 'first_time': None,
                        'last_time': None, 'status': 'no_data'})
    assert reader() == expected
    compact_projection(store, '005930', endpoint, {'status': 'ready'})
    assert reader() == expected
    assert store.metadata(f'toss-compaction:{endpoint}:005930')['status'] == 'legacy_preserved'


def test_partial_financing_mirrors_fall_back_per_section(store):
    credit = {'status': 'ready', 'rows': [{'date': '2026-01-02', 'balance': 2}]}
    lending = {'status': 'ready', 'rows': [{'date': '2010-01-01', 'balance': 8}]}
    store.put('toss-normalized:financing:005930', {'code': '005930', 'credit': credit})
    store.put('financing:005930', {'code': '005930', 'lending': lending})
    expected = public_financing({'code': '005930', 'credit': credit, 'lending': lending}, 'all')
    assert read_financing(store, '005930', 'all') == expected


@pytest.mark.parametrize('endpoint', ['investor-trading', 'credit-trades', 'short-selling'])
@pytest.mark.parametrize('correction', [False, True])
def test_first_enabled_collection_advances_known_projection_without_losing_dates(
        store, monkeypatch, endpoint, correction):
    def raw(day, value, digest):
        return {'date': day, 'individual': {'netBuyVolume': value},
                'marginLoan': {'balanceQuantity': value}, 'shortSellingVolume': value,
                'source_trace': {'source': 'toss', 'source_document_id': digest * 64,
                                 'fixture_non_production': True}}
    key = f'toss:{endpoint}:005930'
    put_series(store, key, {'rows': [raw('2026-01-01', 1, 'a')], 'status': 'ready'}, reverse=True)
    collector = TossCollector(store, object())
    collector._project_trading('005930', endpoint, {'status': 'ready'})
    new_rows = [raw('2026-01-02', 2, 'b')]
    if correction:
        new_rows.append(raw('2026-01-01', 9, 'b'))
    put_series(store, key, {'rows': new_rows, 'status': 'ready'}, reverse=True)
    monkeypatch.setenv('MARKET_COMPACT_TRADING_PROJECTIONS', '1')
    collector._project_trading('005930', endpoint, {'status': 'ready'})
    audit = store.metadata(f'toss-compaction:{endpoint}:005930')
    assert audit['status'] == 'compacted'
    assert audit['verification']['method'] == 'previous-toss-projection-retained-v1'
    if endpoint == 'investor-trading':
        rows = read_flows(store, '005930')
        field = 'individual'
    elif endpoint == 'credit-trades':
        rows = read_financing(store, '005930', 'all')['credit']['rows']
        field = 'balance'
    else:
        rows = read_short_selling(store, '005930', 'all')['trades']['rows']
        field = 'volume'
    by_date = {row['date']: row for row in rows}
    assert set(by_date) == {'2026-01-01', '2026-01-02'}
    assert by_date['2026-01-01'][field] == (9 if correction else 1)


@pytest.mark.parametrize('damage', ['untraced', 'same_document_correction', 'missing_date'])
def test_prior_projection_proof_cannot_certify_unknown_or_removed_history(store, damage):
    def raw(day, value):
        return {'date': day, 'individual': {'netBuyVolume': value},
                'source_trace': {'source': 'toss', 'source_document_id': 'a' * 64,
                                 'fixture_non_production': True}}
    key = 'toss:investor-trading:005930'
    original = raw('2026-01-01', 1)
    if damage == 'untraced':
        original.pop('source_trace')
    put_series(store, key, {'rows': [original], 'status': 'ready'}, reverse=True)
    TossCollector(store, object())._project_trading(
        '005930', 'investor-trading', {'status': 'ready'})
    expected = store.get('toss-normalized:flows:005930')['rows']
    updated = [raw('2026-01-02', 2)]
    if damage == 'same_document_correction':
        updated.append(raw('2026-01-01', 9))
    if damage == 'missing_date':
        with store.engine.begin() as db:
            db.execute(store.time_series.table.delete().where(
                store.time_series.table.c.series == key))
    put_series(store, key, {'rows': updated, 'status': 'ready'}, reverse=True)
    if damage == 'missing_date':
        store.put(key, {**store.metadata(key), 'row_count': 1,
                        'first_time': '2026-01-02', 'last_time': '2026-01-02'})
    compact_projection(store, '005930', 'investor-trading', {'status': 'ready'})
    assert store.metadata('toss-compaction:investor-trading:005930')['status'] == 'legacy_preserved'
    assert read_flows(store, '005930') == expected


def test_independent_history_written_after_compaction_stays_visible(store):
    seed(store, 'credit-trades', credit_rows(2))
    compact_projection(store, '005930', 'credit-trades', {'status': 'ready'})
    payload = store.metadata('financing:005930')
    payload['credit']['rows'].insert(0, {'date': '2010-01-01', 'balance': 42,
                                       'source_trace': {'source': 'krx'}})
    store.put('financing:005930', payload)
    compact_projection(store, '005930', 'credit-trades', {'status': 'ready'})
    result = read_financing(store, '005930', 'all')
    assert [(row['date'], row['balance']) for row in result['credit']['rows']] == [
        ('2010-01-01', 42), ('2020-01-01', 0), ('2020-01-02', 1)]
    assert store.metadata('toss-compaction:credit-trades:005930')['status'] == 'legacy_preserved'


def test_empty_replaced_source_cannot_erase_compacted_history(store):
    seed(store, 'credit-trades', credit_rows(2))
    compact_projection(store, '005930', 'credit-trades', {'status': 'ready'})
    before = store.metadata('financing:005930')
    key = 'toss:credit-trades:005930'
    with store.engine.begin() as db:
        db.execute(store.time_series.table.delete().where(store.time_series.table.c.series == key))
    store.put(key, {**store.metadata(key), 'row_count': 0, 'first_time': None,
                    'last_time': None, 'series_revision': 'replacement'})
    with pytest.raises(TradingProjectionUnavailable):
        compact_projection(store, '005930', 'credit-trades', {'status': 'ready'})
    with pytest.raises(TradingProjectionUnavailable):
        read_financing(store, '005930', 'all')
    assert store.metadata('financing:005930') == before
