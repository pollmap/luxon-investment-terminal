"""Public market API contracts. Fixtures are not evidence of live provider access."""
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from services.market_dashboard import app as market_app
from services.market_dashboard.toss import TossClient, TossCollector
from services.market_dashboard.toss_contract import PUBLIC_GET_PATHS, contract_path
from services.market_dashboard.toss_market import TossMarket
from services.market_dashboard.store import Store

def test_public_contracts_exclude_private_and_etf_routes():
    assert len(PUBLIC_GET_PATHS) == len(set(PUBLIC_GET_PATHS)) == 24
    for template in PUBLIC_GET_PATHS:
        path=template.replace('{symbol}','KOSPI' if 'market-indicators' in template else '005930').replace('{sectorId}', '100')
        assert contract_path(path)==template
    for path in ('/api/v1/orders','/api/v1/accounts','/api/v1/stocks/AAPL/warnings', '/api/v1/stocks/005930', '/api/v1/market-indicators/INVALID/candles', '/api/v1/sectors/100/etfs', '/api/v1/sectors/../stocks'):
        with pytest.raises(ValueError):contract_path(path)

@pytest.mark.asyncio
async def test_rate_limit_retries_and_keeps_raw_evidence(tmp_path,monkeypatch):
    store=Store('sqlite://',tmp_path)
    replies=iter([httpx.Response(429,headers={'Retry-After':'0'}),httpx.Response(200,json={'result':[]})])
    client=TossClient(store,client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:next(replies))))
    client.token=AsyncMock(return_value='fixture_non_production')
    monkeypatch.setenv('TOSS_REQUEST_INTERVAL','.1')
    result,trace=await client.get('/api/v1/prices',{'symbols':'005930'})
    assert result==[] and trace['source_document_id']
    assert store.get('toss-api:/api/v1/prices')['status']=='received'
    await client.close()


@pytest.mark.asyncio
async def test_http_failure_is_counted_without_replacing_last_success_evidence(tmp_path,monkeypatch):
    store=Store('sqlite://',tmp_path)
    replies=iter([
        httpx.Response(200,json={'result':{'warnings':[{'code':'fixture'}]}}),
        httpx.Response(404,json={'error':'not_found'}),
    ])
    client=TossClient(store,client=httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request:next(replies))))
    client.token=AsyncMock(return_value='fixture_non_production')
    monkeypatch.setenv('TOSS_REQUEST_INTERVAL','.1')
    result, _ = await client.get('/api/v1/stocks/999999/warnings')
    assert result['warnings'][0]['code']=='fixture'
    with pytest.raises(httpx.HTTPStatusError):
        await client.get('/api/v1/stocks/999999/warnings')
    recorded=store.get('toss-api:/api/v1/stocks/{symbol}/warnings')
    assert recorded['status']=='http_error'
    assert recorded['last_error']=='HTTP_404'
    assert recorded['metrics']['request_count']==2
    assert recorded['metrics']['http_errors']==1
    assert recorded['metrics']['http_status_counts']=={'200':1,'404':1}
    assert recorded['last_success']['sha256']
    assert 'fixture_non_production' not in str(recorded)
    await client.close()

@pytest.mark.asyncio
async def test_all_added_public_collectors_normalize_official_examples(tmp_path):
    spec=json.loads((Path(__file__).parents[1]/'docs/toss-openapi-reference-20261003.json').read_text(encoding='utf-8'))
    async def get(path,params=None):
        template=contract_path(path)
        examples=spec['paths'][template]['get']['responses']['200']['content']['application/json']['examples']
        data=next(iter(examples.values()))['value']['result']
        return data,{'source':'fixture_non_production','sha256':'fixture'}
    store=Store('sqlite://',tmp_path)
    store.put('universe',{'rows':[{'code':'005930','name':'삼성전자','market':'KOSPI'},{'code':'000660','name':'SK하이닉스','market':'KOSPI'}]})
    collector=TossCollector(store,client=type('Client',(),{'get':staticmethod(get)})())
    market=TossMarket(collector)
    await market.ranking()
    await market.indicators()
    await market.index_candles('KOSPI')
    await market.index_investors('KOSPI')
    await market.stocks(['005930'])
    await market.listings('KOSPI')
    await market.warnings('005930')
    await market.limits('005930')
    await market.calendar('KR')
    await market.calendar('US')
    await market.exchange_rate()
    ranking=store.get('toss-ranking:MARKET_TRADING_AMOUNT:1d:0')
    assert ranking['rows'][0]['change']==pytest.approx(1.25)
    assert store.get('toss-exchange-rate')['rate']==1375
    assert store.get('toss-index-investors:KOSPI:1d')['rows'][-1]['foreigner']['net']==200000000000
    assert store.get('toss-index-investors:KOSPI:1d')['unit']=='원'
    assert len(store.listed_codes())==2

def test_institution_breakdown_keeps_missing_sides_missing():
    from services.market_dashboard.toss_market import institution_amounts
    result=institution_amounts({'pensionFund':{'buyAmount':'15000000000','sellAmount':'10000000000'},
                                'insurance':{'buyAmount':'20'}})
    assert result['pensionFund']['net']==5000000000
    assert result['insurance']['net'] is None and result['bank']['buy'] is None

def test_public_endpoints_validate_scope_and_never_emit_authentication(tmp_path,monkeypatch):
    store=Store('sqlite://',tmp_path)
    store.put('universe',{'rows':[{'code':'005930','name':'fixture','market':'KOSPI','source_trace':{'source':'fixture'}}]})
    store.put('toss-warnings:005930',{'rows':[],'status':'ready','source_trace':{'url':'fixture_private'},'error':'internal_error'})
    monkeypatch.setattr(market_app,'get_store',lambda:store)
    market_app._rows_cache.clear()
    with TestClient(market_app.app) as client:
        assert client.get(market_app.BASE+'/rankings').status_code==200
        assert client.get(market_app.BASE+'/rankings?type=TOP_GAINERS&duration=realtime').status_code==422
        assert client.get(market_app.BASE+'/indices/KR_BOND_10Y/investors').status_code==422
        response=client.get(market_app.BASE+'/securities/005930/warnings')
        assert response.status_code==200 and 'fixture_private' not in response.text and 'internal_error' not in response.text
        assert client.get(market_app.BASE+'/securities/999999/warnings').status_code==404

@pytest.mark.asyncio
async def test_snapshot_cannot_replace_newer_stream_trade_or_book(tmp_path):
    store=Store('sqlite://',tmp_path)
    async def get(path,params=None):
        store.put('depth:005930',{'book':[{'ask':110}], 'book_as_of':'2026-10-02T12:00:10+09:00',
            'trades':[{'price':110}], 'last_trade_received_at':'9999-01-01', 'trades_status':'ready'})
        return ({'timestamp':'2026-10-02T12:00:00+09:00','asks':[{'price':'100','volume':'1'}]} if path.endswith('orderbook') else []),{}
    collector=TossCollector(store,client=type('Client',(),{'get':staticmethod(get)})())
    await collector.snapshot('005930',include_price=False)
    data=store.get('depth:005930')
    assert data['book'][0]['ask']==110 and data['trades'][0]['price']==110
