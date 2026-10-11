import asyncio, importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
from services.market_dashboard.store import Store
from services.market_dashboard.issuer_documents import company_link,company_report,report_dependencies
from services.market_dashboard.official_views import read_view
from services.market_dashboard.authenticated_sources import AuthenticatedSources
app=importlib.import_module('services.market_dashboard.app')


def seeded(tmp_path):
    s=Store('sqlite://',tmp_path)
    s.put('universe', {'rows': [
        {'code':'005930','name':'삼성전자','source_trace':{'source':'fixture_non_production'}},
        {'code':'005935','name':'삼성전자우','source_trace':{'source':'fixture_non_production'}},
    ]})
    s.put('dart-corporations',{'codes':{'005930':'fixture_issuer'}})
    s.put('company-report-fixture',{'status':'ready','rows':[
        {'name':'매출액','current':100,'unit':'KRW','statement':'IS','account_id':'ifrs-full_Revenue'},
        {'name':'기본주당이익','current':10,'unit':'KRW','statement':'IS','account_id':'ifrs-full_BasicEarningsLossPerShare'}]})
    s.put('official:005930',{'financial_reports':{'2025:11011:CFS':{'year':'2025','report_code':'11011',
        'basis':'CFS','status':'ready','snapshot_key':'company-report-fixture','row_count':2}}})
    s.put('official:005935',{'per_share_facts':[{'value':7,'source':'own_fixture'}]})
    return s


def test_shared_report_keeps_raw_and_independent_per_share_state(tmp_path,monkeypatch):
    s=seeded(tmp_path)
    monkeypatch.setattr(app,'get_store',lambda:s)
    monkeypatch.setattr(app,'rows_with_details',lambda:(s.get('universe')['rows'],{}))
    app._body_cache.clear()
    client=TestClient(app.app)
    p='/api/market/v1/securities/005935/financials'
    index=client.get(p).json()
    assert len(index['reports'])==1
    assert index['issuer_link']['issuer_security_code']=='005930'
    value=client.get(p,params={'year':'2025','report_code':'11011','basis':'CFS'}).json()
    assert [r['name'] for r in value['rows']]==['매출액']
    assert value['excluded_per_share_rows']==1
    assert len(s.get('company-report-fixture')['rows'])==2
    assert s.get('official:005935')['per_share_facts'][0]['value']==7
    assert read_view(s,'005935','analysis')['eps']['rows']==[]
    assert s.get('events:005935') is None
    client.get(p,params={'year':'2024','report_code':'11011','basis':'CFS'})
    assert s.get('financial-request:005930')['query_key']=='statement:2024:11011:CFS'
    assert s.get('financial-request:005935') is None


def test_company_facts_shareholders_share_but_dividend_and_shares_remain_separate(tmp_path):
    s=seeded(tmp_path)
    source=s.get('official:005930')
    source.update(company={'corp_name':'삼성전자'},report_facts={'year':'2025','sections':{
        'shareholders':{'rows':[{'name':'회사 주주'}],'year':'2025'},
        'dividends':{'rows':[{'amount':100}]},'shares':{'rows':[{'count':1000}]}}})
    s.put('official:005930',source)
    own=s.get('official:005935')
    own['report_facts']={'sections':{'dividends':{'rows':[{'amount':110}]}}}
    s.put('official:005935',own)
    result=read_view(s,'005935','company')
    assert result['company']['corp_name']=='삼성전자'
    assert result['report_facts']['sections']['shareholders']['year']=='2025'
    assert result['report_facts']['sections']['dividends']['rows'][0]['amount']==110
    assert 'shares' not in result['report_facts']['sections']
    assert 'shareholders' not in s.get('official:005935')['report_facts']['sections']
    source['company']['corp_name']='정정 기업명'
    s.put('official:005930',source)
    assert read_view(s,'005935','company')['company']['corp_name']=='정정 기업명'


def test_company_detail_cache_tracks_issuer_revision(tmp_path,monkeypatch):
    s=seeded(tmp_path)
    primary=s.get('official:005930')
    primary['company']={'corp_name':'공시 기업명'}
    s.put('official:005930',primary)
    monkeypatch.setattr(app,'get_store',lambda:s)
    monkeypatch.setattr(app,'rows_with_details',lambda **kwargs:(s.get('universe')['rows'],{}))
    client=TestClient(app.app)
    url='/api/market/v1/securities/005935?view=company'
    assert client.get(url).json()['official']['company']['corp_name']=='공시 기업명'
    primary['company']['corp_name']='정정된 기업명'
    s.put('official:005930',primary)
    assert client.get(url).json()['official']['company']['corp_name']=='정정된 기업명'


def test_source_correction_and_identity_withdrawal_change_read_model(tmp_path):
    s=seeded(tmp_path)
    assert read_view(s,'005935','reports')['financial_reports']['2025:11011:CFS']['status']=='ready'
    o=s.get('official:005930');o['financial_reports']['2025:11011:CFS']['status']='error';s.put('official:005930',o)
    assert read_view(s,'005935','reports')['financial_reports']['2025:11011:CFS']['status']=='error'
    assert 'official:005930' in report_dependencies(s,'005935')
    s.put('dart-corporations',{'codes':{'005930':'fixture_issuer','005935':'native_issuer'}})
    assert company_link(s,'005935') is None
    assert not read_view(s,'005935','reports').get('financial_reports')


def test_unreviewed_symbol_and_changed_listing_never_alias(tmp_path):
    s=seeded(tmp_path)
    assert company_link(s,'005945') is None
    s.put('universe',{'rows':[{'code':'005930','name':'삼성전자'},{'code':'005935','name':'different entity'}]})
    assert company_link(s,'005935') is None


def test_preferred_refresh_reuses_issuer_lock_and_does_not_write_fake_progress(tmp_path):
    s=seeded(tmp_path)
    source=AuthenticatedSources(SimpleNamespace(store=s,client=None));source.dart='fixture_non_production'
    original=source._refresh_financial_data
    issuer_call=AsyncMock(return_value={'status':'ready','processed':1})
    async def dispatch(code,corp_code=None,max_tasks=3):
        if code=='005930':return await issuer_call(code,corp_code,max_tasks=max_tasks)
        return await original(code,corp_code,max_tasks=max_tasks)
    source._refresh_financial_data=dispatch
    result=asyncio.run(source.refresh_financial_data('005935',max_tasks=1))
    assert result['issuer_security_code']=='005930'
    issuer_call.assert_awaited_once_with('005930','fixture_issuer',max_tasks=1)
    assert not s.get('official:005935').get('financial_query_progress')

def test_company_ttm_amounts_shared_without_preferred_eps_and_correction_invalidates(tmp_path,monkeypatch):
    s=seeded(tmp_path)
    primary=s.get('official:005930')
    primary['period_facts']=[{'account_id':'IS:매출액','basis':'CFS','unit':'원','currency':'KRW','kind':'duration',
        'period_verified':True,'period_start':start,'period_end':end,'value':value,
        'source_trace':{'source':'fixture_non_production'}} for start,end,value in
        [('2025-01-01','2025-03-31',10),('2025-04-01','2025-06-30',20),
         ('2025-07-01','2025-09-30',30),('2025-10-01','2025-12-31',40)]]
    s.put('official:005930',primary)
    monkeypatch.setattr(app,'get_store',lambda:s)
    client=TestClient(app.app)
    url='/api/market/v1/securities/005935/financials/ttm'
    result=client.get(url).json()
    assert result['rows'][0]['value']==100
    assert result['per_share_status']=='not_calculated'
    assert result['issuer_link']['issuer_security_code']=='005930'
    primary['period_facts'][-1]['value']=80;s.put('official:005930',primary)
    assert client.get(url).json()['rows'][0]['value']==140
    assert s.get('official:005935')['per_share_facts'][0]['value']==7
    s.put('universe',{'rows':[{'code':'005930','name':'삼성전자'},{'code':'005935','name':'different company'}]})
    assert client.get(url).json()['rows']==[]
