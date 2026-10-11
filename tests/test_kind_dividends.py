from copy import deepcopy
from types import SimpleNamespace
import httpx
import pytest

from services.market_dashboard.kind_dividends import NOTICES, parse_notice, collect_kind_dividends
import hashlib
from services.market_dashboard.store import Store

NOTICE = '<table><tr><td>1. 회사명 현대자동차</td></tr><tr><td>1우선주 197,100</td></tr><tr><td>3. 사유 중간(분기)배당락(기준가격 변동없음)</td></tr><tr><td>4. 적용일 2025-11-27</td></tr></table>'
TRACE = {'source': 'hyundai_motor_ir', 'source_url': 'https://example.test/ir',
         'sha256': 'a' * 64, 'available_at': '2026-10-04T00:00:00+00:00'}


@pytest.mark.parametrize('old,new', [('현대자동차','다른회사'), ('1우선주','보통주'),
    ('2025-11-27','2025-11-28'), ('3. 사유','3. 다른항목')])
def test_notice_rejects_identity_date_or_schema_changes(old,new):
    with pytest.raises(ValueError):
        parse_notice(NOTICE.replace(old,new).encode(),NOTICES[0],TRACE)


@pytest.mark.asyncio
async def test_exact_preferred_entitlement_and_failure_preservation(tmp_path):
    store=Store('sqlite://',tmp_path)
    store.put('universe',{'rows':[{'code':'005385','name':'현대차우'}]})
    obs={'id':'issuer','security_code':'005385','share_class':'preferred','share_series':1,
        'record_date':'2025-11-30','amount':'2500','currency':'KRW','confirmed_payment_date':'2025-12-31',
        'reviewed':True,'review_method':'hyundai-dividend-table-v3','source_trace':TRACE}
    store.put('dividend-observations:005385',{'rows':[obs]})
    event={**obs,'kind':'dividend','payment_date':'2025-12-31','payment_status':'confirmed','verified':False}
    store.put('events:005385',{'rows':[event],'coverage':{'verified':False}})
    store.put('events:005380',{'rows':[{'kind':'dividend','verified':False}]})
    common=deepcopy(store.get('events:005380'))
    calls=[]
    async def get(url):
        calls.append(url)
        return httpx.Response(200,request=httpx.Request('GET',url),content=NOTICE.encode())
    source=SimpleNamespace(store=store,client=SimpleNamespace(get=get))
    await collect_kind_dividends(source,'005385')
    await collect_kind_dividends(source,'005385')
    await collect_kind_dividends(source,'005380')
    assert len(calls)==1
    result=store.get('events:005385')['rows'][0]
    assert result['ex_date']=='2025-11-27' and result['verified']
    assert not store.get('events:005385')['coverage']['verified']
    assert store.get('events:005380')==common
    saved=store.get('events:005385')
    key='kind-dividends-job:005385:'+hashlib.sha256(NOTICES[0]['url'].encode()).hexdigest()[:16]
    store.put(key,{})
    async def fail(url): raise httpx.ReadError('fixture error')
    source.client.get=fail
    await collect_kind_dividends(source,'005385')
    assert store.get('events:005385')==saved
    assert store.get(key)['status']=='error'


def test_binding_does_not_select_nearby_or_other_series():
    from services.market_dashboard.kind_dividends import reconcile_notice
    for series,record in [(2,'2025-11-30'),(1,'2025-11-29')]:
        with pytest.raises(ValueError):
            reconcile_notice(NOTICES[0],'2025-11-27',TRACE,[{
                'security_code':'005385','share_class':'preferred','share_series':series,
                'record_date':record,'currency':'KRW','reviewed':True,
                'review_method':'hyundai-dividend-table-v3','source_trace':TRACE,
                'amount':'2500','confirmed_payment_date':'2025-12-31'}])


@pytest.mark.parametrize('binding', NOTICES)
def test_reviewed_security_labels_are_distinct(binding):
    notice=NOTICE.replace('현대자동차',binding['company']).replace('1우선주',binding['share_label']).replace('2025-11-27',binding['ex_date'])
    assert parse_notice(notice.encode(),binding,TRACE)==binding['ex_date']
    for other in ('보통주식','1우선주','2우선주B','3우선주B'):
        if other!=binding['share_label']:
            with pytest.raises(ValueError):
                parse_notice(notice.replace(binding['share_label'],other).encode(),binding,TRACE)


@pytest.mark.asyncio
async def test_multiple_documents_have_independent_retry_checkpoints(tmp_path,monkeypatch):
    import services.market_dashboard.kind_dividends as module
    first=NOTICES[0]
    second={**first,'url':'https://example.test/second','ex_date':'2025-08-28','record_date':'2025-08-31'}
    monkeypatch.setattr(module,'NOTICES',(first,second))
    store=Store('sqlite://',tmp_path)
    store.put('universe',{'rows':[{'code':first['code'],'name':first['name']}]})
    observations=[{'id':'issuer-'+str(i),'security_code':b['code'],'share_class':'preferred','share_series':1,
        'record_date':b['record_date'],'amount':'2500','currency':'KRW','confirmed_payment_date':'2025-12-31',
        'reviewed':True,'review_method':'hyundai-dividend-table-v3','source_trace':TRACE} for i,b in enumerate((first,second))]
    store.put('dividend-observations:005385',{'rows':observations})
    store.put('events:005385',{'rows':[{**o,'kind':'dividend','payment_date':o['confirmed_payment_date'],
        'payment_status':'confirmed','verified':False} for o in observations],'coverage':{'verified':False}})
    calls=[]
    async def get(url):
        calls.append(url)
        b=first if url==first['url'] else second
        return httpx.Response(200,request=httpx.Request('GET',url),content=NOTICE.replace('2025-11-27',b['ex_date']).encode())
    source=SimpleNamespace(store=store,client=SimpleNamespace(get=get))
    await collect_kind_dividends(source,'005385')
    await collect_kind_dividends(source,'005385')
    assert calls==[first['url'],second['url']]
    assert all(row['verified'] for row in store.get('events:005385')['rows'])
    assert not store.get('events:005385')['coverage']['verified']
    assert len(store.list_prefix('kind-dividends-job:005385:'))==2


def test_reviewed_dart_ex_date_does_not_need_or_invent_payment_confirmation():
    from services.market_dashboard.kind_dividends import reconcile_notice
    from services.market_dashboard.dividend_evidence import enrich_dividends
    binding=next(row for row in NOTICES if row['code']=='000660')
    event={'id':'dart','kind':'dividend','filing_id':binding['filing_id'],
        'security_code':'000660','share_class':'ordinary','record_date':'2025-05-31',
        'currency':'KRW','amount':'375','payment_status':'announced','verified':False,
        'source_trace':{**TRACE,'source':'opendart'}}
    observation=reconcile_notice(binding,binding['ex_date'],TRACE,[],[event])
    result=enrich_dividends([event],[observation])[0]
    assert result['ex_date']=='2025-05-29'
    assert result['status']=='payment_confirmation_required' and not result['verified']
    assert not result.get('payment_date')
    for field,value in [('record_date','2025-05-30'),('security_code','005930'),
                        ('share_class','preferred'),('filing_id','other'),('superseded_by','correction')]:
        with pytest.raises(ValueError):
            reconcile_notice(binding,binding['ex_date'],TRACE,[],[{**event,field:value}])
