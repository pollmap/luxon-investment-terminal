"""Synthetic documents exercise class/date evidence; not production data."""
import io
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from services.market_dashboard.corporate_events import parse_dividend_document, collect_events, PARSER_VERSION
from services.market_dashboard.store import Store,now

def document(extra=''):
    return ('''<table><tr><td>3. 1주당 배당금(원)</td><td>보통주식</td><td>374</td></tr>
    <tr><td>종류주식</td><td>375</td></tr>
    <tr><td>6. 배당기준일</td><td>2026-06-30</td></tr>
    <tr><td>7. 배당금지급 예정일자</td><td>2026-08-28</td></tr>
    <tr><td>배당락일</td><td>2026-06-29</td></tr>'''+extra+'</table>').encode()

def parse(content,**args):
    return parse_dividend_document(content,code='005930',filing_id='20260901000001',
        published_at='2026-09-01',evidence={'source':'fixture_non_production'},**args)

def test_declared_payment_cannot_become_cash_even_after_the_calendar_date():
    row=parse(document())[0]
    assert row['payment_date']=='2026-08-28' and row['payment_status']=='announced'
    assert not row['verified'] and row['status']=='payment_confirmation_required'
    confirmed=parse(document('<tr><td>실제 지급일</td><td>2026-08-31</td></tr>'))[0]
    assert confirmed['payment_date']=='2026-08-31' and confirmed['scheduled_payment_date']=='2026-08-28'
    assert confirmed['payment_status']=='confirmed' and confirmed['verified']

def test_share_class_is_exact_and_unknown_preferred_issue_is_not_guessed():
    assert parse(document())[0]['amount']=='374'
    assert parse(document(),share_label='종류주식')[0]['amount']=='375'
    assert parse(document(),share_label='2우선주식')==[]
    assert parse(document().replace('보통주식'.encode(),'보통주'.encode()))[0]['amount']=='374'

def test_conflicting_dates_or_future_payment_confirmation_are_rejected():
    with pytest.raises(ValueError,match='predates'):
        parse(document('<tr><td>지급 완료일</td><td>2026-09-10</td></tr>'))
    with pytest.raises(ValueError,match='Ex date'):
        parse(document().replace(b'2026-06-29',b'2026-07-01'))

def test_correction_comparison_and_preferred_totals_are_not_per_share_amounts():
    correction='<table><tr><td>3. 1주당 배당금(원) - 보통주식</td><td>500</td><td>650</td></tr></table>'.encode()
    annex=('<table><tr><td>종류주식명</td><td>종류주식구분</td><td>1주당 배당금(원)</td><td>배당금총액(원)</td></tr>'
        '<tr><td>우선주</td><td>1우선주</td><td>375</td><td>227,757,150</td></tr></table>').encode()
    assert parse(correction+document()+annex)[0]['amount']=='374'
    assert parse(correction+document()+annex,share_label='종류주식')[0]['amount']=='375'
    with pytest.raises(ValueError,match='Ambiguous'):
        parse(document()+document())

@pytest.mark.asyncio
async def test_parser_upgrade_reuses_hash_verified_archive_without_api_request(tmp_path):
    s=Store('sqlite://',tmp_path)
    receipt='20260901000001';code='005930'
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as z:
        z.writestr('report.xml',document())
    digest=s.raw('dart-events','fixture_non_production',buffer.getvalue())
    s.put('corporate-event-index:'+code,{'rows':[{'receipt':receipt,'title':'현금배당결정','date':'20260901'}],
        'status':'ready','received_at':now()})
    s.put('corporate-events-job:'+code,{'documents':{receipt:{'status':'parsed','raw_hash':digest}},
        'complete':True,'checked_at':now()})
    source=SimpleNamespace(store=s,dart='fixture',corporations=AsyncMock(return_value={code:'fixture'}),
        _dart_get=AsyncMock(side_effect=AssertionError('must use immutable archive')))
    await collect_events(source,code)
    assert s.get('corporate-events-job:'+code)['parser_version']==PARSER_VERSION
    assert s.get('corporate-events-job:'+code)['complete']
    assert s.get('events:'+code)['rows'][0]['amount']=='374'
    assert not s.get('events:'+code)['coverage']['verified']
    source._dart_get.assert_not_called()
    await collect_events(source,code)
    source._dart_get.assert_not_called()

@pytest.mark.asyncio
async def test_budget_wait_and_stale_index_can_replay_without_freshness_promotion(tmp_path):
    s=Store('sqlite://',tmp_path)
    receipt='20260901000001';code='005930'
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as z:
        z.writestr('report.xml',document())
    digest=s.raw('dart-events','fixture_non_production',buffer.getvalue())
    index={'rows':[{'receipt':receipt,'title':'현금배당결정','date':'20260901'}],
        'status':'partial','received_at':'2025-01-01T00:00:00+00:00','next_page':7}
    s.put('corporate-event-index:'+code,index)
    s.put('corporate-events-job:'+code,{'documents':{receipt:{'status':'parsed','raw_hash':digest}},
        'complete':False,'checked_at':'2025-01-01T00:00:00+00:00',
        'error':'DartDailyBudgetExceeded','retry_after':'2099-01-01T00:00:00+00:00'})
    source=SimpleNamespace(store=s,dart='fixture',corporations=AsyncMock(side_effect=AssertionError('no authentication')),
        _dart_get=AsyncMock(side_effect=AssertionError('no provider request')))
    await collect_events(source,code)
    job=s.get('corporate-events-job:'+code)
    assert job['documents'][receipt]['parser_version']==PARSER_VERSION
    assert job['retry_after']=='2099-01-01T00:00:00+00:00'
    assert job['error']=='DartDailyBudgetExceeded' and not job['complete']
    assert job['checked_at']=='2025-01-01T00:00:00+00:00'
    assert s.get('corporate-event-index:'+code)==index
    assert s.get('events:'+code)['rows'][0]['amount']=='374'
    source._dart_get.assert_not_called();source.corporations.assert_not_called()

@pytest.mark.asyncio
async def test_explicit_archive_replay_never_downloads_missing_document(tmp_path):
    s=Store('sqlite://',tmp_path);code='005930';receipt='20260901000001'
    s.put('corporate-event-index:'+code,{'rows':[{'receipt':receipt,'title':'현금배당결정','date':'20260901'}],
        'status':'ready','received_at':'2025-01-01T00:00:00+00:00'})
    source=SimpleNamespace(store=s)
    await collect_events(source,code,archive_only=True)
    assert s.get('corporate-events-job:'+code,{})=={}
    assert s.get('events:'+code,{})=={}


@pytest.mark.asyncio
async def test_archive_replay_reconciles_new_dates_when_all_documents_are_already_parsed(tmp_path):
    """fixture_non_production: evidence-only refresh must not redownload."""
    from copy import deepcopy
    s=Store('sqlite://',tmp_path);code='005930';receipt='20260901000001'
    event=parse(document().replace('<tr><td>배당락일</td><td>2026-06-29</td></tr>'.encode(),b''))[0]
    index={'rows':[{'receipt':receipt,'title':'현금배당결정','date':'20260901'}],
        'status':'partial','received_at':'2025-01-01T00:00:00+00:00','next_page':3}
    job={'documents':{receipt:{'status':'parsed','parser_version':PARSER_VERSION}},
        'complete':False,'parser_version':PARSER_VERSION,'checked_at':'2025-01-01T00:00:00+00:00',
        'retry_after':'2099','error':'DartProviderUnavailable','observation_revision':'old'}
    s.put('corporate-event-index:'+code,index)
    s.put('corporate-events-job:'+code,job)
    snapshot={'rows':[event],'received_at':'2025-01-01T00:00:00+00:00','status':'partial',
        'coverage':{'verified':False,'reason':'ex_dates_and_all_actions_required'}}
    s.put('events:'+code,snapshot)
    observation={k:deepcopy(event[k]) for k in ('security_code','share_class','record_date','amount','currency')}
    observation.update(id='fixture-proof',reviewed=True,ex_date='2026-06-29',
        confirmed_payment_date='2026-08-31',source_trace={'source':'fixture_non_production',
        'source_url':'https://example.test/payment','sha256':'a'*64,'available_at':'2026-09-01'})
    s.put('dividend-observations:'+code,{'rows':[observation]})
    source=SimpleNamespace(store=s,corporations=AsyncMock(side_effect=AssertionError('no provider')),
        _dart_get=AsyncMock(side_effect=AssertionError('no provider')))
    await collect_events(source,code,archive_only=True)
    saved=s.get('events:'+code)
    row=saved['rows'][0]
    assert row['payment_date']=='2026-08-31' and row['payment_status']=='confirmed'
    assert row['ex_date']=='2026-06-29' and row['scheduled_payment_date']=='2026-08-28'
    assert row['verified'] and not saved['coverage']['verified']
    assert saved['received_at']==snapshot['received_at']
    assert s.get('corporate-event-index:'+code)==index
    state=s.get('corporate-events-job:'+code)
    for key in ('error','retry_after','complete','checked_at','documents'):
        assert state[key]==job[key]
    source.corporations.assert_not_awaited();source._dart_get.assert_not_awaited()
    await collect_events(source,code,archive_only=True)
    assert s.get('events:'+code)==saved and s.get('corporate-events-job:'+code)==state
    s.engine.dispose()

