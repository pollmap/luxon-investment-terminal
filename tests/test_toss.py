"""fixture_non_production: broker contract, missing fields and prohibited endpoints."""
import pytest
import httpx
import json
import time
import asyncio
from services.market_dashboard.toss import numeric, normalize_book, normalize_candles, TossCollector, TossClient
from services.market_dashboard.store import Store

def test_retry_after_accepts_seconds_and_http_date(monkeypatch):
    from services.market_dashboard.toss import retry_after_seconds
    from email.utils import formatdate
    monkeypatch.setattr('services.market_dashboard.toss.time.time',lambda:1700000000)
    assert retry_after_seconds('30')==30
    assert retry_after_seconds(formatdate(1700000060,usegmt=True))==60
    assert retry_after_seconds('NaN')==5


def test_numeric_and_missing():
    assert numeric(None) is None
    assert numeric('0') == 0
    assert numeric('0.03215')*100 == pytest.approx(3.215)
    with pytest.raises(ValueError):numeric('NaN')


@pytest.mark.asyncio
async def test_failed_symbol_in_batch_does_not_block_other_prices(tmp_path,monkeypatch):
    collector=TossCollector(Store('sqlite://',tmp_path))
    calls=[]
    async def get(path,params):
        codes=params['symbols'].split(',');calls.append(codes)
        if '000000' in codes:
            req=httpx.Request('GET','https://example.test'+path)
            raise httpx.HTTPStatusError('fixture',request=req,response=httpx.Response(404,request=req))
        return [{'symbol':c,'currency':'KRW','lastPrice':'105','timestamp':'2026-10-02T15:30:00+09:00'} for c in codes],{}
    monkeypatch.setattr(collector.client,'get',get)
    await collector.prices(['005930','000000','000660'])
    assert set(collector.store.get('quotes'))=={'005930','000660'}
    assert collector.store.get('toss-price-error:000000')['http_status']==404
    calls.clear()
    await collector.prices(['005930','000000','000660'])
    assert calls==[['005930','000660']]
    await collector.client.close()


@pytest.mark.asyncio
async def test_price_refresh_is_independent_of_slow_history(tmp_path,monkeypatch):
    collector=TossCollector(Store('sqlite://',tmp_path))
    collector.store.put('universe',{'rows':[{'code':'005930'}]})
    collector.store.request('005930')
    calls=[]
    async def prices(codes):calls.append(list(codes))
    async def forbidden(*args,**kwargs):raise AssertionError('Quote fallback must not await history')
    async def stop(delay):
        assert delay==5
        raise asyncio.CancelledError
    monkeypatch.setattr(collector,'prices',prices)
    monkeypatch.setattr(collector,'history',forbidden)
    monkeypatch.setattr('services.market_dashboard.toss.asyncio.sleep',stop)
    with pytest.raises(asyncio.CancelledError):await collector.refresh_prices()
    assert calls==[['005930']]
    await collector.client.close()


def test_no_synthetic_ohlc():
    with pytest.raises(ValueError):
        normalize_candles([{'timestamp':'2026-10-01T00:00:00+09:00','closePrice':'100'}])


def test_book_order_and_missing_side():
    book=normalize_book({'asks':[{'price':'102','volume':'4'},{'price':'101','volume':'2'}],'bids':[{'price':'99','volume':'0'}]})
    assert book[0]['ask']==101 and book[0]['bid_quantity']==0
    assert book[1]['bid'] is None


def test_integrated_quote_does_not_copy_krx_percent(tmp_path):
    store=Store('sqlite://',tmp_path)
    store.put('quotes',{'005930':{'price':90,'change':7,'as_of':'2026-09-30'}})
    collector=TossCollector(store)
    collector.save_quote({'symbol':'005930','currency':'KRW','timestamp':'2026-10-01T15:30:00+09:00','lastPrice':'100'},{'source':'toss'})
    assert store.get('quotes')['005930']['change'] is None
    collector.save_quote({'symbol':'005930','currency':'KRW','timestamp':'2026-09-30T15:30:00+09:00','lastPrice':'1'},{'source':'toss'})
    assert store.get('quotes')['005930']['price']==100
    collector.save_quote({'symbol':'005930','currency':'KRW','timestamp':None,'lastPrice':'200'},{'source':'toss'})
    assert store.get('quotes')['005930']['price']==100
    assert store.get('quote-unavailable:005930')['status']=='timestamp_missing'


@pytest.mark.asyncio
async def test_account_order_paths_never_reach_auth(tmp_path):
    client=TossClient(Store('sqlite://',tmp_path))
    for path in ['/api/v1/orders','/api/v1/accounts','/api/v1/candles/../orders']:
        with pytest.raises(ValueError):await client.get(path)
    await client.close()


@pytest.mark.asyncio
async def test_stream_auth_failure_keeps_reconnect_loop_alive(tmp_path, monkeypatch):
    collector = TossCollector(Store('sqlite://', tmp_path))
    async def fail_token():
        request = httpx.Request('POST', 'https://example.test/oauth2/token')
        raise httpx.HTTPStatusError('fixture', request=request, response=httpx.Response(401, request=request))
    async def stop_after_backoff(delay):
        assert delay == 2
        raise asyncio.CancelledError
    monkeypatch.setattr(collector.client, 'token', fail_token)
    monkeypatch.setattr('services.market_dashboard.toss.asyncio.sleep', stop_after_backoff)
    with pytest.raises(asyncio.CancelledError):
        await collector.stream()
    assert collector.store.get('provider-toss-stream')['status'] == 'reconnecting'
    await collector.client.close()


@pytest.mark.asyncio
async def test_one_failed_snapshot_does_not_block_other_subscriptions(tmp_path, monkeypatch):
    collector=TossCollector(Store('sqlite://',tmp_path))
    collector.store.put('universe',{'rows':[{'code':'000660'},{'code':'005930'}]})
    collector.store.request('000660');collector.store.request('005930')
    collector.store.put('provider-toss-stream',{'accepted':['old-connection'],'epoch':'old'})
    async def token():
        assert collector.store.get('provider-toss-stream')['accepted']==[]
        return 'fixture-token'
    async def snapshot(code):
        if code=='000660':raise ValueError('fixture unavailable')
    sent=[]
    class Socket:
        calls=0
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def send(self,value):sent.append(json.loads(value))
        async def recv(self):
            self.calls+=1
            if self.calls==1:return json.dumps({'type':'subscriptions','subscribed':['trade:kr:005930','orderbook:kr:005930'],'rejected':[]})
            raise asyncio.CancelledError
    monkeypatch.setattr(collector.client,'token',token)
    monkeypatch.setattr(collector,'snapshot',snapshot)
    monkeypatch.setattr('websockets.connect',lambda *a,**kw:Socket())
    with pytest.raises(asyncio.CancelledError):await collector.stream()
    assert set(sent[0][0]['codes'])=={'005930','000660'}
    state=collector.store.get('provider-toss-stream')
    assert state['status']=='subscribed' and state['epoch']!='old'
    assert collector.store.get('toss-recovery:000660')['epoch']==state['epoch']
    await collector.client.close()


@pytest.mark.asyncio
async def test_revoked_token_refreshes_once_without_dropping_data(tmp_path, monkeypatch):
    token_path=tmp_path/'token.json'
    token_path.write_text(json.dumps({'access_token':'fixture-old','expires_at':time.time()+5000}))
    monkeypatch.setenv('TOSS_TOKEN_FILE',str(token_path))
    monkeypatch.setenv('TOSS_CLIENT_ID','fixture-client')
    monkeypatch.setenv('TOSS_CLIENT_SECRET','fixture-not-secret')
    requests=[]
    def respond(request):
        requests.append(request.url.path)
        if request.url.path=='/oauth2/token':
            return httpx.Response(200,json={'access_token':'fixture-new','expires_in':3600})
        if request.headers.get('Authorization')=='Bearer fixture-old':
            return httpx.Response(401)
        return httpx.Response(200,json={'result':[]})
    client=TossClient(Store('sqlite://',tmp_path/'store'),client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    assert (await client.get('/api/v1/prices',{'symbols':'005930'}))[0]==[]
    assert requests==['/api/v1/prices','/oauth2/token','/api/v1/prices']
    await client.invalidate_token('fixture-old')
    assert json.loads(token_path.read_text())['expires_at']>time.time()
    await client.close()


@pytest.mark.asyncio
async def test_five_minute_chart_keeps_observed_week_without_mixing_sessions(tmp_path, monkeypatch):
    collector=TossCollector(Store('sqlite://',tmp_path))
    async def get(path,params):
        return {'symbol':'005930','candles':[
            {'timestamp':day+'T09:00:00+09:00','openPrice':'100','highPrice':'110',
             'lowPrice':'95','closePrice':'105','volume':'10'}
            for day in ('2026-09-21','2026-09-28','2026-10-02')
        ],'nextBefore':None}, {'source':'fixture'}
    monkeypatch.setattr(collector.client,'get',get)
    await collector.history('005930','1m')
    rows=collector.store.get('bars:005930:통합:5min')['rows']
    assert [r['date'] for r in rows]==['2026-09-28','2026-10-02']
    assert [r['volume'] for r in rows]==[10,10]
    assert all(not r['complete'] for r in rows)
    assert len(collector.store.get('bars:005930:통합:1min')['rows'])==3
    assert len(collector.store.get('toss-chart:005930')['points'])==1
    await collector.client.close()


def test_first_quote_reads_only_one_prior_normalized_bar_and_rechecks_corrections(tmp_path,monkeypatch):
 from services.market_dashboard.time_series import put_series
 store=Store('sqlite://',tmp_path);collector=TossCollector(store)
 key='bars:005930:통합:day'
 put_series(store,key,{'rows':[{'date':'2026-10-01','close':90},{'date':'2026-10-02','close':100},{'date':'2026-10-05','close':999}], 'venue':'통합'})
 original=store.get
 def guarded(k,*args,**kwargs):
  assert k!=key,'Quote path deserialized lifetime history'
  return original(k,*args,**kwargs)
 monkeypatch.setattr(store,'get',guarded)
 reads=[];read=store.time_series.rows
 def bounded(series,**kwargs):
  assert kwargs.get('limit')==1 and kwargs.get('reverse') is True
  reads.append(kwargs);return read(series,**kwargs)
 monkeypatch.setattr(store.time_series,'rows',bounded)
 item={'symbol':'005930','currency':'KRW','timestamp':'2026-10-05T09:00:00+09:00','lastPrice':'110'}
 collector.save_quote(item,{'source':'fixture_non_production'})
 assert store.get('quotes')['005930']['change']==pytest.approx(10)
 collector.save_quote(item,{'source':'fixture_non_production'})
 assert len(reads)==1
 put_series(store,key,{'rows':[{'date':'2026-10-02','close':110}], 'venue':'통합'})
 collector.save_quote(item,{'source':'fixture_non_production'})
 assert store.get('quotes')['005930']['change']==0 and len(reads)==2


def test_quote_trade_day_uses_kst_even_when_source_timestamp_is_utc(tmp_path):
 from services.market_dashboard.time_series import put_series
 store=Store('sqlite://',tmp_path);collector=TossCollector(store)
 put_series(store,'bars:005930:통합:day',{'rows':[{'date':'2026-10-01','close':80},{'date':'2026-10-02','close':100}]})
 collector.save_quote({'symbol':'005930','currency':'KRW','timestamp':'2026-10-04T23:30:00Z','lastPrice':'110'},{'source':'fixture_non_production'})
 assert store.get('quotes')['005930']['change']==pytest.approx(10)
 assert ('005930','2026-10-05') in collector.previous_closes


@pytest.mark.asyncio
async def test_official_trade_and_book_frames_update_quotes_and_preserve_equal_trades(tmp_path,monkeypatch):
 collector=TossCollector(Store('sqlite://',tmp_path));collector.store.request('005930')
 collector.store.put('universe',{'rows':[{'code':'005930'}]})
 events=[{'type':'subscriptions','subscribed':['trade:kr:005930','orderbook:kr:005930'],'rejected':[]}]
 trade={'type':'message','topic':'trade:kr:005930','data':{'price':'100','volume':'2','timestamp':'2026-10-05T09:00:00+09:00','currency':'KRW'}}
 events.extend([trade,trade,{'type':'message','topic':'orderbook:kr:005930','data':{'asks':[{'price':'101','volume':'20'}],'bids':[{'price':'99','volume':'10'}],'timestamp':None,'currency':'KRW'}},
  {**trade,'data':{**trade['data'],'currency':'USD','price':'1'}},
  {**trade,'data':{**trade['data'],'price':'-1'}}])
 class Socket:
  async def __aenter__(self):return self
  async def __aexit__(self,*args):pass
  async def send(self,value):pass
  async def recv(self):
   if events:return json.dumps(events.pop(0))
   raise asyncio.CancelledError
 async def token():return 'fixture-token'
 monkeypatch.setattr(collector.client,'token',token)
 monkeypatch.setattr('websockets.connect',lambda *a,**kw:Socket())
 with pytest.raises(asyncio.CancelledError):await collector.stream()
 assert collector.store.get('quotes')['005930']['price']==100
 depth=collector.store.get('depth:005930')
 assert len(depth['trades'])==2 and depth['book'][0]['ask']==101
 assert depth['book_as_of'] is None
 assert collector.store.get('toss-stream-rejected:005930')['reason']=='unsupported_or_invalid_payload'
 await collector.client.close()
