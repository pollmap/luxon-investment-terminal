import ast
from pathlib import Path

import pytest

from services.market_dashboard.corporate_events import parse_dividend_document
from services.market_dashboard.retire_providers import migrate
from services.market_dashboard.store import Store
from services.market_dashboard.time_series import put_series


def test_no_retired_imports_or_hosts_in_runtime():
    root=Path(__file__).parents[1]/'services/market_dashboard'
    for file in root.glob('*.py'):
        tree=ast.parse(file.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node,ast.ImportFrom):
                assert not any(name in (node.module or '') for name in ('kiwoom','kis_supplements'))
        assert 'api.kiwoom.com' not in file.read_text(encoding='utf-8')
        assert 'openapi.koreainvestment.com' not in file.read_text(encoding='utf-8')

def test_retirement_preserves_dart_toss_and_public_fields_and_can_recover(tmp_path):
    store=Store('sqlite://',tmp_path)
    store.put('detail:005930',{'company':{'name':'fixture'},'brokers':[{'member':'old'}], 'brokers_trace':{'source':'kis'},'flows':[{'date':'2025-01-01','source_trace':{'source':'toss'}}]})
    store.put('official:005930',{'company':{'source_trace':{'source':'opendart'}}})
    store.put('provider-kiwoom',{'status':'ready'})
    dry=migrate(store)
    assert dry['deleted']==['provider-kiwoom'] and dry['updated']==['detail:005930']
    assert store.get('detail:005930')['brokers']
    migrate(store,apply=True,backup_sha256='a'*64)
    assert store.get('provider-kiwoom') is None
    assert store.get('detail:005930')['flows'] and 'brokers' not in store.get('detail:005930')
    assert store.get('official:005930')['company']
    assert len(store.list_prefix('retirement:'))==3

def test_series_migration_keeps_all_rows_and_updates_only_changed_observations(tmp_path):
    store=Store('sqlite://',tmp_path)
    key='bars:005930:통합:day'
    rows=[{'date':'2025-01-01','close':10},{'date':'2025-01-02','close':20}]
    put_series(store,key,{'rows':rows,'status':'ready'})
    assert store.get(key)['rows']==rows
    assert store.time_series.upsert(key,rows)==0
    assert store.time_series.upsert(key,[{**rows[0],'source_trace':{'collected_at':'new'}}])==0
    put_series(store,key,{'rows':rows+[{'date':'2025-01-03','close':30}],'status':'ready'})
    assert len(store.get(key)['rows'])==3
    assert store.get(key)['row_count']==3

def test_dividend_parser_never_turns_record_date_into_ex_date():
    document='''<table><tr><td>3. 1주당 배당금(원)</td><td>보통주식</td><td>374</td></tr><tr><td>종류주식</td><td>375</td></tr><tr><td>6. 배당기준일</td><td>2026-06-30</td></tr><tr><td>7. 배당금지급 예정일자</td><td>2026-08-28</td></tr></table>'''.encode()
    rows=parse_dividend_document(document,code='005930',filing_id='fixture',published_at='2026-07-30',evidence={'source':'fixture'})
    assert rows[0]['amount']=='374' and rows[0]['payment_date']=='2026-08-28'
    assert rows[0].get('ex_date') is None and rows[0]['verified'] is False
    assert rows[0]['date_kind']=='payment' and rows[0]['payment_status']=='announced'

def test_priority_corporate_queue_cannot_skip_four_fifths_of_the_universe(tmp_path):
    from services.market_dashboard.corporate_events import next_target
    store=Store('sqlite://',tmp_path)
    codes=[f'{i:06d}' for i in range(10)]
    store.put('universe',{'rows':[{'code':c} for c in codes]})
    store.request(codes[-1])
    visited=[next_target(store) for _ in range(50)]
    assert set(visited)==set(codes)
    assert visited.count(codes[-1])==41

def test_partial_dart_indexes_resume_without_reducing_universe_round_robin(tmp_path):
    from services.market_dashboard.corporate_events import next_target
    store=Store('sqlite://',tmp_path)
    codes=[f'{i:06d}' for i in range(10)]
    store.put('universe',{'rows':[{'code':code} for code in codes]})
    store.request(codes[-1])
    for code in codes[:2]:
        store.put('corporate-event-index:'+code,{'status':'partial','next_page':2})

    visited=[next_target(store) for _ in range(50)]
    universe_turns=visited[::5]
    partial_turns=visited[4::5]
    assert set(universe_turns)==set(codes)
    assert set(partial_turns)==set(codes[:2])
    assert visited.count(codes[-1])==31  # 30 active slots plus its universe slot

def test_partial_dart_retry_wait_does_not_block_other_continuations(tmp_path):
    from datetime import datetime, timedelta, timezone

    from services.market_dashboard.corporate_events import next_target
    store=Store('sqlite://',tmp_path)
    codes=['000001','000002','000003']
    store.put('universe',{'rows':[{'code':code} for code in codes]})
    store.put('corporate-event-index:000001',{'status':'partial','next_page':2})
    store.put('corporate-event-index:000002',{'status':'partial','next_page':2})
    retry_after=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    store.put('corporate-events-job:000001',{'retry_after':retry_after})
    store.request(codes[-1])

    for _ in range(4):
        next_target(store)
    assert next_target(store)=='000002'

def test_partial_dart_queue_does_not_take_universe_slot_without_active_requests(tmp_path):
    from services.market_dashboard.corporate_events import next_target
    store=Store('sqlite://',tmp_path)
    codes=['000001','000002','000003']
    store.put('universe',{'rows':[{'code':code} for code in codes]})
    store.put('corporate-event-index:000001',{'status':'partial','next_page':2})

    for _ in range(4):
        next_target(store)
    selected=next_target(store)
    cursor=store.get('corporate-events-cursor',{})
    assert selected in codes
    assert cursor['universe_cursor']==5
    assert 'partial_after' not in cursor

def test_subsidiary_dividend_cannot_become_holding_company_dividend():
    from services.market_dashboard.corporate_events import is_issuer_action, issuer_events
    items={'foreign':{'title':'현금ㆍ현물배당결정(자회사의 주요경영사항)'},'own':{'title':'[기재정정]현금ㆍ현물배당결정'}}
    rows=[{'id':'1','filing_id':'foreign'},{'id':'2','filing_id':'own'}]
    kept,quarantined=issuer_events({'rows':rows},items)
    assert kept==[rows[1]] and quarantined==[rows[0]]
    assert not is_issuer_action('유상증자결정(종속회사의 주요경영사항)')

def test_related_event_repair_preserves_evidence_and_is_idempotent(tmp_path):
    from services.market_dashboard.corporate_events import repair_related_events
    store=Store('sqlite://',tmp_path)
    store.put('corporate-event-index:123456',{'rows':[{'receipt':'r','title':'현금배당결정(자회사의 주요경영사항)'}]})
    original={'id':'event','filing_id':'r','amount':'100'}
    store.put('events:123456',{'rows':[original]})
    assert repair_related_events(store)==[{'code':'123456','quarantined':1}]
    assert store.get('events:123456')['rows']==[]
    assert store.get('corporate-event-quarantine:123456')['rows']==[original]
    assert repair_related_events(store)==[]

def test_series_cursor_and_observations_roll_back_together(tmp_path,monkeypatch):
    store=Store('sqlite://',tmp_path)
    key='history:fixture'
    put_series(store,key,{'rows':[{'date':'2025-01-01','close':10}],'next_before':'2025-01-01'})
    def fail_metadata(*args,**kwargs):
        raise RuntimeError('fixture disk failure')
    monkeypatch.setattr(store,'put',fail_metadata)
    with pytest.raises(RuntimeError):
        put_series(store,key,{'rows':[{'date':'2024-12-30','close':9}],'next_before':'2024-12-30'})
    value=store.get(key)
    assert value['next_before']=='2025-01-01' and len(value['rows'])==1

def test_same_size_correction_changes_series_revision_without_losing_history(tmp_path):
    store=Store('sqlite://',tmp_path)
    key='history:fixture'
    put_series(store,key,{'rows':[{'date':'2025-01-01','close':10},{'date':'2025-01-02','close':20}]})
    revision=store.get(key)['series_revision']
    put_series(store,key,{'rows':[{'date':'2025-01-01','close':11}]})
    value=store.get(key)
    assert value['series_revision']!=revision and value['row_count']==2
    assert [r['close'] for r in value['rows']]==[11,20]
