"""Historical IFRS recognition must not relax accounting-input validation."""
from copy import deepcopy
import pytest
from services.market_dashboard.taxonomy import full_ifrs_account
from services.market_dashboard.eps_calculation import eps_inputs
from services.market_dashboard.per_share import reported_eps
from services.market_dashboard.fundamental_metrics import calculate_fundamental_metrics
from tests.test_eps_calculation import observation
from tests.test_fundamental_metrics import facts

LEGACY='http://xbrl.iasb.org/taxonomy/2010-04-30/ifrs'


@pytest.mark.parametrize('namespace',[LEGACY,'http://xbrl.ifrs.org/taxonomy/2013-03-28/ifrs',
    'https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full'])
def test_legacy_and_current_use_same_validation(namespace):
    values=observation('2025-01-01','2025-03-31',100,10,'2025-05-10')
    for row in values:
        row['account_id']='{'+namespace+'}'+row['account_id'].rsplit('}',1)[1]
    assert eps_inputs(values)['rows'][0]['value']=='10'
    assert reported_eps({'xbrl_facts':values})['rows'][0]['value']==10
    invalid=deepcopy(values)
    invalid[-1]['unit']='{http://www.xbrl.org/2003/instance}shares'
    assert not eps_inputs(invalid)['rows']
    assert not reported_eps({'xbrl_facts':invalid})['rows']


@pytest.mark.parametrize('namespace',['https://fake.test/xbrl.ifrs.org/taxonomy/2024/ifrs-full',
    'http://xbrl.ifrs.org.evil.test/taxonomy/2024/ifrs-full',
    'http://xbrl.ifrs.org/taxonomy/2024/ifrs-smes',
    'http://xbrl.ifrs.org/taxonomy/2024/ifrs-mc',
    'http://xbrl.iasb.org/taxonomy/2099-01-01/ifrs'])
def test_unrecognized_namespace_is_not_a_standard_fact(namespace):
    account='{'+namespace+'}BasicEarningsLossPerShare'
    assert not full_ifrs_account(account)
    values=observation('2025-01-01','2025-03-31',100,10,'2025-05-10')
    for row in values:
        row['account_id']='{'+namespace+'}'+row['account_id'].rsplit('}',1)[1]
    assert not eps_inputs(values)['rows']
    assert not reported_eps({'xbrl_facts':values})['rows']


def test_legacy_cashflow_metrics_keep_distinct_calculations():
    source=facts()
    expected=calculate_fundamental_metrics(source)
    for row in source:
        row['account_id']='{'+LEGACY+'}'+row['account_id'].rsplit('}',1)[1]
    result=calculate_fundamental_metrics(source)
    assert [(r['metric'],r['value']) for r in result['rows']]==[(r['metric'],r['value']) for r in expected['rows']]
