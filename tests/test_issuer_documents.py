"""Fixture-only company sharing; security financial facts must remain separate."""
from unittest.mock import AsyncMock
import pytest
from services.market_dashboard.store import Store, now
from services.market_dashboard.issuer_dividends import SYMBOLS
from services.market_dashboard.issuer_documents import document_issuer, project_documents
from services.market_dashboard.authenticated_sources import AuthenticatedSources
from services.market_dashboard.official_views import read_view


def setup(store):
    store.put('dart-corporations',{'codes':{'005380':'fixture_corp'}})
    store.put('universe', {'rows': [{'code': c, 'name': n} for c, (n, _) in SYMBOLS.items()]})
    store.put('official:005380', {'company': {'corp_name': 'fixture issuer'},
        'statements': [{'EPS': 'ordinary only'}], 'source_trace': {'source': 'fixture_non_production'},
        'company_received_at': now()})
    store.put('filings:005380', {'status': 'ready', 'received_at': now(),
        'rows': [{'receipt': 'fixture', 'title': 'fixture filing', 'source_trace': {'source': 'fixture_non_production'}}]})


def test_reviewed_identity_not_suffix_guess_and_native_mapping_wins(tmp_path):
    store = Store('sqlite://', tmp_path); setup(store)
    corps = {'005380': 'fixture_corp'}
    assert document_issuer(store, '005385', corps) == '005380'
    assert document_issuer(store, '005390', corps) is None
    assert document_issuer(store, '005385', {**corps, '005385': 'native'}) == '005385'
    store.put('universe', {'rows': [{'code': '005380', 'name': '현대차'}, {'code': '005385', 'name': 'wrong issuer'}]})
    assert document_issuer(store, '005385', corps) is None


def test_only_documents_and_company_are_shared_and_last_good_data_survives(tmp_path):
    store = Store('sqlite://', tmp_path); setup(store)
    store.put('official:005385', {'statements': [{'EPS': 'preferred independent'}], 'source_trace': {'source': 'own'}})
    project_documents(store, '005385', '005380')
    assert store.get('filings:005385')['rows'] == store.get('filings:005380')['rows']
    official = store.get('official:005385')
    assert official['statements'] == [{'EPS': 'preferred independent'}]
    assert official['source_trace']['source'] == 'own'
    assert official['company_source_trace']['source'] == 'fixture_non_production'
    assert read_view(store, '005385', 'company')['issuer_link']['scope'] == 'company_documents_only'
    assert store.get('events:005385') is None and store.get('prices:005385') is None
    store.put('filings:005380', {'status': 'error', 'rows': []})
    project_documents(store, '005385', '005380')
    assert len(store.get('filings:005385')['rows']) == 1


@pytest.mark.asyncio
async def test_worker_reuses_fresh_company_disclosures_without_dart_calls(tmp_path):
    store = Store('sqlite://', tmp_path); setup(store)
    auth = object.__new__(AuthenticatedSources)
    auth.store = store; auth.dart = 'fixture_non_production'
    auth.corporations = AsyncMock(return_value={'005380': 'fixture_corp'})
    auth._dart_get = AsyncMock(side_effect=RuntimeError('must not request'))
    for code in ('005385', '005387', '005389'):
        await auth.filings(code)
        assert store.get('filings:' + code)['status'] == 'ready'
    auth._dart_get.assert_not_awaited()


@pytest.mark.asyncio
async def test_source_refresh_failure_preserves_projected_documents(tmp_path):
    store = Store('sqlite://', tmp_path); setup(store)
    primary = store.get('filings:005380'); primary['received_at'] = '2020-01-01T00:00:00+00:00'
    store.put('filings:005380', primary)
    auth = object.__new__(AuthenticatedSources)
    auth.store = store; auth.dart = 'fixture_non_production'
    auth.corporations = AsyncMock(return_value={'005380': 'fixture_corp'})
    auth._dart_get = AsyncMock(side_effect=RuntimeError('fixture outage'))
    with pytest.raises(RuntimeError): await auth.filings('005385')
    assert store.get('filings:005385')['rows'] == primary['rows']
