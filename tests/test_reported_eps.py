"""fixture_non_production: class, units, corrections and stored-report discovery."""
from services.market_dashboard.per_share import reported_eps, security_eps, latest_observations
from services.market_dashboard.xbrl_facts import queue_stored_reports
from services.market_dashboard.store import Store

NS='http://xbrl.ifrs.org/taxonomy/2025/ifrs-full'
def fact(**patch):
    return {'account_id':'{'+NS+'}BasicEarningsLossPerShare','basis':'CFS','company_id':'corp',
        'period_start':'2025-01-01','period_end':'2025-12-31','period_verified':True,
        'currency':'KRW','unit':'{http://www.xbrl.org/2003/iso4217}KRW/{http://www.xbrl.org/2003/instance}shares',
        'value':'100','fact_id':'f1','filing_id':'20260314000001','published_at':'20260314',
        'source_trace':{'source':'fixture_non_production'},'dimensions':{},**patch}

def model(*rows):return reported_eps({'xbrl_facts':list(rows)})

def test_stock_class_and_currency_never_guessed():
    pref=fact(fact_id='pref',value='102',dimensions={'{'+NS+'}ClassesOfShareCapitalAxis':'{'+NS+'}PreferenceSharesMember'})
    result=model(fact(),pref,fact(currency=None,unit='shares'))
    rows=security_eps(result,'005930','corp')
    assert len(rows)==1 and rows[0]['value']==100 and rows[0]['published_at']=='2026-03-14'
    assert result['rejected']=={'per_share_unit_unverified':1}
    assert not security_eps(result,'005935',None)
    assert rows[0]['per_share_basis'] is None  # actual reported EPS != split-aligned series

def test_notes_duplicate_once_but_conflicts_and_segments_rejected():
    explicit=fact(fact_id='f2',dimensions={'{'+NS+'}ClassesOfShareCapitalAxis':'{'+NS+'}OrdinarySharesMember'})
    result=model(fact(),explicit)
    assert len(result['rows'])==1 and result['rows'][0]['input_fact_ids']==['f1','f2']
    assert not model(fact(),{**explicit,'value':'101'})['rows']
    assert not model(fact(dimensions={'segment':'phones'}))['rows']

def test_actual_period_and_loss_retained_no_quarter_eps_sum():
    annual=fact(value='-10')
    half=fact(period_end='2025-06-30',fact_id='h1')
    quarter=fact(period_start='2025-04-01',period_end='2025-06-30',fact_id='q2')
    rows=model(annual,half,quarter)['rows']
    assert {r['frequency'] for r in rows}=={'annual','cumulative','quarterly'}
    assert next(r for r in rows if r['frequency']=='annual')['value']==-10
    assert not any(r['frequency']=='ttm' for r in rows)

def test_corrections_remain_available_latest_table_selects_new_version():
    rows=model(fact(),fact(value='120',published_at='20260401',filing_id='20260401000001',fact_id='correction'))['rows']
    assert len(rows)==2
    assert latest_observations(rows)[0]['value']==120

def test_receipts_in_existing_reports_queue_without_source_refetch(tmp_path):
    s=Store('sqlite://',tmp_path)
    s.put('financial-report:old',{'rows':[{'receipt':'20260314000001'}]})
    official={'financial_reports':{'2025:CFS':{'snapshot_key':'financial-report:old','report_code':'11011','status':'ready'}}}
    s.put('official:005930',official)
    assert queue_stored_reports(s,'005930')==1
    s.put('xbrl-job:20260314000001',{'status':'ready','facts':20})
    assert queue_stored_reports(s,'005930')==0
    assert s.get('xbrl-job:20260314000001')['facts']==20
    s.put('official:005930',{**official,'period_facts':[]})
    assert queue_stored_reports(s,'005930')==0

def test_public_eps_uses_projection_and_dart_identity(monkeypatch,tmp_path):
    from fastapi.testclient import TestClient
    from services.market_dashboard import app as module
    s=Store('sqlite://',tmp_path)
    s.put('universe',{'rows':[{'code':'005930','name':'fixture_non_production'}]})
    s.put('dart-corporations',{'codes':{'005930':'corp'}})
    s.put('official:005930',{'xbrl_facts':[fact()]})
    monkeypatch.setattr(module,'get_store',lambda:s)
    response=TestClient(module.app).get('/api/market/v1/securities/005930/financials/per-share')
    assert response.status_code==200 and response.json()['rows'][0]['value']==100
    assert response.json()['comparison_status']=='reported_only'


def test_reported_eps_rejects_accounting_context_and_impossible_publication():
    scope={'{'+NS+'}ConsolidatedAndSeparateFinancialStatementsAxis':'{'+NS+'}SeparateMember'}
    result=model(fact(dimensions=scope),fact(fact_id='early',published_at='2025-12-01'))
    assert not result['rows']
    assert result['rejected']=={'accounting_scope_conflict':1,'publication_before_period_end':1}


def test_duplicate_equal_eps_with_different_split_units_remains_reported_only():
    a=fact(per_share_basis='old-shares')
    b=fact(fact_id='restated',per_share_basis='new-shares')
    result=model(a,b)
    assert result['rows'][0]['value']==100
    assert result['rows'][0]['per_share_basis'] is None
    assert result['rows'][0]['input_fact_ids']==['f1','restated']
