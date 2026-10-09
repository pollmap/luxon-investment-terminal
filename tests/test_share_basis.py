"""fixture_non_production; a bounded share-unit attestation is not universal."""
from services.market_dashboard.share_basis import aligned_facts, aligned_prices
from services.market_dashboard.valuation_series import valuation_series
from test_fundamental_metrics import facts

E={'verified':True,'no_split_interval':True,'basis_id':'fixture','security_code':'005930','company_id':'corp',
   'start':'2024-01-01','end':'2025-03-31','source_trace':{'source_url':'https://example.test','sha256':'fixture'}}


def test_only_reported_periods_and_filings_within_reviewed_interval():
    source=facts(); [f.pop('per_share_basis') for f in source]
    assert all(r['per_share_basis']=='fixture' for r in aligned_facts(source,E))
    for change in ({'company_id':'other'},{'period_start':'2023-01-01'},{'published_at':'2025-04-01'}):
        assert 'per_share_basis' not in aligned_facts([{**source[0],**change}],E)[0]


def test_source_adjusted_prices_are_never_claimed_aligned():
    assert 'per_share_basis' not in aligned_prices({'adjustment':'source'},E,'005930')
    assert 'per_share_basis' not in aligned_prices({'adjustment':'raw'},E,'005935')
    snapshot=aligned_prices({'adjustment':'raw','venue':'KRX','rows':[{'date':'2025-03-17','close':100},{'date':'2025-04-01','close':100}]},E,'005930')
    f={'metric':'EPS','value':10,'basis':'CFS','frequency':'annual','period_start':'2024-01-01','period_end':'2024-12-31','period_verified':True,'published_at':'2025-03-14','source_trace':E['source_trace'],'security_code':'005930','currency':'KRW','share_class_verified':True,'per_share_basis':'fixture'}
    result=valuation_series([f],snapshot,code='005930',interval='day')
    assert result['series'][0]['reference_price']==150
    assert result['series'][1]['reference_price'] is None


def test_late_no_split_report_requires_matching_explicit_unit_review():
    from copy import deepcopy
    f={**facts()[0],'published_at':'2025-04-20'}
    f.pop('per_share_basis',None)
    review={k:f[k] for k in ('filing_id','basis','period_start','period_end')}
    review.update(verified=True,company_id='corp',security_code='005930',
                  unit_as_of='2025-03-31',source_trace=E['source_trace'])
    evidence={**E,'fact_unit_reviews':[review]}
    aligned=aligned_facts([f],evidence)[0]
    assert aligned['per_share_basis']=='fixture'
    assert aligned['published_at']=='2025-04-20' and aligned['value']==f['value']
    assert aligned['share_unit_review']==review
    for change in ({'unit_as_of':'2025-04-01'},{'unit_as_of':'invalid'},
                   {'security_code':'005935'},{'company_id':'other'},
                   {'filing_id':'unreviewed'},{'verified':False}):
        bad=deepcopy(evidence);bad['fact_unit_reviews']=[{**review,**change}]
        assert 'per_share_basis' not in aligned_facts([f],bad)[0]
    assert 'per_share_basis' not in aligned_facts([f],{**E,'fact_unit_reviews':[review,review]})[0]
