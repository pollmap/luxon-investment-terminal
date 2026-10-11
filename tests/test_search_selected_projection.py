"""Search compatibility and bounded hydration using fixture-only stored evidence."""

import importlib

import pytest
from sqlalchemy import event

from services.market_dashboard import request_timings as timings
from services.market_dashboard import search_view
from services.market_dashboard.enriched_view import publish, rebuild
from services.market_dashboard.models import Query, apply_query
from services.market_dashboard.search_view import selected_inputs
from services.market_dashboard.store import Store

market = importlib.import_module('services.market_dashboard.app')


@pytest.fixture
def source(tmp_path, monkeypatch):
    store = Store(url='sqlite://', root=tmp_path)
    store.put('enriched', {})
    store.put('universe', {'rows': [
        {'code': str(100000 + index), 'name': f'삼성 fixture {index}',
         'aliases': [f'별칭{index}'], 'market': 'KOSPI', 'price': 100 + index,
         'change': 1, 'value': 1000 - index, 'as_of': '2026-10-08',
         'source_trace': {'source': 'fixture_non_production'}}
        for index in range(80)]})
    monkeypatch.setattr(market, 'get_store', lambda: store)
    monkeypatch.setattr(market, '_security_universe_cache', None)
    monkeypatch.setattr(market, '_security_rows', market.OrderedDict())
    monkeypatch.setattr(market, '_rows_cache', {})
    yield store
    store.engine.dispose()


def compact(row):
    fields = ('logo_url', 'code', 'name', 'market', 'sector', 'price', 'change',
              'as_of', 'received_at', 'market_status', 'source_trace')
    return {**{key: row[key] for key in fields if key in row},
            'facts': {key: row['facts'][key] for key in ('price', 'change')
                      if key in row.get('facts', {})}}


@pytest.mark.parametrize('query', ['', '삼성', 'ㅅㅅ', '별칭79', '100001', '새별칭', '없음'])
def test_search_matches_existing_list_semantics_without_full_hydration(source, monkeypatch, query):
    source.put('enriched', {
        '100079': {'name': '삼성 변경', 'aliases': ['새별칭'], 'sector': 'fixture',
                   'facts': {'value': {'value': 5000, 'period': '2026-10-08'}},
                   'large_financial_history': ['fixture'] * 5000},
        '100078': {'facts': {'value': {'value': 9000, 'period': '2026-10-07'}}},
    })
    source.put('quotes', {
        '100077': {'price': 200, 'as_of': '2026-10-08',
                   'quote_trace': {'source': 'fixture_non_production'},
                   'daily_facts': {'value': {'value': 6000, 'period': '2026-10-08'}}},
        '100076': {'price': 999, 'as_of': '2026-10-07',
                   'quote_trace': {'source': 'fixture_non_production'}},
    })
    source.update_quote('100075', {'price': 300, 'as_of': '2026-10-08',
        'quote_trace': {'source': 'toss'},
        'daily_facts': {'value': {'value': 7000, 'period': '2026-10-08'}}})
    expected = apply_query(market.rows_with_details(check_revision=True)[0],
                           Query(q=query, limit=30))
    expected['rows'] = [compact(row) for row in expected['rows']]
    original_get, original_assemble = source.get, market.assemble_rows
    sizes, selections = [], []

    def guarded_get(key, *args, **kwargs):
        assert key not in ('enriched', 'quotes'), 'Search decoded an entire compatibility payload'
        return original_get(key, *args, **kwargs)

    def counted_assemble(store, inputs=None, **kwargs):
        sizes.append(len(inputs[0]['rows']))
        return original_assemble(store, inputs, **kwargs)

    def sql(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.startswith('WITH requested'):
            selections.append(statement)

    monkeypatch.setattr(source, 'get', guarded_get)
    monkeypatch.setattr(market, 'assemble_rows', counted_assemble)
    event.listen(source.engine, 'before_cursor_execute', sql)
    try:
        assert market.search_payload(query, 30) == expected
    finally:
        event.remove(source.engine, 'before_cursor_execute', sql)
    assert sizes == [len(expected['rows'])]
    assert len(selections) == (1 if expected['rows'] else 0)
    assert sizes[0] <= 30


def test_turnover_update_can_promote_a_security_outside_initial_limit(source):
    assert market.search_payload('삼성', 1)['rows'][0]['code'] == '100000'
    source.update_quote('100079', {'price': 179, 'as_of': '2026-10-08',
        'quote_trace': {'source': 'toss'},
        'daily_facts': {'value': {'value': 9999, 'period': '2026-10-08'}}})
    assert market.search_payload('삼성', 1)['rows'][0]['code'] == '100079'


def test_live_single_quote_refresh_projects_only_changed_row(source):
    source.put('quotes', {'100079': {'aliases': ['과거별칭']}})
    source.update_quote('100079', {'price': 179, 'as_of': '2026-10-08T09:00:00+09:00',
                                  'quote_trace': {'source': 'toss'}})
    market.search_payload('삼성', 30)
    source.update_quote('100079', {'price': 180, 'as_of': '2026-10-08T10:00:00+09:00',
        'aliases': ['새로운별칭'], 'quote_trace': {'source': 'toss'},
        'daily_facts': {'value': {'value': 9999, 'period': '2026-10-08'}}})
    statements = []

    def sql(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(source.engine, 'before_cursor_execute', sql)
    try:
        with timings.collect() as record:
            result = market.search_payload('삼성', 30)
    finally:
        event.remove(source.engine, 'before_cursor_execute', sql)
    assert result['rows'][0]['code'] == '100079'
    assert result['rows'][0]['price'] == 180
    assert record.counts['search_delta'] == record.counts['search_delta_rows'] == 1
    assert 'search_full' not in record.counts
    assert not any('json_each' in statement for statement in statements)
    expected = apply_query(market.rows_with_details(check_revision=True)[0],
                           Query(q='삼성', limit=30))
    expected['rows'] = [compact(row) for row in expected['rows']]
    assert result == expected
    assert market.search_payload('과거별칭', 30)['total'] == 0
    assert market.search_payload('새로운별칭', 30)['rows'][0]['price'] == 180


@pytest.mark.parametrize('change', ['legacy', 'deleted', 'large_batch'])
def test_quote_delta_falls_back_for_legacy_delete_and_large_batch(source, change):
    source.put('quotes', {'100079': {'aliases': ['복원별칭'], 'value': 9999,
                                   'as_of': '2026-10-08'}})
    source.update_quote('100079', {'aliases': ['최근별칭']})
    market.search_payload('', 30)
    if change == 'legacy':
        # Simulate an old external writer, bypassing Store.put's safe conversion
        # of compatibility book writes into per-code updates.
        with source.engine.begin() as db:
            source.put('quotes', {'100078': {'value': 99999, 'as_of': '2026-10-08'}},
                       _connection=db)
    elif change == 'deleted':
        with source.engine.begin() as db:
            db.execute(source.snapshots.delete().where(source.snapshots.c.key == 'quote:100079'))
        source.put('quote-book', {'change': 'fixture-deletion'})
    else:
        source.update_quotes({str(100000 + index): {'value': 9000 + index}
                              for index in range(31)})
    with timings.collect() as record:
        actual = market.search_payload('', 30)
    expected = apply_query(market.rows_with_details(check_revision=True)[0], Query(limit=30))
    expected['rows'] = [compact(row) for row in expected['rows']]
    assert actual == expected
    assert record.counts['search_fallback'] == record.counts['search_full'] == 1
    assert 'search_delta' not in record.counts
    if change == 'deleted':
        assert market.search_payload('최근별칭', 30)['total'] == 0
        assert market.search_payload('복원별칭', 30)['total'] == 1


def test_concurrent_quote_change_never_caches_mixed_heads(source, monkeypatch):
    source.update_quote('100079', {'value': 1, 'as_of': '2026-10-08'})
    market.search_payload('', 30)
    source.update_quote('100079', {'value': 5000})
    original = search_view._records
    changed = False

    def interleaved(store, statement, params=None):
        nonlocal changed
        records = original(store, statement, params)
        if params and 'keys' in params and not changed:
            changed = True
            source.update_quote('100078', {'value': 9999, 'as_of': '2026-10-08'})
        return records

    monkeypatch.setattr(search_view, '_records', interleaved)
    with timings.collect() as record:
        actual = market.search_payload('', 30)
    assert changed
    assert record.counts['search_fallback'] == record.counts['search_full'] == 1
    assert 'search_delta' not in record.counts
    expected = apply_query(market.rows_with_details(check_revision=True)[0], Query(limit=30))
    expected['rows'] = [compact(row) for row in expected['rows']]
    assert actual == expected
    assert market.search_payload('', 30) == expected


def test_missing_day_and_equal_values_keep_legacy_order(source):
    source.put('universe', {'rows': [
        {'code': '100000', 'name': 'fixture_non_production', 'value': 10,
         'as_of': '2026-10-08', 'source_trace': {}},
        {'code': '100001', 'name': 'fixture_non_production', 'value': 10,
         'as_of': '2026-10-08', 'source_trace': {}},
        {'code': '100002', 'name': 'fixture_non_production', 'value': 99,
         'source_trace': {}},
    ]})
    assert [r['code'] for r in market.search_payload('', 30)['rows']] == [
        '100001', '100000', '100002']


def test_projection_does_not_leak_preferred_or_excluded_company(source):
    source.put('universe', {'rows': [
        {'code': '005930', 'name': '삼성전자', 'market': 'KOSPI', 'price': 100,
         'value': 10, 'as_of': '2026-10-08', 'source_trace': {}},
        {'code': '005935', 'name': '삼성전자우', 'market': 'KOSPI', 'price': 80,
         'value': 9, 'as_of': '2026-10-08', 'source_trace': {}},
        {'code': '999999', 'name': '삼성 제외', 'market': 'KONEX', 'value': 999,
         'as_of': '2026-10-08', 'source_trace': {}},
    ]})
    result = market.search_payload('삼성', 30)
    assert result['total'] == 2
    assert [(r['code'], r['price']) for r in result['rows']] == [('005930', 100), ('005935', 80)]


def test_selected_inputs_reject_stale_projection_and_limit(source):
    source.put('enriched', {'100000': {'price': 10}, '100001': {'price': 20}})
    rebuild(source)
    source.put('enriched', {'100000': {'price': 30}})
    details, quotes = selected_inputs(source, ['100000', '100001'])
    assert details == {'100000': {'price': 30}, '100001': {}}
    assert quotes == {'100000': {}, '100001': {}}
    with pytest.raises(ValueError, match='30'):
        selected_inputs(source, [str(100000 + index) for index in range(31)])


def test_selected_enrichment_ignores_other_companies_but_not_legacy_writes(source, monkeypatch):
    source.put('enriched', {'100000': {'per': 10}, '100001': {'per': 20}})
    rebuild(source)
    first = market.security_row(source, '100000')
    head = source.revisions(('enriched-item:100000',))
    publish(source, {'code': '100001', 'facts': {'per': {'value': 21}}})
    assert source.revisions(('enriched-item:100000',)) == head
    original = market.assemble_rows

    def forbidden(*args, **kwargs):
        pytest.fail('Another company caused selected-company reconstruction')

    monkeypatch.setattr(market, 'assemble_rows', forbidden)
    assert market.security_row(source, '100000') is first
    monkeypatch.setattr(market, 'assemble_rows', original)
    source.put('enriched', {'100000': {'per': 11}})
    assert market.security_row(source, '100000')['per'] == 11
    rebuild(source)
    source.put('enriched', {})
    assert 'per' not in market.security_row(source, '100000')


def test_warm_projection_revision_uses_one_payload_free_query(source):
    source.put('enriched', {'100000': {'per': 10}})
    rebuild(source)
    keys = ('universe', 'enriched-item:100000', 'quote:100000')
    expected = source.revisions(keys)
    statements = []

    def sql(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(source.engine, 'before_cursor_execute', sql)
    try:
        assert source.revisions(keys) == expected
    finally:
        event.remove(source.engine, 'before_cursor_execute', sql)
    assert len(statements) == 1
    assert 'payload' not in statements[0]


def test_missing_enrichment_item_is_stable_until_added(source):
    source.put('enriched', {'100001': {'per': 20}})
    rebuild(source)
    key = ('enriched-item:100000',)
    missing = source.revisions(key)
    publish(source, {'code': '100001', 'facts': {'per': {'value': 21}}})
    assert source.revisions(key) == missing
    publish(source, {'code': '100000', 'facts': {'per': {'value': 10}}})
    assert source.revisions(key) != missing
