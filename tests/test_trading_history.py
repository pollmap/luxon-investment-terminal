"""fixture_non_production: market scope, nulls, pagination and old observations."""
import pytest
from services.market_dashboard.trading_history import trading_history
from services.market_dashboard.financial_timeline import annual_timeline
from services.market_dashboard.authenticated_sources import dart_financial_task_plan
from services.market_dashboard.store import Store


def test_investor_breakdown_preserves_null_and_fractional_holding_rate():
    result=trading_history({'rows':[{'date':'2020-01-02','individual':None,
        'foreigner':{'netBuyVolume':'-30'},'institution':{'netBuyVolume':'20','breakdown':{'bank':{'netBuyVolume':'12'}}},
        'foreignerHolding':{'holdingRate':'0.12345'}}]},'investor-trading')
    row=result['rows'][0]
    assert result['venue']=='통합' and row['individual'] is None
    assert row['foreigner']==-30 and row['bank']==12 and row['holding']==pytest.approx(12.345)
    assert row['pensionFund'] is None


def test_program_total_requires_both_parts_and_filters_exact_dates():
    data={'rows':[{'date':'2019-01-02','arbitrage':{'buyVolume':'10'},'nonArbitrage':{'buyVolume':'20'}},
                  {'date':'2026-10-02','arbitrage':{'buyVolume':'11'}}]}
    result=trading_history(data,'program-trades','buyVolume')
    assert result['venue']=='KRX' and result['rows'][0]['total']==30 and result['rows'][1]['total'] is None
    assert len(trading_history(data,'program-trades','buyVolume',end='2020-01-01')['rows'])==1
    with pytest.raises(ValueError): trading_history(data,'program-trades',start='2026-01-01',end='2020-01-01')


def test_report_plan_reaches_2015_without_duplicate_tasks():
    tasks=dart_financial_task_plan()
    assert min(t['year'] for t in tasks if t['kind']=='statement')=='2015'
    assert len({t['query_key'] for t in tasks})==len(tasks)
    assert all(int(t['year'])>=2023 for t in tasks if t['kind']=='indicator')


def test_annual_timeline_does_not_mix_currency_scope_or_conflicting_values(tmp_path):
    store=Store('sqlite://',tmp_path)
    row={'statement':'IS','unit':'KRW','account_id':'ifrs-full_Revenue','current':100000000}
    store.put('financial-report:a',{'rows':[row,{**row,'unit':'USD','current':1000}]})
    reports={'2015':{'year':'2015','report_code':'11011','basis':'CFS','status':'ready','snapshot_key':'financial-report:a'}}
    result=annual_timeline(store,reports)
    assert result['rows'][0]['value']==1 and result['rows'][0]['period']=='2015'
    assert not annual_timeline(store,reports,'OFS')['rows']
    store.put('financial-report:a',{'rows':[row,{**row,'current':900000000}]})
    assert not annual_timeline(store,reports)['rows']
