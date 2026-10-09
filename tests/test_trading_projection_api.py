"""Fixture-only API contracts for bounded trading reads and isolated failures."""
import asyncio

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

from services.market_dashboard import app as market
from services.market_dashboard.response_cache import RevisionResponseCache
from services.market_dashboard.store import Store
from services.market_dashboard.time_series import put_series
from services.market_dashboard.toss import TossCollector
from services.market_dashboard.trading_projection import TradingProjectionUnavailable


@pytest.fixture
def store(tmp_path, monkeypatch):
    value = Store('sqlite://', tmp_path)
    value.put('universe', {'rows': [
        {'code': '005930', 'name': 'fixture_non_production', 'market': 'KOSPI',
         'price': 100, 'as_of': '2026-10-08', 'source_trace': {}}
    ]})
    monkeypatch.setattr(market, 'get_store', lambda: value)
    monkeypatch.setattr(market, '_detail_responses', RevisionResponseCache())
    market._rows_cache.clear()
    yield value
    value.engine.dispose()


def source(store, endpoint, rows):
    put_series(store, f'toss:{endpoint}:005930', {
        'rows': [{**row, 'source_trace': {'source': 'fixture_non_production'}} for row in rows],
        'status': 'ready',
    }, reverse=True)
    TossCollector(store, object())._project_trading('005930', endpoint, {'status': 'ready'})


@pytest.mark.parametrize('route', ['financing', 'short-selling'])
def test_period_routes_do_not_assemble_market_or_wait_for_collection(store, monkeypatch, route):
    store.put('financing:005930', {'code': '005930', 'credit': {
        'status': 'ready', 'rows': [{'date': '2026-10-08', 'balance': 0}]
    }})
    store.put('short-selling:005930', {'code': '005930', 'balance': {
        'status': 'stale', 'rows': [{'date': '2026-10-06', 'volume': 123}]
    }})
    def no_market(*args, **kwargs):
        pytest.fail('A symbol section must not reassemble every listed security')
    monkeypatch.setattr(market, 'rows_with_details', no_market)
    calls = []
    monkeypatch.setattr(store, 'request', lambda code: calls.append(code))
    tasks = BackgroundTasks()
    reader = market.financing if route == 'financing' else market.short_selling
    payload = reader('005930', tasks, period='month')
    assert payload['code'] == '005930'
    assert calls == []
    asyncio.run(tasks())
    assert calls == ['005930']


def test_refresh_failure_preserves_read_result_and_other_jobs(store, monkeypatch):
    from services.market_dashboard import toss_backfill
    store.put('financing:005930', {'code': '005930', 'credit': {
        'status': 'ready', 'rows': [{'date': '2026-10-08', 'balance': 0}]
    }})
    jobs = []
    def broken(_code):
        raise OSError('fixture DB intent failure')
    monkeypatch.setattr(store, 'request', broken)
    monkeypatch.setattr(toss_backfill, 'request_history', lambda _s, _c, kind: jobs.append(kind))
    tasks = BackgroundTasks()
    result = market.financing('005930', tasks)
    asyncio.run(tasks())
    assert result['credit']['rows'][0]['balance'] == 0
    assert jobs == ['credit-trades', 'securities-lending']


def test_section_http_contract_keeps_bounds_missing_zero_and_provenance_private(store):
    source(store, 'credit-trades', [
        {'date': '2024-02-28', 'marginLoan': {'balanceQuantity': 15}},
        {'date': '2024-02-29', 'marginLoan': {'balanceQuantity': 0}},
        {'date': '2024-03-31', 'marginLoan': {'balanceQuantity': 20},
         'stockLoan': {'balanceQuantity': None}},
    ])
    with TestClient(market.app) as client:
        path = market.BASE + '/securities/005930/financing'
        response = client.get(path + '?period=month')
        assert response.status_code == 200
        result = response.json()
        assert [r['date'] for r in result['credit']['rows']] == ['2024-02-29', '2024-03-31']
        assert result['credit']['rows'][0]['balance'] == 0
        assert result['stock_loan']['rows'][0]['balance'] is None
        assert 'source_trace' not in response.text
        assert client.get(path + '?period=bad').status_code == 422
        assert client.get(path.replace('005930', '999999')).status_code == 404


def test_activity_validator_tracks_source_correction_without_legacy_reprojection(store):
    # Provider identity is part of the contract; these values remain fixtures.
    put_series(store, 'toss:investor-trading:005930', {'status': 'ready', 'rows': [
        {'date': '2026-10-08', 'individual': {'netBuyVolume': 10},
         'source_trace': {'source': 'toss', 'fixture_non_production': True}}
    ]}, reverse=True)
    TossCollector(store, object())._project_trading('005930', 'investor-trading', {'status': 'ready'})
    from services.market_dashboard.trading_projection import compact_projection
    compact_projection(store, '005930', 'investor-trading', {'status': 'ready'})
    with TestClient(market.app) as client:
        path = market.BASE + '/securities/005930?view=activity'
        first = client.get(path)
        assert first.json()['detail']['flows'][0]['individual'] == 10
        put_series(store, 'toss:investor-trading:005930', {'status': 'ready', 'rows': [
            {'date': '2026-10-08', 'individual': {'netBuyVolume': 11},
             'source_trace': {'source': 'toss', 'fixture_non_production': True}}
        ]}, reverse=True)
        updated = client.get(path, headers={'If-None-Match': first.headers['etag']})
        assert updated.status_code == 200
        assert updated.json()['detail']['flows'][0]['individual'] == 11
        assert updated.headers['etag'] != first.headers['etag']


def test_trading_source_failure_is_isolated_from_price(store, monkeypatch):
    def broken(*args, **kwargs):
        raise TradingProjectionUnavailable('fixture missing source')
    monkeypatch.setattr(market, 'read_flows', broken)
    monkeypatch.setattr(market, 'read_financing', broken)
    with TestClient(market.app) as client:
        path = market.BASE + '/securities/005930'
        whole = client.get(path)
        assert whole.status_code == 200
        assert whole.json()['security']['price'] == 100
        assert whole.json()['detail']['flows_status'] == 'error'
        assert whole.json()['detail']['flows'] == []
        failure = client.get(path + '/financing')
        assert failure.status_code == 503
        assert 'fixture' not in failure.text
        assert client.get(path + '?view=summary').status_code == 200


def naver_source(store, quantity=99):
    from services.market_dashboard.providers import NAVER_FLOW_KIND, NAVER_FLOW_SCOPE
    put_series(store, 'naver:investor-trading:005930', {
        'code': '005930', 'aggregation': NAVER_FLOW_KIND, 'source': 'naver',
        'scope': NAVER_FLOW_SCOPE, 'venue': 'PROVIDER', 'status': 'ready',
        'rows': [{'date': '2026-10-08', 'foreign': quantity, 'individual': -quantity,
                  'institution': 0, 'holding': None, 'source_trace': {
                      'source': 'naver', 'fixture_non_production': True,
                      'scope': NAVER_FLOW_SCOPE, 'period': '2026-10-08', 'unit': '주',
                      'source_document_id': 'a' * 64,
                      'source_url': 'https://m.stock.naver.com/api/stock/005930/integration'}}]
    }, reverse=True)


def test_separate_fallback_is_identified_and_invalidates_activity_validator(store):
    naver_source(store)
    with TestClient(market.app) as client:
        path = market.BASE + '/securities/005930?view=activity'
        first = client.get(path)
        detail = first.json()['detail']
        assert detail['flows'][0]['foreign'] == 99
        assert detail['flows_source'] == 'naver'
        assert '거래시장 구분 미제공' in detail['flows_scope']
        naver_source(store, 100)
        changed = client.get(path, headers={'If-None-Match': first.headers['etag']})
        assert changed.status_code == 200
        assert changed.json()['detail']['flows'][0]['foreign'] == 100


def test_naver_fallback_never_replaces_or_extends_toss_rows(store):
    naver_source(store)
    toss = {'date': '2026-10-07', 'foreign': 11, 'source_trace': {'source': 'toss'}}
    store.put('toss-normalized:flows:005930', {'rows': [toss]})
    result = market.detail_snapshot(store, '005930', 'activity')
    assert result['detail']['flows'] == [toss]
    assert 'flows_source' not in result['detail']


def test_pure_legacy_naver_history_also_reads_new_separate_observations(store):
    naver_source(store)
    old = store.time_series.rows('naver:investor-trading:005930')[0]
    old['date'] = '2010-01-01'
    old['source_trace']['period'] = old['date']
    store.put('detail:005930', {'flows': [old]})
    result = market.detail_snapshot(store, '005930', 'activity')
    assert [row['date'] for row in result['detail']['flows']] == ['2026-10-08', '2010-01-01']
    assert result['detail']['flows_source'] == 'naver'


def test_corrupt_toss_reference_never_silently_switches_to_naver(store, monkeypatch):
    naver_source(store)
    def broken(*_args):
        raise TradingProjectionUnavailable('fixture broken Toss reference')
    monkeypatch.setattr(market, 'read_flows', broken)
    result = market.detail_snapshot(store, '005930', 'full')
    assert result['detail']['flows'] == []
    assert result['detail']['flows_status'] == 'error'
    assert result['security']['price'] == 100
