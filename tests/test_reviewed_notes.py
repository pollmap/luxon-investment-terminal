"""fixture_non_production: reviewed statement facts are not guessed XBRL units."""
from copy import deepcopy
from services.market_dashboard.reviewed_notes import verified_note
from services.market_dashboard.per_share import reported_eps
from services.market_dashboard.eps_calculation import eps_inputs
from services.market_dashboard.fact_store import FactStore
from services.market_dashboard.store import Store


def test_archived_reviewed_notes_reconcile_and_keep_original_unit_error(tmp_path):
    store=Store('sqlite://',tmp_path)
    base={'company_id':'corp','filing_id':'fixture','basis':'CFS','period_start':'2024-01-01',
          'period_end':'2024-12-31','period_verified':True,'published_at':'2025-03-14',
          'mapping_verified':True,'currency':'KRW','dimensions':{},'decimals':'0',
          'source_trace':{'source':'dart_reviewed_note','sha256':'fixture_non_production',
             'source_url':'https://example.test','company_id':'corp','filing_id':'fixture'}}
    rows=[]
    for name,value,unit in [('BasicEarningsLossPerShare','10','{iso}KRW/{http://www.xbrl.org/2003/instance}shares'),
                            ('WeightedAverageShares','100','{http://www.xbrl.org/2003/instance}shares'),
                            ('ProfitLossAttributableToOrdinaryEquityHoldersOfParentEntity','1000','{iso}KRW')]:
        rows.append({**base,'account_id':'{urn:korea-stocks:reviewed-eps-note}'+name,
                     'source_account_label':name,'value':value,'unit':unit})
    facts=FactStore(store);assert facts.append('corp',rows)==3
    saved=facts.analysis_facts('corp')
    assert len(saved)==3 and len(eps_inputs(saved)['rows'])==1
    assert reported_eps({'xbrl_facts':saved})['rows'][0]['value']==10
    for change in ({'mapping_verified':False},{'filing_id':'other'},{'company_id':'other'}):
        assert not verified_note({**saved[0],**change})
    bad=deepcopy(saved[0]);bad['source_trace']['sha256']=''
    assert not verified_note(bad)
