"""fixture_non_production: split and reverse-split unit reconciliation."""
from copy import deepcopy
from decimal import Decimal
from services.market_dashboard.split_units import valid_history, normalize_fact, normalize_prices
from services.market_dashboard.share_basis import aligned_facts
from services.market_dashboard.eps_calculation import rounding

TRACE = {'source_url':'https://example.test/fixture','sha256':'fixture_non_production'}
E = {'verified':True,'complete_split_history':True,'basis_id':'fixture-split',
     'security_code':'005930','company_id':'corp','start':'2024-01-01',
     'end':'2025-12-31','basis_date':'2025-12-31','source_trace':TRACE,
     'splits':[{'kind':'split','effective_date':'2025-03-20','new_shares':'50','old_shares':'1',
                'verified':True,'source_trace':TRACE}],
     'fact_unit_reviews':[{'filing_id':'fixture-report','basis':'CFS','period_start':'2024-01-01',
                          'period_end':'2024-12-31','unit_as_of':'2025-03-14',
                          'verified':True,'source_trace':TRACE}]}
F = {'fact_id':'original','company_id':'corp','filing_id':'fixture-report','basis':'CFS',
     'account_id':'{http://xbrl.ifrs.org/fixture}BasicEarningsLossPerShare',
     'period_start':'2024-01-01','period_end':'2024-12-31','published_at':'2025-03-14',
     'source_trace':TRACE,'value':'100','decimals':'0',
     'unit':'{http://www.xbrl.org/2003/iso4217}KRW/{http://www.xbrl.org/2003/instance}shares'}


def test_split_preserves_source_and_reconciliation_precision():
    old=deepcopy(F)
    eps=aligned_facts([F],E)[0]
    assert eps['value']=='2.00' and eps['per_share_basis']==E['basis_id']
    assert eps['fact_id']!='original' and eps['input_fact_ids']==['original']
    assert rounding(eps)==Decimal('0.01') and F==old
    shares=normalize_fact({**F,'account_id':'{http://xbrl.ifrs.org/fixture}WeightedAverageShares','unit':'{http://www.xbrl.org/2003/instance}shares'},E)
    profit=normalize_fact({**F,'account_id':'{http://xbrl.ifrs.org/fixture}ProfitLoss','unit':'{iso}KRW','value':'10000'},E)
    assert Decimal(shares['value'])==5000 and profit['value']=='10000'
    assert Decimal(profit['value'])/Decimal(shares['value'])==Decimal(eps['value'])
    assert normalize_fact(eps,E)==eps
    bad_unit={**F,'unit':'{http://www.xbrl.org/2003/instance}shares'}
    assert normalize_fact(bad_unit,E)==bad_unit


def test_already_restated_comparatives_not_adjusted_from_period_end():
    review=deepcopy(E); review['fact_unit_reviews'][0]['unit_as_of']='2025-03-20'
    result=normalize_fact({**F,'value':'2'},review)
    assert Decimal(result['value'])==2 and result['split_normalization']['factor']=='1'
    assert normalize_fact({**F,'filing_id':'unreviewed'},E)=={**F,'filing_id':'unreviewed'}
    preferred={**F,'dimensions':{'{x}ClassesOfShareCapitalAxis':'{x}PreferenceSharesMember'}}
    assert normalize_fact(preferred,E)==preferred


def test_raw_prices_split_reverse_split_and_no_adjusted_price_double_application():
    raw={'adjustment':'raw','rows':[{'date':'2025-03-19','close':5000,'volume':100,'amount':500000},
          {'date':'2025-03-20','close':100,'volume':5000}, {'date':'2026-01-01','close':100}]}
    result=normalize_prices(raw,E)
    assert [r['close'] for r in result['rows']]==[100,100]
    assert result['rows'][0]['volume']==5000 and result['rows'][0]['amount']==500000
    assert result['rows'][0]['raw_ohlcv']['close']==5000
    assert normalize_prices(result,E)==result
    source={**raw,'adjustment':'source'}
    assert normalize_prices(source,E)==source
    reverse=deepcopy(E);reverse['splits'][0].update(new_shares='1',old_shares='10')
    assert normalize_prices(raw,reverse)['rows'][0]['close']==50000


def test_unverified_missing_and_conflicting_action_history_cannot_align():
    for change in ({'complete_split_history':False},{'splits':[]},{'basis_date':'2026-01-01'}):
        assert not valid_history({**E,**change})
    conflict=deepcopy(E);conflict['splits'].append(conflict['splits'][0].copy())
    assert not valid_history(conflict)
    missing=deepcopy(E);missing['fact_unit_reviews']=[]
    assert 'per_share_basis' not in aligned_facts([F],missing)[0]


def test_later_publication_requires_explicit_review_of_its_share_unit():
    # A later report can explicitly state a comparative in a reviewed old unit.
    # Its release date still controls point-in-time availability, not the unit.
    fact = {**F, 'published_at': '2026-03-15', 'value': '2'}
    review = deepcopy(E)
    review['fact_unit_reviews'][0]['unit_as_of'] = '2025-12-31'
    aligned = aligned_facts([fact], review)[0]
    assert aligned['per_share_basis'] == E['basis_id']
    assert aligned['published_at'] == '2026-03-15'
    assert Decimal(aligned['value']) == 2
    assert aligned['input_fact_ids'] == ['original']

    # An unrelated filing cannot inherit the review, even for the same period.
    unreviewed = {**fact, 'filing_id': 'other-filing'}
    assert 'per_share_basis' not in aligned_facts([unreviewed], review)[0]
    broad = {**E, 'complete_split_history': False, 'no_split_interval': True}
    assert 'per_share_basis' not in aligned_facts([fact], broad)[0]
