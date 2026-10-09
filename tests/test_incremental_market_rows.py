"""Fixture-only tests for revision-safe reuse of unchanged market rows."""
import copy
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from services.market_dashboard import app as market
from services.market_dashboard.enriched_view import rebuild
from services.market_dashboard.models import Query, apply_query
from services.market_dashboard.quote_hub import QuoteHub
from services.market_dashboard.store import Store


def row(code, price=100, value=1000, name='fixture_non_production'):
    return {'code': code, 'name': name, 'market': 'KOSPI', 'price': price,
            'value': value, 'volume': 10, 'as_of': '2026-10-02',
            'sector': 'fixture_non_production', 'source_trace': {'source': 'fixture'}}


def quote(price=120):
    return {'price': price, 'as_of': '2026-10-02T15:30:00+09:00',
            'quote_trace': {'source': 'toss', 'fixture_non_production': True}}


@pytest.fixture
def store(tmp_path, monkeypatch):
    source = Store(root=tmp_path)
    source.put('universe', {'rows': [row('005930', name='삼성전자'),
                                   row('000660', 200, 2000, name='SK하이닉스'),
                                   row('005935', 50, None, name='삼성전자우')],
                            'received_at': '2026-10-02T16:00:00+09:00'})
    source.put('enriched', {'005930': {'per': 10}, '000660': {'per': 20}})
    source.put('quotes', {'005930': quote()})
    monkeypatch.setattr(market, 'get_store', lambda: source)
    monkeypatch.setattr(market, '_rows_cache', {})
    monkeypatch.setattr(market, '_security_rows', market.OrderedDict())
    monkeypatch.setattr(market, '_security_universe_cache', None)
    yield source
    source.engine.dispose()


@pytest.mark.parametrize('projection', [False, True])
def test_projection_reuses_unchanged_rows_but_legacy_inputs_rebuild(store, monkeypatch, projection):
    if projection:
        rebuild(store)
    old_rows, _ = market.rows_with_details(check_revision=True)
    old = {r['code']: r for r in old_rows}
    # A separate writer replaces stored observations, as the collector process does.
    writer = Store(root=store.root)
    try:
        values = writer.get('enriched')
        values['000660']['per'] = 22
        writer.put('enriched', values)
        if projection:
            rebuild(writer)
    finally:
        writer.engine.dispose()
    expected = market.assemble_rows(store)
    assembled = []
    original = market.select_daily_measures

    def counted(combined, candidates):
        assembled.append(combined['code'])
        return original(combined, candidates)

    monkeypatch.setattr(market, 'select_daily_measures', counted)
    actual = market.rows_with_details(check_revision=True)
    assert actual == expected
    assert assembled == (['000660'] if projection else ['005930', '000660', '005935'])
    if projection:
        assert actual[0][0] is old['005930']
        assert actual[0][2] is old['005935']
    assert actual[0][1]['per'] == 22 and old['000660']['per'] == 20


def test_input_dates_metadata_zero_missing_and_deleted_rows_match_full_rebuild(store):
    market.rows_with_details(check_revision=True)
    market.security_row(store, '005930')
    changes = [
        ('quotes', {'005930': {**quote(0), 'daily_facts': {
            'volume': {'value': 0, 'period': '2026-10-02', 'unit': '주'}}}}),
        ('quotes', {'005930': {**quote(None), 'as_of': '2026-10-03T15:30:00+09:00'}}),
        ('quotes', {'005930': {**quote(), 'quote_trace': {'source': 'toss', 'revision': 'new'}}}),
        ('enriched', {'005930': {
            'per': None, 'facts': {'per': {'value': None, 'period': '2025'}}}}),
        ('enriched', {}),
        ('quotes', {}),
        ('universe', {'rows': [row('005930', 77)], 'received_at': '2026-10-03'}),
    ]
    for key, payload in changes:
        store.put(key, payload)
        expected = market.assemble_rows(store)
        assert market.rows_with_details(check_revision=True) == expected
        assert market.security_row(store, '005930') == expected[0][0]
    with store.engine.begin() as db:
        db.execute(store.snapshots.delete().where(store.snapshots.c.key == 'quotes'))
    assert market.security_row(store, '005930')['price'] == 77
    store.put('universe', {'rows': []})
    assert market.rows_with_details(check_revision=True)[0] == []
    with pytest.raises(market.HTTPException) as error:
        market.security_row(store, '005930')
    assert error.value.status_code == 404


@pytest.mark.parametrize('key', ['universe', 'enriched', 'quotes'])
@pytest.mark.parametrize('projection', [False, True])
def test_get_edit_put_cannot_mutate_previous_selected_input(store, key, projection):
    # Keep the same Store instance: this is a supported legacy write contract,
    # even though production collection normally uses a separate process.
    if projection:
        rebuild(store)
    first = market.security_row(store, '005930')
    market.rows_with_details(check_revision=True)
    payload = store.get(key)
    if key == 'universe':
        payload['rows'][0]['name'] = 'updated fixture_non_production'
    elif key == 'quotes':
        payload['005930']['price'] = 333
    else:
        payload['005930']['per'] = 33
    store.put(key, payload)
    expected = market.assemble_rows(store)
    actual = market.security_row(store, '005930')
    assert actual == expected[0][0] and actual != first
    assert market.rows_with_details(check_revision=True) == expected


@pytest.mark.parametrize('initial_projection', [False, True])
def test_projection_activation_or_invalidation_cannot_reuse_legacy_aliases(
    store, monkeypatch, initial_projection,
):
    if initial_projection:
        rebuild(store)
    market.rows_with_details(check_revision=True)
    payload = store.get('enriched')
    payload['005930']['per'] = 44
    store.put('enriched', payload)
    if not initial_projection:
        rebuild(store)
    expected = market.assemble_rows(store)
    calls = []
    original = market.select_daily_measures

    def counted(combined, candidates):
        calls.append(combined['code'])
        return original(combined, candidates)

    monkeypatch.setattr(market, 'select_daily_measures', counted)
    assert market.rows_with_details(check_revision=True) == expected
    assert calls == ['005930', '000660', '005935']


def test_selected_and_static_rows_keep_original_values_and_independent_quotes(store):
    first = market.security_row(store, '005930')
    static = market.security_row(store, '005930', include_quote=False)
    store.put('enriched', {'005930': {'per': 10}, '000660': {'per': 999}})
    assert market.security_row(store, '005930') == first
    store.put('quotes', {'005930': quote(130)})
    assert market.security_row(store, '005930')['price'] == 130
    assert market.security_row(store, '005930', include_quote=False) == static
    assert static['price'] == 100


def test_rank_changes_ties_filters_aliases_and_initials_match_full_query(store):
    market.rows_with_details(check_revision=True)
    source = store.get('universe')
    replacement = copy.deepcopy(source)
    replacement['rows'][0]['value'] = 2000
    replacement['rows'][0]['aliases'] = ['반도체 대표']
    replacement['rows'][0]['market'] = 'KOSDAQ'
    store.put('universe', replacement)
    rows, _ = market.rows_with_details(check_revision=True)
    expected, _ = market.assemble_rows(store)
    queries = [Query(), Query(direction='asc'), Query(q='ㅅㅅ'), Query(q='반도체'),
               Query(market='KOSDAQ'), Query(rules=[{'metric': 'price', 'value': 100}]),
               Query(any_rules=[{'metric': 'per', 'value': 15},
                                {'metric': 'price', 'operator': 'lte', 'value': 60}])]
    for query in queries:
        assert apply_query(rows, query) == apply_query(expected, query)
    assert [r['code'] for r in apply_query(rows, Query())['rows']] == ['005930', '000660', '005935']


def test_current_read_consumers_do_not_mutate_retained_input_or_combined_rows(store):
    inputs = tuple(store.get(key) for key in ('universe', 'enriched', 'quotes'))
    original_inputs = copy.deepcopy(inputs)
    rows, _ = market.rows_with_details(check_revision=True)
    originals = copy.deepcopy(rows)
    market.search_payload('삼성', 12)
    market.screener(Query())
    market.export_screener(Query())
    market.sectors()
    market.security_peers('005930')
    market.detail_snapshot(store, '005930', view='summary')
    market.detail_snapshot(store, '005930', view='full')
    market.compare('005930,000660')
    hub = QuoteHub(market.rows_with_details)
    hub.publish(rows)
    assert rows == originals and inputs == original_inputs


def test_late_old_revision_read_cannot_keep_new_value_hidden(store, monkeypatch):
    from services.market_dashboard import enriched_view
    market.security_row(store, '005930')
    old_heads = store.revisions(('universe', 'enriched', 'quotes'))
    # Force an older, already-running read to finish after the newer one.
    store.put('enriched', {'005930': {'per': 11}})
    delayed_heads = store.revisions(('universe', 'enriched', 'quotes'))
    entered, release = threading.Event(), threading.Event()
    original = enriched_view.read_item

    def delayed_read(source, code):
        result = original(source, code)
        if threading.current_thread().name.startswith('old_reader'):
            entered.set()
            assert release.wait(5)
        return result

    monkeypatch.setattr(enriched_view, 'read_item', delayed_read)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='old_reader') as pool:
        delayed = pool.submit(market.security_row, store, '005930', revisions=delayed_heads)
        try:
            assert entered.wait(3)
            store.put('enriched', {'005930': {'per': 12}})
            assert market.security_row(store, '005930')['per'] == 12
        finally:
            release.set()
        assert delayed.result(timeout=3)['per'] == 11
    assert old_heads != delayed_heads
    assert market.security_row(store, '005930')['per'] == 12
    assert market.security_row(store, '005930')['price'] == 120


def test_row_reuse_is_store_isolated_and_keeps_bounded_selected_cache(store, tmp_path):
    first = market.security_row(store, '005930')
    other = Store(root=tmp_path / 'another')
    try:
        codes = [f'{100000 + i:06d}' for i in range(260)]
        other.put('universe', {'rows': [row(code) for code in codes] + [row('005930', 777)]})
        assert market.security_row(other, '005930')['price'] == 777
        assert market.security_row(store, '005930') is first
        for code in codes:
            market.security_row(other, code)
        assert len(market._security_rows) == 256
        assert market.security_row(store, '005930') == first
    finally:
        other.engine.dispose()
