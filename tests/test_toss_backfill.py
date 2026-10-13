"""fixture_non_production: queue fairness, recovery and collection failure semantics."""
from types import SimpleNamespace
from datetime import datetime, timedelta

import httpx
import pytest

from services.market_dashboard.store import Store, now
from services.market_dashboard.toss import TossCollector
from services.market_dashboard.toss_backfill import (
    TossBackfill,
    after,
    burst_pages,
    classify_provider_catalogue_omissions,
    pause_seconds,
    price_collection_status,
    recent_head,
    resume_history,
)


@pytest.mark.parametrize(('configured','expected'),[(None,16),('8',8),('1',1),('99',16),('bad',16)])
def test_backfill_burst_is_bounded_and_invalid_config_falls_back(monkeypatch,configured,expected):
    monkeypatch.delenv('MARKET_TOSS_HISTORY_BURST',raising=False)
    if configured is not None:
        monkeypatch.setenv('MARKET_TOSS_HISTORY_BURST',configured)
    assert burst_pages('MARKET_TOSS_HISTORY_BURST') == expected


def test_retry_dates_compare_instants_across_legacy_kst_checkpoints():
    assert not after('2026-10-03T16:00:00+09:00','2026-10-03T08:00:00+00:00')
    assert after('2026-10-03T18:00:00+09:00','2026-10-03T08:00:00+00:00')
    assert not after('invalid','2026-10-03T08:00:00+00:00')


def test_midnight_does_not_starve_old_history_and_backfill_is_not_a_head_refresh():
    snapshot={'head_received_at':'2026-10-01T00:00:00+00:00',
              'received_at':'2026-10-03T00:00:00+00:00','next_before':'old'}
    assert not recent_head(snapshot,'2026-10-03T01:00:00+00:00')
    assert [resume_history(snapshot,'next_before','2026-10-03T01:00:00+00:00',i) for i in range(5)]==[False,True,True,True,True]
    assert recent_head({'head_received_at':'2026-10-02T16:00:00+00:00'},'2026-10-03T01:00:00+09:00')


@pytest.mark.asyncio
async def test_history_resume_keeps_latest_clock_separate(tmp_path):
    store,collector,_=setup(tmp_path)
    await collector.history('000660')
    before=store.metadata('bars:000660:통합:day')['head_received_at']
    await collector.history('000660',resume=True)
    row=store.metadata('bars:000660:통합:day')
    assert row['head_received_at']==before and row['history_received_at']


def disk(_):
    return SimpleNamespace(total=100 * 1024**3, free=50 * 1024**3)


class Client:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    async def get(self, path, params):
        self.calls.append((path, params))
        if self.fail:
            req = httpx.Request('GET', 'https://example.test' + path)
            raise httpx.HTTPStatusError('fixture', request=req, response=httpx.Response(401, request=req))
        if path.endswith('candles'):
            day = '2020-01-02' if params.get('before') else '2026-10-02'
            return {'candles': [{'timestamp': day + 'T15:30:00+09:00', 'openPrice': '10',
                'closePrice': '11', 'highPrice': '12', 'lowPrice': '9', 'volume': '20'}],
                'nextBefore': None if params.get('before') else '2020-01-02T15:30:00+09:00'}, {'source': 'fixture_non_production'}
        return {'records': []}, {'source': 'fixture_non_production'}


def setup(tmp_path, client=None):
    store = Store('sqlite://', tmp_path)
    store.put('universe', {'rows': [{'code': '000660'}, {'code': '005930'}]})
    collector = TossCollector(store, client or Client())
    return store, collector, TossBackfill(collector, disk)


@pytest.mark.asyncio
async def test_restart_and_fair_phase_scan(tmp_path):
    store, collector, queue = setup(tmp_path)
    await queue.step()
    assert store.get('toss-backfill-progress')['cursor'] == 0
    await TossBackfill(collector, disk).step()
    progress = store.get('toss-backfill-progress')
    assert (progress['cursor'], progress['phase']) == (0, 2)
    assert len(collector.client.calls) == 3
    assert collector.client.calls[-1][0].endswith('/000660/investor-trading')
    assert store.metadata('bars:000660:통합:day')['backfill_complete']
    assert store.get('toss-backfill:000660:investor-trading')['status'] == 'no_data'
    for _ in range(12):
        await TossBackfill(collector, disk).step()
    assert store.metadata('bars:005930:통합:day')['backfill_complete']
    assert len(store.list_prefix('toss-backfill:')) == 14
    assert store.get('toss-backfill-progress')['cycles'] == 1


@pytest.mark.asyncio
async def test_resume_does_not_lose_old_pages_on_head_refresh(tmp_path):
    store, collector, _ = setup(tmp_path)
    await collector.history('000660')
    assert store.get('bars:000660:통합:day')['next_before'] == '2020-01-02T15:30:00+09:00'
    await collector.history('000660')
    result = await collector.history('000660', resume=True)
    assert len(result['rows']) == 2 and result['backfill_complete']
    await collector.history('000660')
    assert len(store.get('bars:000660:통합:day')['rows']) == 2
    assert store.get('bars:000660:통합:day')['backfill_complete']


@pytest.mark.asyncio
async def test_auth_failure_is_retryable_not_source_unavailable(tmp_path):
    store, collector, queue = setup(tmp_path, Client(fail=True))
    assert await queue.step() == 'error'
    state = store.get('toss-backfill:000660:history')
    assert state['status'] == 'error' and state['http_status'] == 401
    assert store.get('toss-backfill-progress')['status'] == 'provider_backoff'
    assert await queue.step() == 'backoff'
    assert len(collector.client.calls) == 1


@pytest.mark.asyncio
async def test_due_item_error_gets_a_bounded_retry_turn_before_full_universe_cycle(tmp_path):
    store, collector, queue = setup(tmp_path)
    error_key = 'toss-backfill:005930:short-selling'
    store.put(error_key, {'status': 'error', 'retry_after': '2020-01-01T00:00:00+00:00',
                          'failures': 1})
    store.put('trading-history-request:000660:history',
              {'code': '000660', 'kind': 'history', 'at': now()})
    store.put('toss-backfill-progress', {'cursor': 0, 'phase': 0, 'attempts': 5, 'cycles': 0})

    result = await queue.step()

    assert result == 'no_data'
    assert collector.client.calls[0][0].endswith('/005930/short-selling')
    progress = store.get('toss-backfill-progress')
    assert progress['cursor'] == 0 and progress['phase'] == 0
    assert store.get(error_key)['status'] == 'no_data'


@pytest.mark.asyncio
async def test_disk_guard_preserves_cursor_and_sources(tmp_path):
    store, collector, _ = setup(tmp_path)
    queue = TossBackfill(collector, lambda _: SimpleNamespace(total=1000 * 1024**3, free=99 * 1024**3))
    assert await queue.step() == 'capacity_limited'
    assert not collector.client.calls


@pytest.mark.asyncio
async def test_stalled_provider_cursor_keeps_last_good_snapshot(tmp_path):
    store, collector, _ = setup(tmp_path)
    await collector.history('000660')
    previous = store.get('bars:000660:통합:day')
    async def stalled(*args):
        return {'candles': [], 'nextBefore': '2020-01-02T15:30:00+09:00'}, {}
    collector.client.get = stalled
    with pytest.raises(ValueError, match='did not advance'):
        await collector.history('000660', resume=True)
    assert store.get('bars:000660:통합:day') == previous


@pytest.mark.asyncio
async def test_trade_pages_preserve_history_and_each_row_source(tmp_path):
    store, collector, _ = setup(tmp_path)
    async def page(path, params):
        older=bool(params.get('until'))
        return {'records':[{'date':'2025-10-01' if older else '2026-10-02','marginLoan':{'balanceQuantity':'5'}}],
                'nextUntil':None if older else '2020-01-02T15:30:00+09:00'}, {'source':'fixture_non_production','sha256':'old' if older else 'head'}
    collector.client.get=page
    await collector.fundamentals_of_trading('000660',('credit-trades',))
    await collector.fundamentals_of_trading('000660',('credit-trades',),resume=True)
    await collector.fundamentals_of_trading('000660',('credit-trades',))
    state=store.get('toss:credit-trades:000660')
    assert state['backfill_complete'] and state['next_until'] is None and len(state['rows'])==2
    rows=store.get('toss-normalized:financing:000660')['credit']['rows']
    assert [r['source_trace']['sha256'] for r in rows]==['old','head']
    async def empty(*args):return {'records':[],'nextUntil':None}, {'sha256':'empty'}
    collector.client.get=empty
    await collector.fundamentals_of_trading('000660',('credit-trades',))
    assert store.get('toss:credit-trades:000660')['status']=='stale'
    assert len(store.get('toss-normalized:financing:000660')['credit']['rows'])==2


@pytest.mark.asyncio
async def test_stalled_trading_cursor_preserves_last_good(tmp_path):
    store, collector, _ = setup(tmp_path)
    original={'rows':[{'date':'2026-10-02'}],'next_until':'same-page'}
    store.put('toss:credit-trades:000660',original)
    async def stalled(*args):return {'records':[],'nextUntil':'same-page'}, {}
    collector.client.get=stalled
    with pytest.raises(ValueError,match='did not advance'):
        await collector.fundamentals_of_trading('000660',('credit-trades',),resume=True)
    assert store.get('toss:credit-trades:000660')==original


def test_symbol_failures_do_not_delay_other_securities():
    assert pause_seconds('error') < 1 and pause_seconds('item_backoff') < 1
    assert pause_seconds('backoff') == 30


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,key,phase',[
    ('history','bars:000660:통합:day',0),
    ('credit-trades','toss:credit-trades:000660',4),
])
async def test_completed_view_collection_repairs_waiting_job_without_provider_request(tmp_path,kind,key,phase):
    store,collector,queue=setup(tmp_path)
    previous={'status':'collecting','rows':1,'visits':12,'next_until':'old-page'}
    job='toss-backfill:000660:'+kind
    store.put(job,previous)
    store.put('toss-backfill-progress',{'cursor':0,'phase':phase,'attempts':1})
    store.put(key,{'status':'ready','row_count':42,'received_at':now(),
        'backfill_complete':True,'first_time':'2000-01-01','last_time':'2026-10-02'})
    assert await queue.step()=='ready'
    state=store.get(job)
    assert state['rows']==42 and state['backfill_complete'] and state['visits']==12
    assert state['failures']==0 and not state.get('next_until') and not state.get('next_before')
    assert store.get('backfill-status-review:000660:'+kind)==previous
    assert collector.client.calls==[]


@pytest.mark.asyncio
async def test_completion_flag_with_remaining_price_cursor_does_not_skip_collection(tmp_path):
    store,collector,queue=setup(tmp_path)
    store.put('bars:000660:통합:day',{'status':'ready','row_count':42,'received_at':now(),
        'backfill_complete':True,'next_before':'2020-01-02T15:30:00+09:00'})
    store.put('toss-backfill-progress',{'attempts':1})
    await queue.step()
    assert collector.client.calls and collector.client.calls[0][1]['before']=='2020-01-02T15:30:00+09:00'


@pytest.mark.asyncio
async def test_program_backfill_writes_only_new_page_and_reads_latest_summary(tmp_path, monkeypatch):
    from datetime import date, timedelta

    from services.market_dashboard.time_series import put_series
    store,collector,_=setup(tmp_path)
    key='toss:program-trades:000660'
    rows=[{'date':(date(2000,1,1)+timedelta(days=i)).isoformat(),
           'arbitrage':{'buyVolume':str(i)},'nonArbitrage':{'buyVolume':'1'},
           'source_trace':{'source':'fixture_non_production','sha256':'old'}} for i in range(5000)]
    put_series(store,key,{'rows':rows,'next_until':'old-page','backfill_complete':False},reverse=True)
    calls=[]; reads=[]
    real_upsert=store.time_series.upsert; real_rows=store.time_series.rows
    def upsert(key,values,**kwargs):
        calls.append(len(values));return real_upsert(key,values,**kwargs)
    def read(key,**kwargs):
        reads.append(kwargs.get('limit'));return real_rows(key,**kwargs)
    monkeypatch.setattr(store.time_series,'upsert',upsert)
    monkeypatch.setattr(store.time_series,'rows',read)
    async def page(*args):
        return {'records':[{'date':'1999-12-31','arbitrage':{'buyVolume':'5'},'nonArbitrage':{'buyVolume':'1'}}],
                'nextUntil':'2020-01-02T15:30:00+09:00'}, {'source':'fixture_non_production','sha256':'older'}
    collector.client.get=page
    await collector.fundamentals_of_trading('000660',('program-trades',),resume=True)
    assert calls==[1] and reads==[1]
    assert store.metadata(key)['row_count']==5001
    latest=store.get('trading:000660')
    assert latest['program']['buy_quantity']==5000
    assert latest['program_trace']['sha256']=='old'
    assert real_rows(key,start='1999-12-31',end='1999-12-31')[0]['source_trace']['sha256']=='older'


@pytest.mark.asyncio
async def test_old_trading_history_keeps_paginating_beyond_one_year(tmp_path):
    store,collector,queue=setup(tmp_path)
    store.put('toss-backfill-progress',{'cursor':0,'phase':1})
    store.put('toss:investor-trading:000660',{'rows':[{'date':'2019-01-02'}],
        'next_until':'2019-01-01','received_at':now(),'backfill_complete':False})
    await queue.step()
    assert collector.client.calls[0][1]['until']=='2019-01-01'


@pytest.mark.asyncio
async def test_viewed_history_gets_one_priority_turn_without_advancing_universe(tmp_path):
    store,collector,queue=setup(tmp_path)
    store.request('005930')
    store.put('trading-history-request:005930:program-trades',{'code':'005930','kind':'program-trades','at':now()})
    await queue.step()
    progress=store.get('toss-backfill-progress')
    assert collector.client.calls[0][0].endswith('/005930/program-trades')
    assert progress['cursor']==0 and progress['phase']==0
    await queue.step()
    assert store.get('toss-backfill-progress')['cursor']==0
    assert store.get('toss-backfill-progress')['phase']==1


@pytest.mark.asyncio
async def test_raw_and_adjusted_history_have_independent_cursors_and_sources(tmp_path):
    store,collector,_=setup(tmp_path)
    await collector.history('000660')
    await collector.history('000660',adjusted=False)
    assert collector.client.calls[-1][1]['adjusted']=='false'
    await collector.history('000660',adjusted=False,resume=True)
    raw=store.get('bars:000660:통합:raw')
    adjusted=store.get('bars:000660:통합:day')
    assert len(raw['rows'])==2 and raw['adjustment']=='raw'
    assert len(adjusted['rows'])==1 and adjusted['next_before']=='2020-01-02T15:30:00+09:00'
    assert all(r['source_trace']['source']=='fixture_non_production' for r in raw['rows'])


@pytest.mark.asyncio
async def test_daily_worker_never_rehydrates_normalized_history(tmp_path,monkeypatch):
    store,collector,_=setup(tmp_path)
    await collector.history('000660')
    original=store.get
    key='bars:000660:통합:day'
    def guarded(k,*args,**kwargs):
        assert k!=key, 'daily collection decoded the full history'
        return original(k,*args,**kwargs)
    monkeypatch.setattr(store,'get',guarded)
    result=await collector.history('000660',resume=True,compact=True)
    assert result['row_count']==2 and result['backfill_complete'] and 'rows' not in result
    assert result['first_time'].startswith('2020-01-02')
    async def empty(*args):return {'candles':[],'nextBefore':None}, {}
    collector.client.get=empty
    latest=await collector.history('000660',compact=True)
    assert latest['row_count']==2 and latest['status']=='stale'
    assert len(original(key)['rows'])==2


class LongHistoryClient(Client):
    async def get(self, path, params):
        self.calls.append((path, dict(params)))
        if not path.endswith('candles'):
            return {'records':[]}, {'source':'fixture_non_production'}
        page = (datetime.fromisoformat('2026-09-28T15:30:00+09:00') - datetime.fromisoformat(params['before'])).days if params.get('before') else 0
        return {'symbol': params['symbol'], 'candles': [
            {'timestamp': (datetime.fromisoformat('2026-09-28T15:30:00+09:00') - timedelta(days=page)).isoformat(),
             'openPrice': '10', 'closePrice': '11', 'highPrice': '12',
             'lowPrice': '9', 'volume': '20'}], 'nextBefore': (datetime.fromisoformat('2026-09-28T15:30:00+09:00') - timedelta(days=page+1)).isoformat()}, {'source': 'fixture_non_production', 'page': page}


@pytest.mark.asyncio
async def test_burst_is_bounded_and_next_security_is_not_starved(tmp_path, monkeypatch):
    monkeypatch.setenv('MARKET_TOSS_HISTORY_BURST', '8')
    store, collector, queue = setup(tmp_path, LongHistoryClient())
    await queue.step()
    assert len(collector.client.calls) == 8
    assert store.metadata('bars:000660:통합:day')['next_before'] == '2026-09-20T15:30:00+09:00'
    assert store.metadata('bars:000660:통합:day')['row_count'] == 8
    for _ in range(7):
        await queue.step()
    second = [params for path,params in collector.client.calls
              if path.endswith('candles') and params.get('symbol')=='005930']
    assert len(second)==8
    assert store.metadata('bars:005930:통합:day')['row_count']==8
    assert store.get('toss-backfill-progress')['last_code']=='005930'


@pytest.mark.asyncio
async def test_existing_archive_head_refresh_keeps_deep_cursor(tmp_path, monkeypatch):
    monkeypatch.setenv('MARKET_TOSS_HISTORY_BURST', '8')
    store, collector, queue = setup(tmp_path, LongHistoryClient())
    await collector.history('000660', pages=8)
    metadata = store.metadata('bars:000660:통합:day')
    store.put('bars:000660:통합:day', {**metadata, 'head_received_at': '2020-01-01T00:00:00+00:00'})
    collector.client.calls.clear()
    await queue.step()
    assert len(collector.client.calls) == 1
    assert store.metadata('bars:000660:통합:day')['next_before'] == '2026-09-20T15:30:00+09:00'
    assert store.metadata('bars:000660:통합:day')['row_count'] == 8


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['transport', 'security', 'stalled', 'cancel'])
async def test_failed_later_page_preserves_valid_pages_and_resume_cursor(tmp_path, failure):
    import asyncio
    client = LongHistoryClient()
    store, collector, _ = setup(tmp_path, client)
    original = client.get
    async def failing(path, params):
        if params.get('before') == '2026-09-26T15:30:00+09:00':
            if failure == 'transport':
                raise httpx.ReadError('fixture interruption')
            if failure == 'cancel':
                raise asyncio.CancelledError()
            data, evidence = await original(path, params)
            if failure == 'security':
                data['symbol'] = '999999'
            else:
                data['nextBefore'] = '2026-09-26T15:30:00+09:00'
            return data, evidence
        return await original(path, params)
    client.get = failing
    error = asyncio.CancelledError if failure == 'cancel' else Exception
    with pytest.raises(error):
        await collector.history('000660', pages=8)
    saved = store.metadata('bars:000660:통합:day')
    assert saved['status'] == 'partial'
    assert saved['row_count'] == 2 and saved['next_before'] == '2026-09-26T15:30:00+09:00'
    assert saved['source_trace']['page'] == 1
    assert not saved['backfill_complete']
    client.get = original
    client.calls.clear()
    await collector.history('000660', pages=2, resume=True)
    assert client.calls[0][1]['before'] == '2026-09-26T15:30:00+09:00'
    assert store.metadata('bars:000660:통합:day')['row_count'] == 4
    assert store.metadata('bars:000660:통합:day')['next_before'] == '2026-09-24T15:30:00+09:00'


class LongTradingClient(Client):
    async def get(self, path, params):
        self.calls.append((path, dict(params)))
        page = int(params.get('until', '0'))
        return {'records': [{'date': f'2026-09-{28-page:02d}',
            'individual': {'netBuyVolume': str(page)},
            'marginLoan': {'balanceQuantity': str(page+10)},
            'balanceQuantity': str(page+20), 'shortSellingVolume': str(page+30),
            'arbitrage': {'buyVolume': '10'}, 'nonArbitrage': {'buyVolume': '20'}}],
            'nextUntil': str(page+1)}, {'source':'fixture_non_production', 'page':page}


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['investor-trading','program-trades','short-selling','credit-trades','securities-lending'])
async def test_trading_burst_commits_each_page_and_projects_once(tmp_path, monkeypatch, kind):
    store, collector, _ = setup(tmp_path, LongTradingClient())
    projected = []
    real = collector._project_trading
    def project(code, endpoint, state):
        projected.append(endpoint)
        real(code, endpoint, state)
    monkeypatch.setattr(collector, '_project_trading', project)
    await collector.fundamentals_of_trading('000660', (kind,), pages=8)
    saved = store.metadata(f'toss:{kind}:000660')
    assert saved['row_count'] == 8 and saved['next_until'] == '8'
    assert len(collector.client.calls) == 8 and projected == [kind]
    assert not saved['backfill_complete']
    assert [r['source_trace']['page'] for r in store.time_series.rows(f'toss:{kind}:000660',reverse=True)] == list(range(8))


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['request', 'security', 'stalled'])
async def test_trading_burst_failure_updates_view_and_resumes_saved_page(tmp_path, failure):
    store, collector, _ = setup(tmp_path, LongTradingClient())
    original = collector.client.get
    async def fail(path, params):
        if params.get('until') == '2':
            if failure == 'request':
                raise httpx.ReadError('fixture transport')
            data, evidence = await original(path, params)
            if failure == 'security':
                data['symbol'] = '999999'
            else:
                data['nextUntil'] = '2'
            return data, evidence
        return await original(path, params)
    collector.client.get = fail
    with pytest.raises(Exception):
        await collector.fundamentals_of_trading('000660', ('credit-trades',), pages=8)
    saved = store.metadata('toss:credit-trades:000660')
    assert saved['row_count'] == 2 and saved['next_until'] == '2'
    assert len(store.get('financing:000660')['credit']['rows']) == 2
    collector.client.get = original
    collector.client.calls.clear()
    await collector.fundamentals_of_trading('000660', ('credit-trades',), pages=2, resume=True)
    assert collector.client.calls[0][1]['until'] == '2'
    assert store.metadata('toss:credit-trades:000660')['row_count'] == 4


@pytest.mark.asyncio
async def test_trading_queue_keeps_deep_cursor_on_head_visit_and_resumes_burst(tmp_path, monkeypatch):
    monkeypatch.setenv('MARKET_TOSS_TRADING_BURST','8')
    store, collector, queue = setup(tmp_path, LongTradingClient())
    await collector.fundamentals_of_trading('000660', ('credit-trades',), pages=8)
    key='toss:credit-trades:000660'
    store.put(key,{**store.metadata(key),'head_received_at':'2020-01-01T00:00:00+00:00'})
    store.put('toss-backfill-progress',{'cursor':0,'phase':4})
    collector.client.calls.clear()
    await queue.step()
    assert len(collector.client.calls)==1 and store.metadata(key)['next_until']=='8'
    store.put('toss-backfill-progress',{'cursor':0,'phase':4})
    collector.client.calls.clear()
    await queue.step()
    assert len(collector.client.calls)==8
    assert collector.client.calls[0][1]['until']=='8'
    assert store.metadata(key)['row_count']==16 and store.metadata(key)['next_until']=='16'


@pytest.mark.asyncio
async def test_pending_trading_pages_are_not_complete_jobs(tmp_path):
    store, collector, queue = setup(tmp_path, LongTradingClient())
    store.put('toss-backfill-progress', {'cursor':0, 'phase':4})
    assert await queue.step() == 'collecting'
    job = store.get('toss-backfill:000660:credit-trades')
    assert job['source_status'] == 'ready' and job['status'] == 'collecting'
    assert job['next_until'] and not job['backfill_complete']


@pytest.mark.asyncio
async def test_empty_final_old_page_finishes_but_empty_head_is_stale(tmp_path):
    from services.market_dashboard.toss_backfill import trading_collection_status
    store, collector, _ = setup(tmp_path, LongTradingClient())
    await collector.fundamentals_of_trading('000660', ('credit-trades',), pages=1)
    async def last_page(*args):
        return {'records': [], 'nextUntil': None}, {'source':'fixture_non_production'}
    collector.client.get = last_page
    await collector.fundamentals_of_trading('000660', ('credit-trades',), resume=True)
    final = store.metadata('toss:credit-trades:000660')
    assert final['row_count'] == 1 and final['backfill_complete']
    assert trading_collection_status(final) == 'ready'
    await collector.fundamentals_of_trading('000660', ('credit-trades',), resume=False)
    assert trading_collection_status(store.metadata('toss:credit-trades:000660')) == 'stale'


@pytest.mark.asyncio
async def test_repair_changes_only_interpretation_not_cursor_or_raw_data(tmp_path):
    from services.market_dashboard.toss_backfill import repair_trading_job_statuses
    store, _, _ = setup(tmp_path)
    pending = {'status':'ready', 'rows':100, 'next_until':'durable', 'backfill_complete':False}
    complete = {'status':'ready', 'rows':200, 'next_until':None, 'backfill_complete':True}
    store.put('toss-backfill:000660:credit-trades', pending)
    store.put('toss-backfill:005930:credit-trades', complete)
    store.put('toss:credit-trades:000660', {'rows':[{'date':'2025-01-01'}]})
    assert await repair_trading_job_statuses(store) == 1
    repaired = store.get('toss-backfill:000660:credit-trades')
    assert repaired['status'] == 'collecting' and repaired['next_until'] == 'durable'
    assert store.get('backfill-status-review:000660:credit-trades') == pending
    assert store.get('toss-backfill:005930:credit-trades') == complete
    assert store.get('toss:credit-trades:000660')['rows'] == [{'date':'2025-01-01'}]
    assert await repair_trading_job_statuses(store) == 0


@pytest.mark.asyncio
async def test_status_repair_archive_and_correction_roll_back_together(tmp_path, monkeypatch):
    from services.market_dashboard.toss_backfill import repair_trading_job_statuses
    store, _, _ = setup(tmp_path)
    key='toss-backfill:000660:credit-trades'
    original={'status':'ready','next_until':'old','backfill_complete':False}
    store.put(key,original)
    put=store.put
    def fail(key,payload,**kwargs):
        if key.startswith('toss-backfill:') and kwargs.get('_connection') is not None:
            raise RuntimeError('fixture interrupted repair')
        return put(key,payload,**kwargs)
    monkeypatch.setattr(store,'put',fail)
    with pytest.raises(RuntimeError): await repair_trading_job_statuses(store)
    assert store.get(key)==original
    assert store.get('backfill-status-review:000660:credit-trades') is None


@pytest.mark.asyncio
async def test_schedule_migration_keeps_exact_old_next_pair_and_wraps_fairly(tmp_path):
    store,collector,queue=setup(tmp_path)
    store.put('toss-backfill-progress', {'cursor':1,'phase':4,'cycles':2,'attempts':1})
    visited=[]
    normal_visits=[]
    for _ in range(20):
        before=store.get('toss-backfill-progress')
        await queue.step()
        progress=store.get('toss-backfill-progress')
        visited.append((progress['last_code'],progress['last_kind']))
        if progress.get('priority_cursor',0)==before.get('priority_cursor',0):
            normal_visits.append(visited[-1])
        if len(normal_visits)==14: break
    assert visited[0]==('005930','credit-trades')
    assert visited[1]==('005930','securities-lending')
    assert len(set(normal_visits))==14
    assert store.get('toss-backfill-progress')['schedule_version']==3
    assert (progress['cursor'],progress['phase'])==(1,4)


@pytest.mark.asyncio
async def test_one_security_error_does_not_starve_its_other_sources(tmp_path):
    store,collector,queue=setup(tmp_path,Client(fail=True))
    # A per-security 404, not a global authentication failure.
    async def fail_candle(path,params):
        if path.endswith('candles'):
            request=httpx.Request('GET','https://example.test'+path)
            raise httpx.HTTPStatusError('fixture',request=request,response=httpx.Response(404,request=request))
        return {'records':[]},{'source':'fixture_non_production'}
    collector.client.get=fail_candle
    assert await queue.step()=='error'
    assert await queue.step()=='no_data'
    progress=store.get('toss-backfill-progress')
    assert progress['last_code']=='000660' and progress['last_kind']=='investor-trading'
    assert store.get('toss-backfill:000660:history')['http_status']==404


@pytest.mark.asyncio
async def test_catalogue_omission_is_archived_and_not_retried_until_catalogue_changes(tmp_path):
    store,collector,queue=setup(tmp_path)
    for market in ('KOSPI','KOSDAQ','KR_ETC'):
        store.put('toss-listing:'+market,{'status':'ready','received_at':now(),'rows':[],
            'source_trace':{'source':'fixture_non_production'}})
    store.put('toss-backfill:000660:history',{'status':'error','http_status':404,'failures':2,
        'retry_after':None,'diagnosis':{'status':'catalogue_mismatch'}})

    assert await classify_provider_catalogue_omissions(store)==1
    state=store.get('toss-backfill:000660:history')
    assert state['status']=='not_in_provider_catalogue'
    revision=state['provider_catalogue_revision']
    assert store.get('backfill-status-review:000660:history')['http_status']==404

    store.put('toss-backfill-progress',{'next_code':'000660','phase':0,'attempts':1})
    assert await queue.step()=='not_in_provider_catalogue'
    assert collector.client.calls==[]
    assert store.get('toss-backfill:000660:history')['provider_catalogue_revision']==revision

    store.put('toss-listing:KOSPI',{'status':'ready','received_at':now(),
        'rows':[{'symbol':'000660'}],'source_trace':{'source':'fixture_non_production'}})
    store.put('toss-backfill-progress',{'next_code':'000660','phase':0,'attempts':2})
    assert await queue.step() in ('ready','collecting')
    assert collector.client.calls
    assert all('000660' in str(params) for _,params in collector.client.calls)
    assert all(path.endswith('/candles') for path,_ in collector.client.calls)

@pytest.mark.asyncio
async def test_new_listing_is_registered_and_existing_next_identity_is_preserved(tmp_path):
    store, collector, queue = setup(tmp_path)
    store.put('toss-backfill-progress', {'cursor':1,'next_code':'005930','phase':2,'attempts':1,'cycles':2})
    store.put('universe', {'rows':[{'code':c} for c in ['000660','003000','005930']]})
    await queue.step()
    progress=store.get('toss-backfill-progress')
    assert progress['last_code']=='005930' and progress['last_kind']=='program-trades'
    assert progress['next_code']=='005930' and progress['cursor']==2
    assert len(store.list_prefix('toss-backfill:'))==21
    assert store.get('toss-backfill:003000:history')['status']=='queued'

@pytest.mark.asyncio
async def test_queued_fresh_data_is_registered_without_requery_or_false_missing(tmp_path):
    store, collector, queue = setup(tmp_path)
    store.put('toss:investor-trading:000660', {'status':'ready','backfill_complete':True,
        'row_count':42,'rows':[], 'head_received_at':now()})
    store.put('toss-backfill-progress', {'cursor':0,'phase':1,'attempts':1})
    assert await queue.step()=='ready'
    assert collector.client.calls==[]
    assert store.get('toss-backfill:000660:investor-trading')['rows']==42
    assert store.get('toss-backfill:000660:investor-trading')['visits']==0

@pytest.mark.asyncio
async def test_missing_jobs_get_bounded_priority_and_do_not_rewrite_existing_cursors(tmp_path):
    store, collector, queue = setup(tmp_path)
    existing={'status':'collecting','next_before':'historic-cursor','rows':123}
    store.put('toss-backfill:005930:history', existing)
    store.put('toss-backfill-progress', {'cursor':1,'phase':4,'cycles':2,'attempts':5})
    await queue.step()
    progress=store.get('toss-backfill-progress')
    assert progress['priority_cursor']==1
    assert (progress['cursor'],progress['phase'])==(1,4)
    assert store.get('toss-backfill:005930:history')==existing
    assert store.get('toss-backfill:'+progress['last_code']+':'+progress['last_kind'])['status']!='queued'

@pytest.mark.asyncio
async def test_removed_next_listing_advances_to_successor_and_reconcile_preserves_jobs(tmp_path):
    store,collector,queue=setup(tmp_path)
    previous={'status':'error','retry_after':'2099-01-01T00:00:00+00:00','next_until':'old','rows':9}
    store.put('toss-backfill:005930:credit-trades',previous)
    store.put('toss-backfill-progress',{'next_code':'003000','cursor':0,'phase':4,'attempts':1})
    assert await queue.step()=='item_backoff'
    assert store.get('toss-backfill-progress')['last_code']=='005930'
    assert store.get('toss-backfill:005930:credit-trades')==previous
    store.put('universe',{'rows':[{'code':c} for c in ['000660','003000','005930']]})
    await queue.step()
    assert store.get('toss-backfill:005930:credit-trades')==previous
    assert len(store.list_prefix('toss-backfill:'))==21


@pytest.mark.asyncio
async def test_jobs_for_codes_outside_current_universe_are_archived_without_losing_data(tmp_path):
    store, collector, queue = setup(tmp_path)
    stale = {'status':'error', 'error':'HTTPStatusError', 'http_status':404,
             'diagnosis':{'status':'catalogue_mismatch'}, 'next_before':'2020-01-02T15:30:00+09:00',
             'rows':321, 'failures':3}
    store.put('toss-backfill:008000:history', stale)
    store.put('toss:history:008000', {'rows':[{'date':'2020-01-01','close':'123'}],
                                     'next_until':'2020-01-02T15:30:00+09:00'})

    await queue.reconcile(store.listed_codes())

    current = store.get('toss-backfill:008000:history')
    assert current['status'] == 'out_of_universe'
    assert current['source_status'] == 'error'
    assert current['next_before'] == '2020-01-02T15:30:00+09:00' and current['rows'] == 321
    archives = store.list_prefix('toss-backfill-scope-history:008000:history:')
    assert len(archives) == 1
    archived = next(iter(archives.values()))
    assert archived['reason'] == 'not_present_in_current_scoped_security_universe'
    assert archived['state'] == stale
    assert store.get('toss:history:008000')['rows'] == [{'date':'2020-01-01','close':'123'}]


@pytest.mark.asyncio
async def test_relisted_code_resumes_preserved_job_cursor(tmp_path):
    store, collector, queue = setup(tmp_path)
    old = {'status':'out_of_universe', 'source_status':'error', 'next_until':'2020-01-02T15:30:00+09:00',
           'rows':99, 'backfill_complete':False, 'scope_version':1}
    store.put('toss-backfill:003000:investor-trading', old)
    codes = ['000660', '003000', '005930']

    await queue.reconcile(codes)

    restored = store.get('toss-backfill:003000:investor-trading')
    assert restored['status'] == 'queued'
    assert restored['next_until'] == '2020-01-02T15:30:00+09:00' and restored['rows'] == 99
    assert restored['reactivated_at']


@pytest.mark.asyncio
@pytest.mark.parametrize(('kind', 'suffix', 'phase', 'adjusted'), [
    ('history', 'day', 0, True),
    ('raw-history', 'raw', 6, False),
])
async def test_empty_latest_price_page_keeps_job_stale_after_complete_history(
    tmp_path, kind, suffix, phase, adjusted,
):
    store, collector, queue = setup(tmp_path)
    key = f'bars:000660:통합:{suffix}'
    job = f'toss-backfill:000660:{kind}'
    await collector.history('000660', pages=2, adjusted=adjusted)
    previous_rows = store.get(key)['rows']
    snapshot = store.metadata(key)
    assert snapshot['backfill_complete'] and snapshot['row_count'] == 2
    store.put(key, {**snapshot, 'head_received_at': '2020-01-01T00:00:00+00:00'})
    original_get = collector.client.get
    empty_calls = []

    async def empty_head(path, params):
        empty_calls.append((path, params))
        return {'candles': [], 'nextBefore': None}, {'source': 'fixture_non_production'}

    collector.client.get = empty_head
    store.put('toss-backfill-progress', {'cursor': 0, 'phase': phase, 'attempts': 1})
    assert await queue.step() == 'stale'
    state = store.get(job)
    assert state['status'] == state['source_status'] == 'stale'
    assert state['backfill_complete'] and state['next_before'] is None
    assert state['rows'] == 2 and store.get(key)['rows'] == previous_rows

    # A same-day visit must not promote the stored stale snapshot to ready.
    # The normal head-refresh cadence is retained; this does not add retries.
    old_job = {**state, 'status': 'ready', 'source_status': 'ready'}
    store.put(job, old_job)
    store.put('toss-backfill-progress', {'cursor': 0, 'phase': phase, 'attempts': 2})
    assert await queue.step() == 'stale'
    assert len(empty_calls) == 1
    assert store.get(job)['source_status'] == 'stale'
    assert store.get(f'backfill-status-review:000660:{kind}') == old_job
    assert store.get(key)['rows'] == previous_rows

    # A later validated head response can recover readiness normally.
    collector.client.get = original_get
    store.put(key, {**store.metadata(key), 'head_received_at': '2020-01-01T00:00:00+00:00'})
    store.put('toss-backfill-progress', {'cursor': 0, 'phase': phase, 'attempts': 3})
    assert await queue.step() == 'ready'
    assert store.get(job)['source_status'] == 'ready'
    assert store.get(key)['rows'] == previous_rows


@pytest.mark.asyncio
@pytest.mark.parametrize(('kind', 'suffix', 'phase', 'adjusted'), [
    ('history', 'day', 0, True),
    ('raw-history', 'raw', 6, False),
])
async def test_empty_final_old_price_page_finishes_the_job_without_erasing_rows(
    tmp_path, kind, suffix, phase, adjusted,
):
    store, collector, queue = setup(tmp_path)
    key = f'bars:000660:통합:{suffix}'
    await collector.history('000660', adjusted=adjusted)
    previous_rows = store.get(key)['rows']
    observed = []

    async def empty_last_page(path, params):
        observed.append(params)
        return {'candles': [], 'nextBefore': None}, {'source': 'fixture_non_production'}

    collector.client.get = empty_last_page
    store.put('toss-backfill-progress', {'cursor': 0, 'phase': phase, 'attempts': 1})
    assert await queue.step() == 'ready'
    assert observed[0]['before'] == '2020-01-02T15:30:00+09:00'
    state = store.get(f'toss-backfill:000660:{kind}')
    assert state['status'] == state['source_status'] == 'ready'
    assert state['backfill_complete'] and state['next_before'] is None
    assert state['rows'] == 1 and store.get(key)['rows'] == previous_rows


@pytest.mark.parametrize('source_status', ['stale', 'partial', 'error'])
@pytest.mark.parametrize('complete', [False, True])
def test_explicit_price_source_failure_is_not_promoted_by_rows_or_old_cursor(
    source_status, complete,
):
    snapshot = {'status': source_status, 'row_count': 500,
                'backfill_complete': complete, 'next_before': None if complete else '2020-01-02T15:30:00+09:00'}
    assert price_collection_status(snapshot) == source_status


@pytest.mark.parametrize(('snapshot', 'expected'), [
    ({'status': 'no_data', 'backfill_complete': True}, 'no_data'),
    ({'status': 'no_data', 'backfill_complete': False, 'next_before': 'older'}, 'collecting'),
    ({'status': 'ready', 'row_count': 5, 'backfill_complete': True,
      'next_before': 'older'}, 'collecting'),
    ({'row_count': 5, 'backfill_complete': True}, 'ready'),
])
def test_price_completion_retains_cursor_and_legacy_snapshot_contract(snapshot, expected):
    assert price_collection_status(snapshot) == expected
