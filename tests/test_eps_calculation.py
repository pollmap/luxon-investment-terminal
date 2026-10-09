from decimal import Decimal
from services.market_dashboard.eps_calculation import calculate_ttm_eps, eps_inputs

NS='{https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full}'
def observation(start,end,profit,shares,published,*,share_basis='split-v1',basis='CFS'):
    rows=[]
    for concept,value,unit,currency in (
        ('ProfitLossAttributableToOwnersOfParent',str(profit),'{iso4217}KRW','KRW'),
        ('WeightedAverageShares',str(shares),'{http://www.xbrl.org/2003/instance}shares',None),
        ('BasicEarningsLossPerShare',str(Decimal(profit)/Decimal(shares)),'{iso4217}KRW/{http://www.xbrl.org/2003/instance}shares','KRW')):
        rows.append({'fact_id':concept+start+end+published,'company_id':'fixture','account_id':NS+concept,'value':value,
            'unit':unit,'currency':currency,'basis':basis,'period_start':start,'period_end':end,'period_verified':True,
            'dimensions':{NS+'ClassesOfShareCapitalAxis':NS+'OrdinarySharesMember'},
            'source_trace':{'source':'fixture'},'filing_id':published,'published_at':published,'per_share_basis':share_basis})
    return rows

def test_ttm_weights_shares_by_actual_period_and_never_sums_eps():
    facts=[]
    for start,end,shares,published in [('2025-01-01','2025-03-31',10,'2025-05-10'),('2025-04-01','2025-06-30',20,'2025-08-10'),('2025-07-01','2025-09-30',30,'2025-11-10'),('2025-10-01','2025-12-31',40,'2026-03-10')]:
        facts+=observation(start,end,100,shares,published)
    result=calculate_ttm_eps(facts)
    assert len(result['rows'])==1
    r=result['rows'][0]
    expected=Decimal(400)/(Decimal(10*90+20*91+30*92+40*92)/365)
    assert abs(Decimal(r['exact_value'])-expected)<Decimal('1e-20')
    assert len(r['input_fact_ids'])==12 and r['published_at']=='2026-03-10'

def test_cumulative_shares_are_time_weighted_before_ttm():
    facts=observation('2025-01-01','2025-03-31',100,10,'2025-05-10')
    # Whole-year shares are a weighted average, not a count to subtract directly.
    facts+=observation('2025-01-01','2025-06-30',200,10,'2025-08-10')
    facts+=observation('2025-01-01','2025-09-30',300,10,'2025-11-10')
    facts+=observation('2025-01-01','2025-12-31',400,10,'2026-03-10')
    result=calculate_ttm_eps(facts)
    assert result['rows'][-1]['value']==40

def test_missing_quarter_or_split_basis_cannot_publish_value():
    facts=[]
    for start,end,published in [('2025-01-01','2025-03-31','2025-05-10'),('2025-04-01','2025-06-30','2025-08-10'),('2025-07-01','2025-09-30','2025-11-10'),('2025-10-01','2025-12-31','2026-03-10')]:
        facts+=observation(start,end,100,10,published,share_basis=None)
    result=calculate_ttm_eps(facts)
    assert result['rows'][-1]['value'] is None and result['rows'][-1]['status']=='share_basis_required'
    assert not calculate_ttm_eps(facts[:-3])['rows']

def test_parent_total_preferred_and_diluted_values_are_not_basic_numerator():
    facts=observation('2025-01-01','2025-12-31',100,10,'2026-03-10')
    facts[0]['dimensions']={}
    assert not eps_inputs(facts)['rows']
    facts[0]['account_id']=NS+'ProfitLossAttributableToOrdinaryEquityHoldersOfParentEntityIncludingDilutiveEffects'
    assert not eps_inputs(facts)['rows']

def test_restated_values_only_apply_after_their_publication_and_mismatch_is_rejected():
    facts=observation('2025-01-01','2025-12-31',100,10,'2026-03-10')
    facts[-1]['value']='20'
    assert eps_inputs(facts)['rejected']['reported_eps_mismatch']==1

def test_duplicate_integer_eps_format_does_not_change_acceptance_or_input_order():
    facts=observation('2025-01-01','2025-12-31',38343,10,'2026-03-10')
    facts[-1]['value']='3834.0'
    facts[-1]['decimals']='INF'
    facts.append({**facts[-1],'fact_id':'duplicate-eps','value':'3834'})
    first=eps_inputs(facts)['rows']
    assert len(first)==1
    assert first==eps_inputs(list(reversed(facts)))['rows']
    assert Decimal(first[0]['reconciliation_residual'])==Decimal('0.3')


def test_publication_entity_and_accounting_scope_are_evidence_not_defaults():
    facts=observation('2025-01-01','2025-12-31',100,10,'2026-03-10')
    facts[0]['company_id']=None
    assert not eps_inputs(facts)['rows']
    facts[0]['company_id']='fixture'
    facts[0]['dimensions'][NS+'ConsolidatedAndSeparateFinancialStatementsAxis']=NS+'SeparateMember'
    assert eps_inputs(facts)['rejected']['accounting_scope_conflict']==1
    facts[0]['dimensions'].pop(NS+'ConsolidatedAndSeparateFinancialStatementsAxis')
    facts[0]['published_at']='2025-12-01'
    assert eps_inputs(facts)['rejected']['publication_evidence_required']==1


def test_old_ready_ttm_is_not_latest_readiness():
    from services.market_dashboard.eps_calculation import ttm_readiness
    old={'period_end':'2024-12-31','status':'ready','published_at':'2025-03-10'}
    latest={'period_end':'2025-06-30','status':'share_basis_required','published_at':'2025-08-10'}
    assert ttm_readiness([], [old], [{'period_end':'2025-06-30'}])['status']=='period_inputs_required'
    result=ttm_readiness([], [old,latest], [{'period_end':'2025-06-30'}])
    assert result['status']=='share_basis_required' and result['period_end']=='2025-06-30'


def test_eps_waits_for_every_input_and_trace_preserves_ttm_publication():
    from services.market_dashboard.financial_dates import publication_date
    facts=[]
    for start,end,pub in [('2025-01-01','2025-03-31','2025-05-10'),
        ('2025-04-01','2025-06-30','2025-08-10'),
        ('2025-07-01','2025-09-30','2025-11-10'),
        ('2025-10-01','2025-12-31','2026-03-10')]:
        facts += observation(start,end,100,10,pub)
    # The note is a later publication than EPS, despite sharing its document ID.
    facts[-2]['published_at']='2026-03-20'
    for fact in facts:
        fact['source_trace']['available_at']=fact['published_at']
    result=calculate_ttm_eps(facts)
    row=result['rows'][0]
    assert row['published_at']=='2026-03-20'
    assert publication_date(row)[0]=='2026-03-20'
    assert row['source_trace']['input_fact_ids']==row['input_fact_ids']


def test_newer_cumulative_correction_replaces_stale_standalone_quarter():
    from services.market_dashboard.eps_calculation import quarters_at
    facts=observation('2025-01-01','2025-03-31',100,10,'2025-05-10')
    facts+=observation('2025-04-01','2025-06-30',100,10,'2025-08-10')
    facts+=observation('2025-01-01','2025-06-30',220,10,'2025-09-01')
    inputs=eps_inputs(facts)['rows']
    before=quarters_at(inputs,'2025-08-31')[-1]
    after=quarters_at(inputs,'2025-09-01')[-1]
    assert before['profit']=='100' and before['value']=='10'
    assert after['profit']=='120' and after['value']=='12'
    assert after['published_at']=='2025-09-01'
    assert len(after['input_fact_ids'])==6
    assert after['source_trace']['input_fact_ids']==after['input_fact_ids']


def test_newly_revised_prior_period_is_not_subtracted_from_older_cumulative():
    from services.market_dashboard.eps_calculation import quarters_at
    facts=observation('2025-01-01','2025-03-31',110,10,'2025-09-01')
    facts+=observation('2025-01-01','2025-06-30',200,10,'2025-08-10')
    rows=quarters_at(eps_inputs(facts)['rows'],'2025-09-01')
    assert len(rows)==1 and rows[0]['period_end']=='2025-03-31'

