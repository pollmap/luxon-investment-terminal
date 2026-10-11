import asyncio
from services.market_dashboard.financial_worker import step
from services.market_dashboard.store import Store


def test_full_financial_queue_serves_viewed_and_background_independent_of_filings_ttl(tmp_path):
    store=Store('sqlite://',tmp_path)
    store.put('universe',{'rows':[{'code':'005930'},{'code':'000660'}]})
    store.request('005930')
    store.put('dart-full-report-progress',{'visits':4})
    calls=[]
    class Source:
        dart=True
        async def refresh_financial_data(self,code,max_tasks):
            calls.append((code,max_tasks))
            return {'status':'ready'}
    source=Source();source.store=store
    result=asyncio.run(step(source))
    assert calls==[('005930',1),('000660',1)]
    assert result['cursor']==1
    assert store.get('dart-full-report-progress')['status']=='collecting'


def test_financial_budget_exhaustion_preserves_cursor_and_stops_background_calls(tmp_path):
    store=Store('sqlite://',tmp_path)
    store.put('universe',{'rows':[{'code':'005930'},{'code':'000660'}]})
    store.put('dart-full-report-progress',{'cursor':1,'active_cursor':3,'visits':4})
    store.request('005930')
    calls=[]
    class Source:
        dart=True
        async def refresh_financial_data(self,code,max_tasks):
            calls.append(code)
            return {'status':'rate_limited','processed':0}
    source=Source();source.store=store
    result=asyncio.run(step(source))
    assert calls==['005930']
    assert result['status']=='rate_limited'
    assert result['cursor']==1 and result['active_cursor']==3


def test_background_receives_five_slots_before_repeating_viewed_company(tmp_path):
    store=Store('sqlite://',tmp_path)
    store.put('universe',{'rows':[{'code':f'{i:06d}'} for i in range(8)]})
    store.request('000007')
    calls=[]
    class Source:
        dart=True
        async def refresh_financial_data(self,code,max_tasks):
            calls.append(code)
            return {'status':'ready','processed':1}
    source=Source();source.store=store
    for _ in range(5):asyncio.run(step(source))
    assert calls==['000000','000001','000002','000003','000007','000004']
    assert store.get('dart-full-report-progress')['next_code']=='000005'
    assert store.get('dart-full-report-progress')['active_cursor']==1


def test_new_listing_does_not_shift_persisted_next_security(tmp_path):
    store=Store('sqlite://',tmp_path)
    store.put('universe',{'rows':[{'code':c} for c in ['000001','000002','000003']]})
    calls=[]
    class Source:
        dart=True
        async def refresh_financial_data(self,code,max_tasks):
            calls.append(code)
            return {'status':'ready'}
    source=Source();source.store=store
    asyncio.run(step(source))
    store.put('universe',{'rows':[{'code':c} for c in ['000000','000001','000002','000003']]})
    asyncio.run(step(source))
    assert calls==['000001','000002']
    # The deleted next target advances to its successor, not to an unrelated index.
    store.put('universe',{'rows':[{'code':c} for c in ['000000','000001','000002','000004']]})
    asyncio.run(step(source))
    assert calls[-1]=='000004'


def test_partial_priority_success_then_limit_keeps_background_target(tmp_path):
    store=Store('sqlite://',tmp_path)
    store.put('universe',{'rows':[{'code':c} for c in ['000001','000002']]})
    store.request('000002')
    store.put('dart-full-report-progress',{'visits':4,'next_code':'000001'})
    calls=[]
    class Source:
        dart=True
        async def refresh_financial_data(self,code,max_tasks):
            calls.append(code)
            return {'status':'ready' if code=='000002' else 'rate_limited'}
    source=Source();source.store=store
    result=asyncio.run(step(source))
    assert calls==['000002','000001']
    assert result['next_code']=='000001'
    assert result['visits']==4
