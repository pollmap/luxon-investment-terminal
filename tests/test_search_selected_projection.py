"""Search compatibility and bounded hydration using fixture-only stored evidence."""

import importlib
import asyncio

import httpx

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
    # Broad matched names rank their bounded candidate set before result hydration.
    assert len(selections) == (2 if query in ('삼성', 'ㅅㅅ') else 1 if expected['rows'] else 0)
    assert sizes[0] <= 30


def test_turnover_update_can_promote_a_security_outside_initial_limit(source):
    assert market.search_payload('삼성', 1)['rows'][0]['code'] == '100000'
    source.update_quote('100079', {'price': 179, 'as_of': '2026-10-08',
        'quote_trace': {'source': 'toss'},
        'daily_facts': {'value': {'value': 9999, 'period': '2026-10-08'}}})
    assert market.search_payload('삼성', 1)['rows'][0]['code'] == '100079'


def test_narrow_identity_search_avoids_market_ranking_projection(source, monkeypatch):
    source.put('enriched', {'100079': {'aliases': ['정밀별칭']}})
    def unexpected(*_args, **_kwargs):
        raise AssertionError('A narrow identity search must not prepare market-wide ranking')
    monkeypatch.setattr(search_view, 'candidates', unexpected)
    assert market.search_payload('정밀별칭', 1)['rows'][0]['code'] == '100079'
    assert market.search_payload('100001', 12)['rows'][0]['code'] == '100001'


def test_unrelated_quote_change_reuses_narrow_response_but_new_matching_alias_invalidates(source, monkeypatch):
    from services.market_dashboard.response_cache import InFlightResponses, RevisionReadBatcher, RevisionResponseCache
    monkeypatch.setattr(market, '_search_responses', RevisionResponseCache())
    monkeypatch.setattr(market, '_search_flights', InFlightResponses())
    monkeypatch.setattr(market, '_revision_reads', RevisionReadBatcher())
    source.put('enriched', {'100000': {'aliases': ['연결별칭']}})
    original, calls = market.search_payload, []
    def counted(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(market, 'search_payload', counted)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=market.app), base_url='http://fixture') as client:
            url=market.BASE+'/search?q=연결별칭'
            first=await client.get(url)
            source.update_quote('100079', {'price':999,'as_of':'2026-10-08','quote_trace':{'source':'toss'}})
            second=await client.get(url)
            assert first.content==second.content and len(calls)==1
            source.update_quote('100079', {'aliases':['연결별칭']})
            third=await client.get(url)
            assert third.json()['total']==2 and len(calls)==2
            source.update_quote('100000', {'price':300,'as_of':'2026-10-08','quote_trace':{'source':'toss'}})
            fourth=await client.get(url)
            selected={row['code']:row for row in fourth.json()['rows']}
            assert selected['100000']['price']==300 and len(calls)==3
    asyncio.run(scenario())


def test_broad_identity_matches_rank_only_candidates_and_hydrate_at_most_thirty(source, monkeypatch):
    universe = source.get('universe')
    universe['rows'] += [{'code':str(200000+i),'name':f'다른기업 fixture {i}', 'market':'KOSPI',
                         'value':1,'as_of':'2026-10-08','source_trace':{'source':'fixture_non_production'}}
                        for i in range(200)]
    source.put('universe', universe)
    # Both identity fields and selected-source versions must remain compatible.
    source.update_quote('100079', {'price':179,'as_of':'2026-10-08','quote_trace':{'source':'toss'},
                                   'daily_facts':{'value':{'value':9999,'period':'2026-10-08'}}})
    def forbidden(*args, **kwargs):
        raise AssertionError('An 80-match query must not prepare the entire ranking map')
    monkeypatch.setattr(search_view, 'candidates', forbidden)
    decoded, original = [], search_view._decode
    def capture(records, sqlite, paths=search_view.PATHS):
        decoded.append((len(records), paths))
        return original(records, sqlite, paths)
    monkeypatch.setattr(search_view, '_decode', capture)
    actual = market.search_payload('삼성', 1)
    assert actual['total'] == 80 and actual['rows'][0]['code'] == '100079'
    assert [count for count, paths in decoded if paths == search_view.PATHS] == [80, 80]
    assert [count for count, paths in decoded if paths == search_view.COMPACT_PATHS] == [1, 1]


def test_enrichment_of_other_company_reuses_candidate_response_but_selected_facts_invalidate(source, monkeypatch):
    from services.market_dashboard.response_cache import InFlightResponses, RevisionReadBatcher, RevisionResponseCache
    source.put('enriched', {'100000':{'aliases':['분리별칭']},'100001':{'facts':{'per':{'value':1}}}})
    rebuild(source)
    monkeypatch.setattr(market, '_search_responses', RevisionResponseCache())
    monkeypatch.setattr(market, '_search_flights', InFlightResponses())
    monkeypatch.setattr(market, '_revision_reads', RevisionReadBatcher())
    original, calls = market.search_payload, []
    def counted(*args, **kwargs):
        calls.append(args[0]);return original(*args, **kwargs)
    monkeypatch.setattr(market, 'search_payload', counted)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=market.app),base_url='http://fixture') as client:
            url=market.BASE+'/search?q=분리별칭'
            first=await client.get(url)
            publish(source, {'code':'100001','facts':{'per':{'value':2}}})
            second=await client.get(url)
            assert first.content == second.content and len(calls) == 1
            publish(source, {'code':'100000','facts':{'price':{'value':200}}})
            third=await client.get(url)
            assert third.json()['rows'][0]['price'] == 200 and len(calls) == 2
    asyncio.run(scenario())


def test_identity_burst_over_thirty_reads_only_changed_fields_without_full_projection(source):
    source.update_quotes({str(100000+i):{'price':100+i,'as_of':'2026-10-08T09:00:00+09:00','quote_trace':{'source':'toss'}}
                          for i in range(80)})
    market.search_payload('별칭79', 1)
    source.update_quotes({str(100000+i):{'price':200+i,'as_of':'2026-10-08T09:01:00+09:00','quote_trace':{'source':'toss'}}
                          for i in range(40)})
    statements=[]
    def sql(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)
    event.listen(source.engine,'before_cursor_execute',sql)
    try:
        result=market.search_payload('별칭79',1)
    finally:
        event.remove(source.engine,'before_cursor_execute',sql)
    assert result['rows'][0]['code'] == '100079'
    assert not any('json_each(s.payload)' in statement for statement in statements)


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
    assert not any('json_each(s.payload)' in statement for statement in statements)
    expected = apply_query(market.rows_with_details(check_revision=True)[0],
                           Query(q='삼성', limit=30))
    expected['rows'] = [compact(row) for row in expected['rows']]
    assert result == expected
    assert market.search_payload('과거별칭', 30)['total'] == 0
    assert market.search_payload('새로운별칭', 30)['rows'][0]['price'] == 180


def test_summary_projection_preserves_first_screen_without_decoding_company_history(source, monkeypatch):
    source.put('enriched', {'100000': {'company':{'products':['원문제품'],'sector':'원문산업',
        'listed_at':'2000-01-01','unused_profile':['fixture_non_production']*2000},
        'per':10,'cap':999,'facts':{'per':{'value':10,'period':'2026-10-08','unit':'배'}},
        'unused_financial_history':['fixture_non_production']*2000}})
    rebuild(source)
    source.put('detail:100000',{'company':{'description':'원문 설명'},'financials':['fixture']*2000})
    source.update_quote('100000',{'price':201,'change':2,'as_of':'2026-10-08T09:00:00+09:00',
                                'quote_trace':{'source':'toss','fixture_non_production':True}})
    expected=market.detail_snapshot(source,'100000','summary',row=market.security_row(source,'100000'))
    import services.market_dashboard.enriched_view as enrichment
    def forbidden(*args,**kwargs):
        raise AssertionError('Summary decoded a full company/quote payload')
    monkeypatch.setattr(enrichment,'read_item',forbidden)
    monkeypatch.setattr(source,'get_quote',forbidden)
    heads=source.revisions(('universe','enriched-item:100000','quote:100000'))
    row=market.summary_row(source,'100000',heads)
    assert 'unused_financial_history' not in row and 'unused_profile' not in row['company']
    assert market.detail_snapshot(source,'100000','summary',row=row)==expected
    # Updating price still uses the narrow read; another company cannot evict it.
    publish(source,{'code':'100001','facts':{'per':{'value':99}}})
    heads=source.revisions(('universe','enriched-item:100000','quote:100000'))
    assert market.summary_row(source,'100000',heads) is row
    source.update_quote('100000',{'price':202,'as_of':'2026-10-08T10:00:00+09:00',
                                'quote_trace':{'source':'toss','fixture_non_production':True}})
    heads=source.revisions(('universe','enriched-item:100000','quote:100000'))
    assert market.summary_row(source,'100000',heads)['price']==202


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


@pytest.mark.parametrize('quote_kind', ['broker_old', 'public_stale', 'partial', 'zero_null'])
def test_compact_hydration_preserves_response_and_nested_evidence(source, quote_kind):
    trace = {'source': 'fixture_non_production', 'source_ref': 'original-reference',
             'nested': {'labels': ['원본', None, True], 'quality': False}}
    detail = {'name': '삼성 fixture enriched', 'aliases': ['검증별칭'], 'market': 'KOSPI',
              'sector': None, 'logo_url': None, 'market_status': 'closed',
              'source_trace': trace, 'unneeded_financial_history': ['fixture'] * 10000,
              'facts': {'price': None, 'change': {'value': 0, 'source_trace': trace},
                        'value': {'value': 9999, 'period': '2026-10-08', 'source_trace': trace},
                        'unneeded_history': {'rows': ['fixture'] * 10000}}}
    source.put('enriched', {'100000': detail})
    rebuild(source)
    quote = {'price': 201, 'change': 2, 'as_of': '2026-10-08',
             'received_at': 'fixture-received', 'market_status': 'closed',
             'quote_trace': {**trace, 'source': 'toss'},
             'unneeded_trades': ['fixture'] * 10000,
             'daily_facts': {'volume': {'value': 123, 'period': '2026-10-08',
                                       'source_trace': trace},
                             'value': {'value': 7777, 'period': '2026-10-08',
                                       'source_trace': trace}}}
    if quote_kind in ('broker_old', 'public_stale'):
        quote['as_of'] = '2026-10-07'
    if quote_kind == 'public_stale':
        quote['quote_trace']['source'] = 'fixture_non_production'
    if quote_kind == 'partial':
        quote.pop('price')
    if quote_kind == 'zero_null':
        quote.update(price=0, change=None, source_trace=None)
    source.put('quotes', {'100000': quote})
    expected = apply_query(market.rows_with_details(check_revision=True)[0],
                           Query(q='검증별칭', limit=30))
    expected['rows'] = [compact(row) for row in expected['rows']]
    actual = market.search_payload('검증별칭', 30)
    assert actual == expected
    all_details, all_quotes = selected_inputs(source, ['100000'])
    details, quotes = selected_inputs(source, ['100000'], compact=True)
    assert 'unneeded_financial_history' in all_details['100000']
    assert 'unneeded_trades' in all_quotes['100000']
    assert 'unneeded_financial_history' not in details['100000']
    assert 'unneeded_trades' not in quotes['100000']
    assert 'unneeded_history' not in details['100000']['facts']
    assert details['100000']['facts']['price'] is None
    assert details['100000']['source_trace'] == trace
    assert quotes['100000']['quote_trace'] == quote['quote_trace']
    assert details['100000']['sector'] is None
    assert 'received_at' not in details['100000']
    assert ('source_trace' in quotes['100000']) == ('source_trace' in quote)
    if quote_kind == 'zero_null':
        assert quotes['100000']['source_trace'] is None
    import json
    original_bytes = len(json.dumps((all_details, all_quotes), ensure_ascii=False))
    compact_bytes = len(json.dumps((details, quotes), ensure_ascii=False))
    assert compact_bytes < original_bytes / 20


@pytest.mark.parametrize('value', [None, False, [], {}])
def test_compact_hydration_preserves_container_null_and_type(source, value):
    # Preserve malformed/null input too; projection must not silently turn an
    # invalid source into a different valid value before existing validation.
    source.put('enriched', {'100000': {'facts': value}})
    source.put('quotes', {'100000': {'daily_facts': value}})
    details, quotes = selected_inputs(source, ['100000', '100001'], compact=True)
    assert details['100000'] == {'facts': value}
    assert quotes['100000'] == {'daily_facts': value}
    assert details['100001'] == quotes['100001'] == {}


def test_postgres_compact_paths_bind_as_text_arrays(source, monkeypatch):
    from types import SimpleNamespace

    from sqlalchemy import Text
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.dialects.postgresql import ARRAY

    observed = []
    store = SimpleNamespace(engine=SimpleNamespace(dialect=postgresql.dialect()),
                            snapshots=source.snapshots)

    def capture(_store, statement, params=None):
        compiled = statement.compile(dialect=store.engine.dialect)
        observed.append(compiled)
        return []

    monkeypatch.setattr(search_view, '_records', capture)
    assert selected_inputs(store, ['100000'], compact=True) == ({}, {})
    compiled = observed[0]
    path_binds = [compiled.binds[name] for name in compiled.params
                  if isinstance(compiled.binds[name].type, ARRAY)]
    assert len(path_binds) == 1  # One typed code array; JSON paths are fixed internal literals.
    assert all(isinstance(bind.type.item_type, Text) for bind in path_binds)
    sql = str(compiled)
    assert '100000' not in sql and 'unnest(CAST(' in sql
    assert sql.count('#>') >= 2 * len(search_view.COMPACT_PATHS)
    assert all(f'%({name})s::TEXT[]' in sql for name in compiled.params
               if isinstance(compiled.binds[name].type, ARRAY))
    assert 'search_selected AS MATERIALIZED' in str(compiled)


def test_cached_identity_generation_does_not_wait_for_another_source_query(source, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    source.update_quote('100000', {'aliases': ['처음별칭']})
    heads = source.revisions(('enriched', 'quotes'))
    enrichment = search_view._light_map(source, 'enriched', heads['enriched'], search_view.IDENTITY_PATHS)
    search_view._light_map(source, 'quotes', heads['quotes'], search_view.IDENTITY_PATHS)
    source.update_quote('100000', {'aliases': ['다음별칭']})
    current = source.revisions(('quotes',))['quotes']
    entered, release, answered = threading.Event(), threading.Event(), threading.Event()
    original = search_view._quote_delta

    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    def cached_read():
        answer = search_view._light_map(source, 'enriched', heads['enriched'], search_view.IDENTITY_PATHS)
        assert answer is enrichment
        answered.set()
        return answer

    monkeypatch.setattr(search_view, '_quote_delta', paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        updating = pool.submit(search_view._light_map, source, 'quotes', current, search_view.IDENTITY_PATHS)
        try:
            assert entered.wait(2)
            cached = pool.submit(cached_read)
            # Structural concurrency gate: the exact observed generation must
            # answer while the other source SQL is explicitly still paused.
            assert answered.wait(1), 'Unrelated source SQL held the shared identity cache lock'
            assert cached.result() is enrichment
        finally:
            release.set()
        assert updating.result()['100000']['aliases'] == ['다음별칭']


def test_candidate_dependencies_keep_heads_of_the_matched_identity_generation(source):
    source.update_quote('100000', {'aliases': ['동시별칭']})
    keys = ('universe', 'enriched', 'quotes')
    first = source.revisions(keys)
    universe = source.get('universe')
    observed = {}
    codes = search_view.identity_candidates(source, universe, '동시별칭', first, observed_heads=observed)
    expected = search_view.candidate_dependencies(source, codes, first, observed_heads=observed)
    source.update_quote('100000', {'aliases': ['새로운동시별칭']})
    latest = source.revisions(keys)
    new_observation = {}
    search_view.identity_candidates(source, universe, '동시별칭', latest, observed_heads=new_observation)
    # Replacing the global projection cache must not relabel the already used
    # identity values with this later writer's heads.
    actual = search_view.candidate_dependencies(source, codes, first, observed_heads=observed)
    changed = search_view.candidate_dependencies(source, codes, latest, observed_heads=new_observation)
    assert actual == expected
    assert actual['quote:100000'] != changed['quote:100000']


@pytest.mark.asyncio
async def test_projection_replacement_between_matching_and_dependency_read_cannot_cache_new_heads(source, monkeypatch):
    from services.market_dashboard.response_cache import InFlightResponses, RevisionReadBatcher, RevisionResponseCache
    source.update_quote('100000', {'aliases': ['변경별칭']})
    monkeypatch.setattr(market, '_search_responses', RevisionResponseCache())
    monkeypatch.setattr(market, '_search_flights', InFlightResponses())
    monkeypatch.setattr(market, '_revision_reads', RevisionReadBatcher())
    original, mutated = search_view.identity_candidates, False

    def replace(*args, **kwargs):
        nonlocal mutated
        codes = original(*args, **kwargs)
        if not mutated:
            mutated = True
            source.update_quote('100000', {'aliases': ['변경별칭', '추가별칭']})
            heads = source.revisions(('universe', 'enriched', 'quotes'))
            original(source, args[1], args[2], heads)
        return codes

    monkeypatch.setattr(search_view, 'identity_candidates', replace)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=market.app), base_url='http://fixture') as client:
        first = await client.get(market.BASE + '/search?q=변경별칭')
        assert first.status_code == 200 and first.json()['total'] == 1
        assert not market._search_responses.entries
        second = await client.get(market.BASE + '/search?q=변경별칭')
        assert second.status_code == 200 and second.json()['total'] == 1
        assert len(market._search_responses.entries) == 1


@pytest.mark.parametrize('key', ['quotes', 'enriched'])
def test_postgres_full_identity_projection_materializes_original_entries_once(monkeypatch, key):
    from types import SimpleNamespace
    from sqlalchemy.dialects import postgresql
    store = SimpleNamespace(engine=SimpleNamespace(dialect=SimpleNamespace(name='postgresql')))
    statements = []

    def capture(_store, statement, params):
        statements.append(str(statement.compile(dialect=postgresql.dialect())))
        assert params == {'key': key}
        width = 1 + 2 * len(search_view.IDENTITY_PATHS)
        extra = ['enriched-item:missing'] if key == 'enriched' else []
        return [tuple([None] * width + extra + ['source-head', 'book-head', None])]

    monkeypatch.setattr(search_view, '_records', capture)
    revision, values, heads = search_view._light_snapshot(store, key, search_view.IDENTITY_PATHS)
    assert values == {} and revision
    assert 'entries AS MATERIALIZED (' in statements[0]
    assert 'FROM entries ORDER BY priority' in statements[0]
    assert 'jsonb_each(s.payload::jsonb)' in statements[0]
    assert 'item_revision' in statements[0]
    if key == 'enriched':
        assert heads == {'default-enriched-item': 'enriched-item:missing'}
    else:
        assert heads == {'quotes': 'source-head', 'quote-book': 'book-head'}
