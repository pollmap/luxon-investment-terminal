"""Deterministic request-start budget tests; no live authentication."""
import httpx
import pytest
from services.market_dashboard import toss
from services.market_dashboard.store import Store


class Clock:
    value=10.0
    async def sleep(self,seconds):
        self.value+=seconds
    def monotonic(self):return self.value


@pytest.mark.asyncio
@pytest.mark.parametrize('latency,expected_gap',[(.8,.8),(.1,.5)])
async def test_slow_roundtrip_consumes_interval_fast_roundtrip_keeps_budget(tmp_path,monkeypatch,latency,expected_gap):
    clock=Clock();starts=[]
    monkeypatch.setattr(toss.time,'monotonic',clock.monotonic)
    monkeypatch.setattr(toss.asyncio,'sleep',clock.sleep)
    monkeypatch.setenv('TOSS_REQUEST_INTERVAL','.5')
    class Client:
        async def get(self,url,**kwargs):
            starts.append(clock.value);clock.value+=latency
            return httpx.Response(200,request=httpx.Request('GET',url),json={'result':{'prices':[]}})
    client=toss.TossClient(Store('sqlite://',tmp_path),client=Client())
    async def token():return 'fixture_non_production'
    client.token=token
    await client.get('/api/v1/prices');await client.get('/api/v1/prices')
    assert starts[1]-starts[0]==pytest.approx(expected_gap)
    metrics=client.store.get('toss-api:/api/v1/prices')
    assert metrics['upstream_ms']==pytest.approx(latency*1000)
    assert metrics['budget_wait_ms']==pytest.approx(max(0,.5-latency)*1000)
    assert metrics['metrics']['request_count']==2
    assert metrics['metrics']['successful_responses']==2
    assert metrics['metrics']['session_id']==client.metrics_session


@pytest.mark.asyncio
async def test_retry_after_starts_at_error_response_even_when_roundtrip_is_slow(tmp_path,monkeypatch):
    clock=Clock();starts=[]
    monkeypatch.setattr(toss.time,'monotonic',clock.monotonic)
    monkeypatch.setattr(toss.asyncio,'sleep',clock.sleep)
    monkeypatch.setenv('TOSS_REQUEST_INTERVAL','.5')
    class Client:
        async def get(self,url,**kwargs):
            starts.append(clock.value);clock.value+=.8
            return httpx.Response(429 if len(starts)==1 else 200,request=httpx.Request('GET',url),
                headers={'Retry-After':'2'},json={'result':{'prices':[]}})
    client=toss.TossClient(Store('sqlite://',tmp_path),client=Client())
    async def token():return 'fixture_non_production'
    client.token=token
    await client.get('/api/v1/prices')
    assert starts==pytest.approx([10.0,12.8])
    metrics=client.store.get('toss-api:/api/v1/prices')
    assert metrics['budget_wait_ms']==2000
    assert metrics['metrics']['request_count']==2
    assert metrics['metrics']['successful_responses']==1
    assert metrics['metrics']['rate_limit_responses']==1
    assert metrics['metrics']['http_status_counts']=={'200':1,'429':1}


def test_response_row_count_uses_endpoint_records_without_inventing_rows():
    count=toss.TossClient._result_row_count
    assert count('/api/v1/stocks/005930/investor-trading',{'records':[{},{}]})==2
    assert count('/api/v1/orderbook',{'asks':[{}],'bids':[{},{}]})==3
    assert count('/api/v1/stocks/005930/warnings',{'warnings':[]})==0
