import asyncio
import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import httpx
import pytest
from services.market_dashboard.authenticated_sources import AuthenticatedSources,DartDailyBudgetExceeded
from services.market_dashboard.store import Store

@pytest.mark.parametrize('content', [b'{"status":"020","message":"limit"}',b'<result><status>020</status><message>limit</message></result>'])
def test_provider_limit_stops_other_periods_and_new_collector_instances(tmp_path,content):
    store=Store('sqlite://',tmp_path);calls=[]
    class Client:
        async def get(self,url,params):
            calls.append(url)
            return httpx.Response(200,content=content)
    provider=SimpleNamespace(store=store,client=Client())
    first=AuthenticatedSources(provider);first.dart='fixture_non_production'
    with pytest.raises(DartDailyBudgetExceeded):asyncio.run(first._dart_get('https://example.test/first',params={}))
    other=AuthenticatedSources(provider);other.dart='fixture_non_production'
    with pytest.raises(DartDailyBudgetExceeded):asyncio.run(other._dart_get('https://example.test/second',params={}))
    assert len(calls)==1
    limit=store.get('dart-provider-limit')
    assert limit['date']==datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()
    assert limit['source_hash']
    assert store.get('dart-usage')['count']==1


def test_previous_day_limit_does_not_block_successful_new_day_or_zip(tmp_path):
    store=Store('sqlite://',tmp_path)
    store.put('dart-provider-limit',{'date':'2000-01-01','status':'020'})
    class Client:
        async def get(self,url,params):return httpx.Response(200,content=b'PK\x03\x04binary')
    source=AuthenticatedSources(SimpleNamespace(store=store,client=Client()));source.dart='fixture_non_production'
    response=asyncio.run(source._dart_get('https://example.test/xbrl',params={}))
    assert response.content==b'PK\x03\x04binary'
    assert store.get('dart-usage')['count']==1
