from services.market_dashboard.store import Store
from services.market_dashboard.fact_store import FactStore


def test_analysis_snapshot_does_not_duplicate_notes_but_keeps_immutable_facts(tmp_path):
    store=Store('sqlite://',tmp_path)
    facts=FactStore(store)
    common={'period_end':'2025-12-31','value':'100','filing_id':'20260303000001',
            'source_trace':{'source':'opendart_xbrl','sha256':'fixture'},'dimensions':{}}
    inputs=[{**common,'account_id':'{ifrs}Revenue'},
            {**common,'account_id':'{ifrs}Revenue','dimensions':{'SegmentAxis':'SegmentA'}},
            {**common,'account_id':'{ifrs}Inventories'},
            {**common,'account_id':'{ifrs}BasicEarningsLossPerShare','dimensions':{'ClassesOfShareCapitalAxis':'OrdinarySharesMember'}}]
    assert facts.append('fixture',inputs)==4
    selected=facts.analysis_facts('fixture')
    assert {f['account_id'] for f in selected}=={'{ifrs}Revenue','{ifrs}BasicEarningsLossPerShare'}
    assert len(selected)==2
    assert len(facts.read('fixture'))==4
    assert all(row['fact_id'] for row in selected)
