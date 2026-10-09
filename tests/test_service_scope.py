import importlib
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from services.market_dashboard.service_scope import REVIEWED_NON_SHARE_INSTRUMENTS, universe_view
from services.market_dashboard.store import Store


def seeded(tmp_path):
    s=Store('sqlite://',tmp_path)
    s.put('universe',{'rows':[{'code':'005930','name':'삼성전자','market':'KOSPI'},
        {'code':'000660','name':'fixture 코스닥','market':'KOSDAQ'},
        {'code':'402420','name':'fixture 코넥스','market':'KONEX'}],
        'coverage':[{'market':'KOSPI'},{'market':'KOSDAQ'},{'market':'KONEX'}],
        'unavailable_markets':['KONEX']})
    return s

def test_service_scope_excludes_konex_without_deleting_source_or_history(tmp_path):
    s=seeded(tmp_path)
    s.put('prices:402420',{'rows':[{'date':'2020-01-01','close':1}]})
    s.request('402420')
    s.request('005930')
    assert s.listed_codes()==('000660','005930')
    assert s.active()==['005930']
    public=universe_view(s.get('universe'))
    assert len(public['rows'])==2 and len(public['coverage'])==2
    assert public['unavailable_markets']==[]
    assert len(s.get('universe')['rows'])==3
    assert s.get('prices:402420')['rows'][0]['close']==1

def test_search_index_and_analysis_do_not_reintroduce_konex(tmp_path,monkeypatch):
    app=importlib.import_module('services.market_dashboard.app')
    s=seeded(tmp_path)
    monkeypatch.setattr(app,'get_store',lambda:s)
    client=TestClient(app.app)
    result=client.get('/api/market/v1/search-index').json()
    assert {r['code'] for r in result['rows']}=={'005930','000660'}
    assert client.get('/api/market/v1/securities/402420/price-window').status_code==404


def test_health_reports_current_in_scope_count_without_mutating_worker_snapshot(
    tmp_path, monkeypatch,
):
    app=importlib.import_module('services.market_dashboard.app')
    s=seeded(tmp_path)
    s.put('worker',{'status':'ready','listed_securities':3})
    monkeypatch.setattr(app,'get_store',lambda:s)
    result=TestClient(app.app).get('/api/market/v1/health').json()
    assert result['worker']['listed_securities']==2
    assert s.get('worker')['listed_securities']==3


def test_cached_provider_ranking_respects_current_scope_and_preserves_raw(tmp_path,monkeypatch):
    app=importlib.import_module('services.market_dashboard.app')
    s=seeded(tmp_path)
    key='toss-ranking:MARKET_TRADING_AMOUNT:1d:0'
    source={'status':'ready','rows':[{'code':'402420','rank':1},
        {'code':'005930','rank':2},{'code':'999999','rank':3}]}
    s.put(key,source)
    monkeypatch.setattr(app,'get_store',lambda:s)
    result=TestClient(app.app).get('/api/market/v1/rankings').json()
    assert result['rows']==[{'code':'005930','rank':2}]
    assert s.get(key)==source


def test_listed_fund_units_are_outside_share_universe_but_source_rows_survive(tmp_path):
    from services.market_dashboard.store import Store
    s=Store('sqlite://',tmp_path)
    rows=[
        {'code':'005930','name':'삼성전자','market':'KOSPI'},
        {'code':'0106J0','name':'대신 KOSPI200인덱스 X클래스','market':'KOSPI'},
        {'code':'0120X0','name':'유진 챔피언중단기크레딧 X클래스','market':'KOSPI'},
        {'code':'094800','name':'맵스리얼티','market':'KOSPI'},
        {'code':'402420','name':'KONEX','market':'KONEX'},
    ]
    s.put('universe',{'rows':rows,'coverage':[{'market':'KOSPI','received':4},{'market':'KONEX','received':1}]})
    assert s.listed_codes()==('005930','094800')
    public=universe_view(s.get('universe'))
    assert {r['code'] for r in public['rows']}=={'005930','094800'}
    assert public['coverage']==[{
        'market':'KOSPI','received':4,'eligible_rows':2,'excluded_non_share_instruments':2,
    }]
    assert public['excluded_instruments']==REVIEWED_NON_SHARE_INSTRUMENTS
    assert [r['code'] for r in s.get('universe')['rows']]==[r['code'] for r in rows]
    assert s.get('universe')['rows'][1]['code']=='0106J0'


@pytest.mark.asyncio
async def test_provider_ranking_does_not_collect_excluded_rows(tmp_path):
    from services.market_dashboard.toss_market import TossMarket
    s=seeded(tmp_path)
    async def get(*args):
        return {'rankings':[{'symbol':code,'rank':i,'currency':'KRW',
            'price':{'lastPrice':'1','changeRate':'0'},'tradingVolume':'1','tradingAmount':'1'}
            for i,code in enumerate(('402420','005930'),1)]}, {'source':'fixture_non_production'}
    await TossMarket(SimpleNamespace(store=s,client=SimpleNamespace(get=get))).ranking()
    rows=s.get('toss-ranking:MARKET_TRADING_AMOUNT:1d:0')['rows']
    assert len(rows)==1 and rows[0]['code']=='005930' and rows[0]['rank']==2


@pytest.mark.asyncio
async def test_reconcile_archives_old_fund_jobs_without_dropping_cursor_or_source_state(tmp_path):
    from services.market_dashboard.toss_backfill import TossBackfill
    s=Store('sqlite://',tmp_path)
    s.put('universe',{'rows':[{'code':'005930','name':'삼성전자','market':'KOSPI'},
        {'code':'0106J0','name':'대신 KOSPI200인덱스 X클래스','market':'KOSPI'}]})
    previous={'status':'error','http_status':404,'next_before':'2015-01-01',
        'visits':12,'raw_source_digest':'fixture_non_production'}
    s.put('toss-backfill:0106J0:history',previous)
    worker=TossBackfill(SimpleNamespace(store=s))
    await worker.reconcile(s.listed_codes())
    assert s.get('toss-backfill:0106J0:history')['status']=='out_of_scope'
    archived=s.get('toss-backfill-out-of-scope:0106J0:history')
    assert archived['state']==previous and archived['state']['next_before']=='2015-01-01'
    assert s.get('toss-backfill:005930:history')['status']=='queued'


@pytest.mark.asyncio
@pytest.mark.parametrize('name', ['맵스리얼티1', '맵스리얼티'])
async def test_maps_common_share_catalogue_omission_stays_visible_and_in_scope(
    tmp_path, monkeypatch, name,
):
    from services.market_dashboard.toss_backfill import TossBackfill

    app = importlib.import_module('services.market_dashboard.app')
    store = Store('sqlite://', tmp_path)
    source = {
        'rows': [
            {'code': '094800', 'name': name, 'market': 'KOSPI',
             'isin': 'KR7094800000', 'security_type': 'stock'},
            {'code': '0106J0', 'name': '상장형 수익증권', 'market': 'KOSPI'},
        ],
        'source_trace': {'source': 'fixture_non_production'},
        'coverage': [{'market': 'KOSPI', 'received': 2}],
    }
    history = {'rows': [{'date': '2020-01-02', 'close': 100}],
               'source_trace': {'source': 'fixture_non_production'}}
    job = {
        'status': 'not_in_provider_catalogue', 'http_status': 404,
        'next_before': '2019-12-31', 'visits': 2,
        'provider_catalogue_revision': 'fixture_non_production',
        'diagnosis': {'status': 'provider_catalogue_omission'},
    }
    store.put('universe', source)
    store.put('prices:094800', history)
    store.put('toss-backfill:094800:history', job)
    store.request('094800')

    assert store.listed_codes() == ('094800',)
    assert store.active() == ['094800']
    await TossBackfill(SimpleNamespace(store=store)).reconcile(store.listed_codes())
    assert store.get('toss-backfill:094800:history') == job
    assert store.get('toss-backfill-out-of-scope:094800:history') is None
    assert store.get('universe') == source
    assert store.get('prices:094800') == history

    monkeypatch.setattr(app, 'get_store', lambda: store)
    result = TestClient(app.app).get('/api/market/v1/search-index')
    assert result.status_code == 200
    assert [(row['code'], row['name']) for row in result.json()['rows']] == [
        ('094800', name),
    ]
