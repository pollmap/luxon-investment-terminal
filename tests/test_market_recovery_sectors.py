"""Synthetic/official-example fixtures, not evidence of live provider data."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from services.market_dashboard import app as market_app
from services.market_dashboard.store import Store
from services.market_dashboard.storage_budget import backfill_capacity
from services.market_dashboard.toss import TossCollector
from services.market_dashboard.toss_market import TossMarket, request_market
from services.market_dashboard.toss_contract import contract_path


def test_filings_exclude_private_companies_and_konex_without_dropping_issuer_level_reports(tmp_path):
    from services.market_dashboard.filing_scope import public_filings
    store = Store('sqlite://', tmp_path)
    store.put('universe', {'rows': [{'code':'005930','market':'KOSPI','name':'삼성전자'},
                                   {'code':'999999','market':'KONEX','name':'코넥스기업'}]})
    store.put('dart-corporations', {'codes': {'005930':'issuer'}})
    data = {'rows': [{'corp_code':'issuer','title':'issuer report'},
                     {'corp_name':'삼성전자','corp_cls':'Y','title':'legacy'},
                     {'stock_code':'999999','title':'excluded'},
                     {'corp_name':'비상장','corp_cls':'E','title':'private'}]}
    assert [r['title'] for r in public_filings(store, data)['rows']] == ['issuer report','legacy']


def test_collection_write_failure_does_not_break_snapshot_read(tmp_path, monkeypatch):
    store = Store('sqlite://', tmp_path)
    store.put('universe', {'rows': [{'code': '005930', 'name': 'fixture', 'market': 'KOSPI', 'price': 100, 'source_trace': {}}]})
    monkeypatch.setattr(market_app, 'get_store', lambda: store)
    with TestClient(market_app.app) as client:
        def readonly():
            raise OperationalError('fixture', {}, OSError('disk full'))
        monkeypatch.setattr(store.engine, 'begin', readonly)
        response = client.get('/api/market/v1/securities/005930?view=summary')
        assert response.status_code == 200
        assert response.json()['security']['price'] == 100
        assert store.active() == []  # Do not pretend the collection intent persisted.
        assert request_market(store, 'fixture', 'sector_catalogue') is False


def test_capacity_requires_below_eighty_and_successful_write(tmp_path):
    def usage(percent):
        return SimpleNamespace(total=100*1024**3, free=(100-percent)*1024**3)
    assert backfill_capacity(tmp_path, usage(90), None)['limited']
    assert backfill_capacity(tmp_path, usage(85), 'capacity_limited')['limited']
    assert backfill_capacity(tmp_path, usage(80), 'capacity_limited')['limited']
    resumed = backfill_capacity(tmp_path, usage(79), 'capacity_limited')
    assert not resumed['limited'] and resumed['write_verified']
    assert not list(tmp_path.iterdir())
    assert backfill_capacity(tmp_path/'missing', usage(79), 'capacity_limited')['limited']


def test_storage_pause_survives_queue_restart(tmp_path, monkeypatch):
    from services.market_dashboard import storage_budget
    store = Store('sqlite://', tmp_path)
    def percent(value):
        monkeypatch.setattr(storage_budget.shutil, 'disk_usage', lambda _: SimpleNamespace(
            total=100*1024**3, free=(100-value)*1024**3))
    percent(91)
    assert storage_budget.collection_capacity(store, 'xbrl')['limited']
    percent(83)
    assert storage_budget.collection_capacity(store, 'xbrl')['limited']
    percent(79)
    state = storage_budget.collection_capacity(store, 'xbrl')
    assert not state['limited'] and state['write_verified']
    assert store.get('storage-capacity:xbrl')['status'] == 'ready'


@pytest.mark.asyncio
async def test_sector_examples_hierarchy_units_and_scope(tmp_path, monkeypatch):
    spec = json.loads((Path(__file__).parents[1]/'docs/toss-openapi-reference-20261008.json').read_text(encoding='utf-8-sig'))
    async def get(path, params=None):
        if params:
            assert params.get('marketCountry') == 'KR'
        examples = spec['paths'][contract_path(path)]['get']['responses']['200']['content']['application/json']['examples']
        return next(iter(examples.values()))['value']['result'], {'source': 'fixture_non_production'}
    store = Store('sqlite://', tmp_path)
    store.put('universe', {'rows': [{'code': '005930', 'name': 'fixture', 'market': 'KOSPI', 'source_trace': {}},
                                   {'code': '000660', 'name': 'out of scope', 'market': 'KONEX'}]})
    client = SimpleNamespace(get=get)
    market = TossMarket(TossCollector(store, client))
    await market.sector_catalogue()
    await market.sector_ranking()
    await market.sector_detail('100')
    await market.sector_stocks('100')
    assert store.get('toss-sectors')['rows'][3]['parent_id'] == '42'
    ranks = store.get('toss-sector-ranking:MARKET_TRADING_AMOUNT:1d')['rows']
    assert ranks[0]['change'] == pytest.approx(1.79)
    assert ranks[1]['value'] is None
    assert store.get('toss-sector:100')['performance'][4]['change'] is None
    members = store.get('toss-sector-stocks:100')
    assert [r['code'] for r in members['rows']] == ['005930']
    assert members['membership_scope'] == 'common_shares_including_child_sectors'
    monkeypatch.setattr(market_app, 'get_store', lambda: store)
    with TestClient(market_app.app) as public:
        assert public.get('/api/market/v1/sectors/catalogue').json()['classification'] == 'toss_sector'
        assert public.get('/api/market/v1/sectors/100/stocks').json()['rows'][0]['code'] == '005930'
        assert public.get('/api/market/v1/sectors/rankings?duration=realtime').status_code == 422
        assert public.get('/api/market/v1/sectors/not-there').status_code == 404
        assert public.get('/api/market/v1/sectors').status_code == 200
    previous = store.get('toss-sector:100')
    client.get = AsyncMock(return_value=({'sectorId':'wrong', 'name':'other', 'depth':0, 'parentSectorId':None}, {}))
    await market.run_job('toss-sector:100', 1, 'sector_detail', sector_id='100')
    retained = store.get('toss-sector:100')
    assert retained['id'] == previous['id'] and retained['status'] == 'stale'
