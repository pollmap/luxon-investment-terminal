from pathlib import Path
from fastapi.testclient import TestClient
import pytest

from services.market_dashboard import app as api
from services.market_dashboard.store import Store
from services.market_dashboard.openalgo_bridge import history_via_openalgo


def test_private_bridge_scope_and_auth(tmp_path, monkeypatch):
    store=Store(url='sqlite://',root=tmp_path/'data')
    store.put('universe',{'rows':[{'code':'005930','name':'삼성전자','market':'KOSPI'}]})
    store.put('bars:005930:통합:day',{'rows':[{'date':'2026-10-02','open':100,'high':110,'low':90,'close':105,'volume':10}], 'venue':'통합','adjustment':'source','status':'ready'})
    key=tmp_path/'internal.key';key.write_text('test-private-key')
    monkeypatch.setenv('MARKET_INTERNAL_KEY_FILE',str(key))
    monkeypatch.setattr(api,'get_store',lambda:store)
    client=TestClient(api.app)
    assert client.get('/internal/market-data/005930/history').status_code==403
    result=client.get('/internal/market-data/005930/history?interval=D',headers={'X-Market-Key':'test-private-key'})
    assert result.status_code==200
    assert result.json()['rows'][0]['close']==105
    assert result.json()['venue']=='통합'
    assert client.get('/internal/market-data/005930/orders',headers={'X-Market-Key':'test-private-key'}).status_code==422
    assert client.get('/internal/market-data/005930/history?start=bad',headers={'X-Market-Key':'test-private-key'}).status_code==422


def test_sidecar_disabled_does_not_make_network_request(monkeypatch):
    monkeypatch.delenv('MARKET_OPENALGO_URL',raising=False)
    assert history_via_openalgo('005930','day',None,None) is None
