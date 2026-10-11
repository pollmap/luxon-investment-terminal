"""fixture_non_production: historical selections cannot poison latest values."""
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from services.market_dashboard import app as api
from services.market_dashboard.store import Store
from services.market_dashboard.toss_market import TossMarket
from services.market_dashboard.market_selection import exchange_selection,listing_selection


@pytest.mark.asyncio
async def test_calendar_and_exchange_parameters_and_cache_isolation(tmp_path):
    store=Store('sqlite://',tmp_path);calls=[]
    async def get(path,params=None):
        calls.append((path,params))
        if 'calendar' in path:
            return {'today':{'date':(params or {}).get('date','2026-10-03')}},{'source':'fixture_non_production'}
        return {'midRate':'0.0007' if params['baseCurrency']=='KRW' else '1400','validFrom':params.get('dateTime')},{'source':'fixture_non_production'}
    market=TossMarket(SimpleNamespace(store=store,client=SimpleNamespace(get=get)))
    await market.calendar('KR')
    await market.calendar('KR','2025-01-02')
    assert calls[-1][1]=={'date':'2025-01-02'}
    assert store.get('toss-calendar:KR')['calendar']['today']['date']=='2026-10-03'
    await market.exchange_rate()
    await market.exchange_rate('KRW','USD','2025-01-02T09:30:00+09:00')
    key,_=exchange_selection('KRW','USD','2025-01-02T00:30:00Z')
    assert store.get(key)['rate']==0.0007
    assert store.get('toss-exchange-rate')['rate']==1400
    assert calls[-1][1]['dateTime']=='2025-01-02T00:30:00+00:00'


@pytest.mark.asyncio
async def test_listing_filters_never_delete_existing_universe(tmp_path):
    store=Store('sqlite://',tmp_path)
    store.put('universe',{'rows':[{'code':'005930'}]})
    calls=[]
    async def get(path,params=None):
        calls.append(params)
        return [],{'source':'fixture_non_production'}
    market=TossMarket(SimpleNamespace(store=store,client=SimpleNamespace(get=get)))
    await market.listings('KOSPI','DELISTED','STOCK',False)
    assert calls[-1]=={'market':'KOSPI','status':'DELISTED','securityType':'STOCK','commonShare':'false'}
    assert store.listed_codes()==('005930',)
    assert store.get(listing_selection('KOSPI','DELISTED','STOCK',False))['rows']==[]


def test_api_queues_exact_selections_and_rejects_private_scope(tmp_path,monkeypatch):
    store=Store('sqlite://',tmp_path)
    monkeypatch.setattr(api,'get_store',lambda:store)
    with TestClient(api.app) as client:
        result=client.get(api.BASE+'/market-context',params={'date':'2025-01-02','date_time':'2025-01-02T09:00:00+09:00','base':'KRW','quote':'USD'})
        assert result.status_code==200
        tasks=list(store.list_prefix('toss-market-request:').values())
        assert len(tasks)==3 and any(r['params'].get('day')=='2025-01-02' for r in tasks)
        assert client.get(api.BASE+'/market-context?date_time=2025-01-02T09:00').status_code==422
        assert client.get(api.BASE+'/market-context?date=2025-02-30').status_code==422
        assert client.get(api.BASE+'/market-context?base=EUR').status_code==422
        assert client.get(api.BASE+'/listings?market=NASDAQ').status_code==422
        assert client.get(api.BASE+'/listings?security_type=ETF').status_code==422
        assert client.get(api.BASE+'/listings?status=DELISTED&common_share=false').status_code==200


def test_market_investor_window_keeps_archive_and_legacy_response(tmp_path,monkeypatch):
    store=Store('sqlite://',tmp_path)
    from datetime import date,timedelta
    rows=[{'date':(date(2025,1,1)+timedelta(days=i)).isoformat(),'individual':i} for i in range(90)]
    store.put('toss-index-investors:KOSPI:1d',{'rows':rows,'status':'ready'})
    monkeypatch.setattr(api,'get_store',lambda:store)
    with TestClient(api.app) as client:
        path=api.BASE+'/indices/KOSPI/investors'
        page=client.get(path+'?limit=30').json()
        assert page['total_rows']==90 and len(page['rows'])==30
        assert page['rows'][0]['date']==rows[-30]['date']
        assert page['rows'][-1]['individual']==89
        assert len(client.get(path+'?limit=60').json()['rows'])==60
        assert len(client.get(path).json()['rows'])==90
        for value in ('0','-1','10001','invalid'):
            assert client.get(path+'?limit='+value).status_code==422
    assert store.get('toss-index-investors:KOSPI:1d')['rows']==rows
