"""Fixture-only reviewed issuer table validation and reconciliation."""
from types import SimpleNamespace
import httpx
import pytest
from services.market_dashboard.issuer_dividends import HEADERS, parse_history, collect_issuer_dividends
from services.market_dashboard.store import Store


def html(records):
    return ('<table><tr>'+''.join('<th>'+v+'</th>' for v in HEADERS)+'</tr>'+''.join(
        '<tr>'+''.join('<td>'+v+'</td>' for v in row)+'</tr>' for row in records)+'</table>').encode()


def trace():
    return {'available_at':'2026-10-04T00:00:00+00:00','source':'fixture_non_production',
            'source_url':'https://example.test/issuer','sha256':'a'*64}


def test_historical_payment_is_common_class_and_future_is_not_confirmed():
    data=html([['2025.02.28','2025.04.18','6,000','6,050','6,100','6,050'],
               ['2026.11.30','2026.12.31','2,500','2,500','2,500','2,500']])
    result=parse_history(data,trace())
    assert len(result)==1 and result[0]['amount']=='6000'
    assert result[0]['share_class']=='ordinary' and result[0]['security_code']=='005380'
    assert result[0]['confirmed_payment_date']=='2025-04-18' and 'ex_date' not in result[0]


@pytest.mark.parametrize('row', [
    ['2025.02.28','2025.02.27','6000','6050','6100','6050'],
    ['2025.02.30','2025.04.18','6000','6050','6100','6050'],
    ['2025.02.28','2025.04.18','NaN','6050','6100','6050']])
def test_invalid_rows_rejected(row):
    with pytest.raises(ValueError): parse_history(html([row]),trace())


def test_changed_headers_and_conflicting_rows_are_not_silently_imported():
    row=['2025.02.28','2025.04.18','6000','6050','6100','6050']
    with pytest.raises(ValueError): parse_history(html([row]).replace(b'Common share',b'Preferred share'),trace())
    with pytest.raises(ValueError): parse_history(html([row,[*row[:2],'7000',*row[3:]]]),trace())


@pytest.mark.asyncio
async def test_collect_reconciles_exact_event_and_failure_preserves_good_data(tmp_path):
    store=Store('sqlite://',tmp_path)
    store.put('universe',{'rows':[{'code':'005380','name':'현대차'}]})
    event={'id':'dart','kind':'dividend','security_code':'005380','share_class':'ordinary',
           'record_date':'2025-02-28','amount':'6000','currency':'KRW','verified':False,'payment_status':'announced'}
    store.put('events:005380',{'rows':[event],'coverage':{'verified':False}})
    class Client:
        calls=0
        async def get(self,url):
            self.calls+=1
            return httpx.Response(200,request=httpx.Request('GET',url),content=html([
                ['2025.02.28','2025.04.18','6000','6050','6100','6050']]))
    client=Client();source=SimpleNamespace(store=store,client=client)
    await collect_issuer_dividends(source,'005380')
    await collect_issuer_dividends(source,'005380')
    assert client.calls==1
    row=store.get('events:005380')['rows'][0]
    assert row['payment_status']=='confirmed' and row['payment_date']=='2025-04-18'
    assert not row['verified'] and row['status']=='ex_date_required'
    assert not store.get('events:005380')['coverage']['verified']
    saved=store.get('dividend-observations:005380')
    store.put('issuer-dividends-job:005380',{})
    async def fail(url): raise httpx.ReadError('fixture failure')
    client.get=fail
    await collect_issuer_dividends(source,'005380')
    assert store.get('dividend-observations:005380')==saved
    assert store.get('issuer-dividends-job:005380')['status']=='error'


@pytest.mark.parametrize('code,amount,series', [('005385','6050',1),('005387','6100',2),('005389','6050',3)])
def test_preferred_series_uses_own_exact_column(code,amount,series):
    result=parse_history(html([['2025.02.28','2025.04.18','6000','6050','6100','6050']]),trace(),code)
    assert result[0]['amount']==amount and result[0]['share_class']=='preferred'
    assert result[0]['security_code']==code and result[0]['share_series']==series


@pytest.mark.asyncio
async def test_all_listed_share_series_share_one_fetch_without_duplicate_entitlement(tmp_path):
    from services.market_dashboard.issuer_dividends import SYMBOLS
    store=Store('sqlite://',tmp_path)
    store.put('universe',{'rows':[{'code':c,'name':n} for c,(n,_) in SYMBOLS.items()]})
    class Client:
        calls=0
        async def get(self,url):
            self.calls+=1
            return httpx.Response(200,request=httpx.Request('GET',url),content=html([
                ['2025.02.28','2025.04.18','6000','6050','6100','6050']]))
    client=Client();source=SimpleNamespace(store=store,client=client)
    for c in SYMBOLS: await collect_issuer_dividends(source,c)
    assert client.calls==1
    assert [store.get('events:'+c)['rows'][0]['amount'] for c in SYMBOLS]==['6000','6050','6100','6050']
    store.put('issuer-dividends-job:005380',{})
    await collect_issuer_dividends(source,'005380')
    assert all(len(store.get('events:'+c)['rows'])==1 for c in SYMBOLS)
    assert all(not store.get('events:'+c)['coverage']['verified'] for c in SYMBOLS)


@pytest.mark.asyncio
async def test_mismatched_listing_is_not_imported(tmp_path):
    store=Store('sqlite://',tmp_path)
    store.put('universe',{'rows':[{'code':'005385','name':'다른 회사'}]})
    class Client:
        async def get(self,url):
            return httpx.Response(200,request=httpx.Request('GET',url),content=html([
                ['2025.02.28','2025.04.18','6000','6050','6100','6050']]))
    await collect_issuer_dividends(SimpleNamespace(store=store,client=Client()),'005385')
    assert not store.get('events:005385')
    assert store.get('issuer-dividends-job:005380')['status']=='error'





def test_long_payment_delay_is_retained_as_unreviewed_candidate():
    result=parse_history(html([['2018.06.30','2019.08.14','1000','1000','1000','1000']]),trace())
    assert len(result)==1 and not result[0]['reviewed']
    assert result[0]['quality_flags']==['payment_delay_requires_review']


@pytest.mark.asyncio
async def test_new_review_retracts_old_issuer_confirmation_without_deleting_evidence(tmp_path):
    store=Store('sqlite://',tmp_path)
    store.put('universe',{'rows':[{'code':'005380','name':'현대차'}]})
    content=html([['2018.06.30','2019.08.14','1000','1000','1000','1000']])
    old=parse_history(content,trace())[0]
    old.update(reviewed=True,review_method='hyundai-dividend-table-v2',source_trace={**trace(),'source':'hyundai_motor_ir'})
    store.put('dividend-observations:005380',{'rows':[old]})
    store.put('events:005380',{'rows':[{**old,'kind':'dividend','payment_status':'confirmed','verified':False}]})
    class Client:
        async def get(self,url):
            return httpx.Response(200,request=httpx.Request('GET',url),content=content)
    await collect_issuer_dividends(SimpleNamespace(store=store,client=Client()),'005380')
    row=store.get('events:005380')['rows'][0]
    assert row['payment_status']=='requires_review' and not row['verified']
    assert store.get('dividend-observation-review:'+old['id'])['reviewed']
    assert not store.get('dividend-observations:005380')['rows'][0]['reviewed']
