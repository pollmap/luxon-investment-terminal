"""fixture_non_production: display-safe period and dimension filtering."""
from services.market_dashboard.financial_summary import ttm_summary


def facts(account='IS:매출액', **extra):
    return [dict(account_id=account, basis='CFS', unit='원', currency='KRW', kind='duration',
        period_verified=True, period_start=start, period_end=end, value=amount,
        source_trace={'source':'fixture_non_production'}, **extra)
        for start,end,amount in [('2025-01-01','2025-03-31',10),('2025-04-01','2025-06-30',20),
                                ('2025-07-01','2025-09-30',30),('2025-10-01','2025-12-31',40)]]


def test_summary_does_not_add_segments_or_copy_into_eps():
    total=facts()
    segment=facts(dimensions={'BusinessSegmentsAxis':'SemiconductorMember'})
    result=ttm_summary({'period_facts':total,'xbrl_facts':segment})
    assert len(result['rows']) == 1 and result['rows'][0]['value'] == 100
    assert result['per_share_status'] == 'not_calculated'
    assert ttm_summary({'period_facts':total},'OFS')['rows'] == []


def test_cash_flow_and_incomplete_periods():
    rows=facts('{ifrs}CashFlowsFromUsedInOperatingActivities')
    assert ttm_summary({'xbrl_facts':rows})['rows'][0]['metric'] == 'operating_cash'
    assert ttm_summary({'xbrl_facts':rows[:-1]})['rows'] == []


def test_current_summary_does_not_mix_old_cash_flow_period():
    revenue=facts()
    cash=facts('{ifrs}CashFlowsFromUsedInOperatingActivities')
    for row in cash:
        row['period_start']=row['period_start'].replace('2025','2023')
        row['period_end']=row['period_end'].replace('2025','2023')
    result=ttm_summary({'period_facts':revenue,'xbrl_facts':cash})
    assert [r['metric'] for r in result['rows']]==['revenue']
    assert result['older_periods']=={'operating_cash':'2023-12-31'}
