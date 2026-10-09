"""Synthetic provider fixtures; never operational market evidence."""
from copy import deepcopy

import httpx
import pytest

from services.market_dashboard.providers import (
    NAVER_FLOW_SCOPE,
    PublicProvider,
    read_naver_flows_fallback,
    trace,
)
from services.market_dashboard.store import Store


@pytest.fixture
def store(tmp_path):
    value = Store('sqlite://', tmp_path)
    yield value
    value.engine.dispose()


def naver_payload(day='20261008', quantity='99'):
    return {'itemCode': '005930', 'dealTrendInfos': [{
        'bizdate': day, 'foreignerPureBuyQuant': quantity,
        'organPureBuyQuant': '2', 'individualPureBuyQuant': '-101',
        'foreignerHoldRatio': '50.5',
    }], 'totalInfos': [{'code': 'per', 'value': '10'}]}


def legacy_naver(day, quantity=1):
    return {'date': day, 'foreign': quantity, 'institution': 2, 'individual': -3,
            'holding': None, 'source_trace': {
                **trace('naver', 'https://m.stock.naver.com/api/stock/005930/integration',
                        'c' * 64, day, '주', '제공처 일별 수급'),
                'fixture_non_production': True}}


async def refresh(store, payload, *, fundamentals=False, on_fundamentals=None):
    provider = PublicProvider(store)
    async def fetch(url, *_args):
        if 'fnguide' in url:
            if on_fundamentals:
                on_fundamentals()
            return httpx.Response(200, text='<input id="cmp_cd" value="005930">'), 'b' * 64
        return httpx.Response(200, json=payload), 'a' * 64
    provider.fetch = fetch
    try:
        return await provider.detail('005930', include_fundamentals=fundamentals)
    finally:
        await provider.client.aclose()


@pytest.mark.asyncio
async def test_naver_refresh_preserves_toss_flow_values_reference_and_facts(store):
    old = {'code': '005930', 'flows': [
        {'date': '2026-10-08', 'foreign': 11, 'institution': 5, 'individual': -16,
         'holding': 40, 'source_trace': {'source': 'toss', 'scope': '통합'}}
    ], 'facts': {'foreign_net': {'value': 11, 'source_trace': {'source': 'toss'}}},
        'source_status': {'flows': {'status': 'ready', 'source': 'toss'}},
        '_trading_projection': {'format': 'fixture_non_production', 'fields': {'flows': {}}}}
    store.put('detail:005930', old)
    store.put('toss-normalized:flows:005930', {'rows': deepcopy(old['flows'])})
    provider = PublicProvider(store)
    async def fetch(*_args):
        return httpx.Response(200, json=naver_payload()), 'a' * 64
    provider.fetch = fetch
    try:
        result = await provider.detail('005930', include_fundamentals=False)
    finally:
        await provider.client.aclose()
    assert result['flows'] == old['flows']
    assert result['_trading_projection'] == old['_trading_projection']
    assert result['facts']['foreign_net'] == old['facts']['foreign_net']
    assert result['source_status']['flows'] == old['source_status']['flows']
    assert result['facts']['per']['value'] == 10
    assert store.get('toss-normalized:flows:005930')['rows'] == old['flows']
    separate = read_naver_flows_fallback(store, '005930')
    assert separate['rows'][0]['foreign'] == 99
    assert separate['source'] == 'naver' and separate['scope'] == NAVER_FLOW_SCOPE
    assert separate['venue'] == 'PROVIDER' and separate['fallback']


@pytest.mark.asyncio
async def test_pure_naver_legacy_is_preserved_and_remains_explicitly_available(store):
    old = {'code': '005930', 'flows': [legacy_naver('2010-01-01')],
           'flows_received_at': '2010-01-02T00:00:00+00:00'}
    store.put('detail:005930', old)
    assert read_naver_flows_fallback(store, '005930')['rows'] == old['flows']
    await refresh(store, naver_payload())
    result = read_naver_flows_fallback(store, '005930')
    assert [row['date'] for row in result['rows']] == ['2026-10-08', '2010-01-01']
    assert result['rows'][-1] == old['flows'][0]
    assert store.metadata('detail:005930')['flows'] == old['flows']
    assert store.metadata('naver:investor-trading:005930')['row_count'] == 1
    assert result['history_complete'] is False


@pytest.mark.asyncio
async def test_mixed_legacy_is_not_relabelled_or_copied_into_separate_source(store):
    old = [legacy_naver('2026-10-08'), {'date': '2010-01-01', 'foreign': 44,
                                      'source_trace': {'source': 'toss'}}]
    store.put('detail:005930', {'code': '005930', 'flows': old})
    before = read_naver_flows_fallback(store, '005930')
    assert before['status'] == 'legacy_mixed_sources' and before['rows'] == []
    await refresh(store, naver_payload())
    assert store.get('detail:005930')['flows'] == old
    separate = read_naver_flows_fallback(store, '005930')
    assert len(separate['rows']) == 1
    assert separate['legacy_status'] == 'legacy_mixed_sources'


@pytest.mark.asyncio
async def test_empty_naver_window_preserves_history_and_zero_is_an_observation(store):
    await refresh(store, naver_payload('20261007', '0'))
    await refresh(store, naver_payload())
    before = read_naver_flows_fallback(store, '005930')
    await refresh(store, {'itemCode': '005930', 'dealTrendInfos': []})
    after = read_naver_flows_fallback(store, '005930')
    assert after['rows'] == before['rows'] and after['rows'][-1]['foreign'] == 0
    assert after['received_at'] == before['received_at'] and after['status'] == 'stale'
    assert 'flows' not in store.get('detail:005930')


@pytest.mark.asyncio
@pytest.mark.parametrize('initially_present', [True, False])
async def test_toss_update_during_fundamentals_fetch_is_preserved(store, initially_present):
    if initially_present:
        store.put('detail:005930', {'flows': [legacy_naver('2000-01-01')]})
    newer = {'flows': [{'date': '2026-10-09', 'foreign': 88,
                       'source_trace': {'source': 'toss'}}],
             '_trading_projection': {'format': 'newer_fixture'},
             'flows_received_at': 'newer', 'flows_status': 'ready',
             'facts': {'foreign_net': {'value': 88}},
             'source_status': {'flows': {'status': 'ready', 'source': 'toss'}}}
    def racing_toss_writer():
        store.put('detail:005930', newer)
    result = await refresh(store, naver_payload(), fundamentals=True,
                           on_fundamentals=racing_toss_writer)
    for field in ('flows', 'flows_received_at', 'flows_status', '_trading_projection'):
        assert result[field] == newer[field]
    assert result['facts']['foreign_net'] == newer['facts']['foreign_net']
    assert result['source_status']['flows'] == newer['source_status']['flows']
    assert result['facts']['per']['value'] == 10


@pytest.mark.parametrize('damage', ['company', 'unit', 'scope', 'hash', 'period', 'reference'])
def test_legacy_fallback_rejects_unverified_or_conflicting_identity(store, damage):
    row = legacy_naver('2026-10-08')
    evidence = row['source_trace']
    payload = {'flows': [row]}
    if damage == 'company':
        evidence['source_url'] = evidence['source_url'].replace('005930', '000270')
    elif damage == 'hash':
        evidence['source_document_id'] = 'missing'
    elif damage == 'reference':
        payload['_trading_projection'] = {'fields': {'flows': {'source_key': 'toss'}}}
    else:
        evidence[damage] = 'wrong'
    store.put('detail:005930', payload)
    result = read_naver_flows_fallback(store, '005930')
    assert result['rows'] == [] and result['status'] in (
        'legacy_evidence_unverified', 'legacy_reference_conflict')
    assert store.metadata('detail:005930') == payload


@pytest.mark.asyncio
async def test_other_company_response_has_no_writes(store):
    with pytest.raises(ValueError, match='identity mismatch'):
        await refresh(store, {**naver_payload(), 'itemCode': '000270'})
    assert store.metadata('naver:investor-trading:005930') is None
    assert store.metadata('detail:005930') is None


@pytest.mark.asyncio
async def test_corrupt_separate_source_is_not_silently_relabelled(store):
    await refresh(store, naver_payload())
    key = 'naver:investor-trading:005930'
    wrong = {**store.metadata(key), '_series_ref': 'naver:investor-trading:000270'}
    store.put(key, wrong)
    with pytest.raises(ValueError, match='identity mismatch'):
        await refresh(store, naver_payload())
    with pytest.raises(ValueError, match='identity mismatch'):
        read_naver_flows_fallback(store, '005930')
    assert store.metadata(key) == wrong


@pytest.mark.asyncio
async def test_broker_publication_preserves_concurrent_flow_fields(store, monkeypatch):
    from services.market_dashboard import providers
    store.put('detail:005930', {'flows': [legacy_naver('2010-01-01')]})
    current = {'flows': [{'date': '2026-10-09', 'foreign': 88,
                          'source_trace': {'source': 'toss'}}],
               '_trading_projection': {'format': 'fixture_non_production'},
               'facts': {'foreign_net': {'value': 88}}}
    publish = providers._publish_detail_preserving_flows
    def raced(store, code, payload):
        store.put('detail:' + code, current)
        return publish(store, code, payload)
    monkeypatch.setattr(providers, '_publish_detail_preserving_flows', raced)
    provider = PublicProvider(store)
    async def fetch(*_args):
        return httpx.Response(200, json={'traderList': [{
            'itemcode': '005930', 'nameKr': 'fixture', 'buyQuant': '3', 'sellQuant': '2',
            'quant': '1', 'bizdate': '20261009'}]}), 'a' * 64
    provider.fetch = fetch
    try:
        await provider.brokers('005930')
    finally:
        await provider.client.aclose()
    result = store.get('detail:005930')
    assert result['flows'] == current['flows']
    assert result['_trading_projection'] == current['_trading_projection']
    assert result['facts']['foreign_net'] == current['facts']['foreign_net']
    assert result['brokers'][0]['net'] == 1
