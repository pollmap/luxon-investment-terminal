"""fixture_non_production: source normalization to public quote/depth transport."""
from fastapi.testclient import TestClient
from services.market_dashboard import app as market
from services.market_dashboard.store import Store
from services.market_dashboard.toss import TossCollector
from services.market_dashboard.response_cache import RevisionResponseCache


def test_received_quote_and_depth_reach_public_stream_and_reconnect_snapshot(tmp_path,monkeypatch):
 store=Store('sqlite://',tmp_path)
 store.put('universe',{'rows':[{'code':'005930','name':'fixture_non_production','price':100,'source_trace':{}}]})
 store.put('depth:005930',{'book':[{'ask':101,'bid':99,'ask_quantity':10,'bid_quantity':10}],
  'book_status':'ready','trades':[],'trades_status':'ready','provider':'toss','venue':'통합','epoch':'fixture-1','gap':True})
 monkeypatch.setattr(market,'get_store',lambda:store)
 monkeypatch.setattr(market,'_detail_responses',RevisionResponseCache())
 monkeypatch.setattr(market,'_rows_cache',{})
 monkeypatch.delenv('MARKET_PUBLIC_ORIGIN',raising=False)
 collector=TossCollector(store)
 with TestClient(market.app) as client:
  with client.websocket_connect(market.BASE+'/stream') as ws:
   ws.send_json({'codes':['005930'],'channels':['quotes','depth']})
   initial=ws.receive_json();book=ws.receive_json()
   assert initial['type']=='snapshot' and initial['rows'][0]['price']==100
   assert book['type']=='depth'
   collector.save_quote({'symbol':'005930','currency':'KRW','lastPrice':'110','timestamp':'2026-10-05T09:00:00+09:00'},
    {'source':'toss_websocket','sha256':'fixture_non_production'})
   store.put('depth:005930',{'book':[{'ask':111,'bid':109,'ask_quantity':20,'bid_quantity':30}],
    'book_status':'ready','trades':[],'trades_status':'ready','provider':'toss','venue':'통합','epoch':'fixture-2','gap':True})
   seen_quote=seen_book=False
   for _ in range(4):
    event=ws.receive_json()
    if event['type']=='snapshot':
     seen_quote=event['rows'][0]['price']==110
     assert event['sequences']['005930']>initial['sequences']['005930']
    if event['type']=='depth':seen_book=event['data']['book'][0]['ask']==111
    if seen_quote and seen_book:break
   assert seen_quote and seen_book
  with client.websocket_connect(market.BASE+'/stream') as ws:
   ws.send_json({'codes':['005930'],'channels':['quotes','depth']})
   assert ws.receive_json()['rows'][0]['price']==110
   assert ws.receive_json()['data']['book'][0]['ask']==111
 store.engine.dispose()
