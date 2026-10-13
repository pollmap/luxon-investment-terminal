"""fixture_non_production: annual display must not manufacture fiscal dates."""
from copy import deepcopy

import pytest

from services.market_dashboard.financial_timeline import annual_contexts, annual_timeline
from services.market_dashboard.official_views import project
from services.market_dashboard.store import Store


def observation():
    return {'account_id': 'IS:매출액', 'name': '매출액', 'basis': 'CFS',
            'currency': 'KRW', 'unit': '원', 'kind': 'duration',
            'period_verified': True, 'period_start': '2024-04-01',
            'period_end': '2025-03-31', 'exact_value': '100000000',
            'filing_id': '20250601000001', 'published_at': '2025-06-01',
            'source_trace': {'source': 'fixture_non_production', 'sha256': 'a' * 64}}


def timeline(tmp_path, contexts):
    store = Store('sqlite://', tmp_path)
    reports = {'report': {'status': 'ready', 'basis': 'CFS', 'report_code': '11011',
                         'year': '2025', 'snapshot_key': 'financial-report:fixture'}}
    store.put('financial-report:fixture', {'rows': [
        {'statement': 'IS', 'unit': 'KRW', 'account_id': 'ifrs-full_Revenue',
         'current': 100000000, 'receipt': '20250601000001',
         'source_trace': {'source': 'fixture_non_production'}}]})
    try:
        return annual_timeline(store, reports, contexts=contexts)
    finally:
        store.engine.dispose()


def test_unverified_report_number_survives_without_invented_year_dates(tmp_path):
    result = timeline(tmp_path, [])
    row = result['rows'][0]
    assert row['value'] == 1 and row['period'] == '2025'
    assert row['period_start'] is row['period_end'] is None
    assert not row['period_verified'] and row['period_reason'] == 'actual_period_unverified'
    assert result['period_coverage'] == {'reported': 1, 'verified': 0}


def test_verified_non_december_year_uses_exact_context(tmp_path):
    facts = [observation()]
    result = timeline(tmp_path, annual_contexts({'period_facts': facts}))
    row = result['rows'][0]
    assert row['period_start'] == '2024-04-01' and row['period_end'] == '2025-03-31'
    assert row['period_verified'] and row['available_at'] == '2025-06-01'
    assert row['publication_date_only'] and result['period_coverage']['verified'] == 1


@pytest.mark.parametrize('change', [
    {'period_verified': False}, {'period_start': '2025-01-01'},
    {'period_start': 'invalid'}, {'currency': 'USD'}, {'unit': '주'},
    {'dimensions': {'BusinessSegmentsAxis': 'SomeSegment'}},
    {'quality_status': 'revoked'}, {'filing_id': ''},
    {'source_trace': {'source': 'fixture_non_production'}}])
def test_unverified_short_currency_segment_or_revoked_fact_cannot_verify_period(change):
    assert annual_contexts({'period_facts': [{**observation(), **change}]}) == []


@pytest.mark.parametrize('change', [
    {'basis': 'OFS'}, {'exact_value': '99999999'}, {'filing_id': '20250601000002'}])
def test_period_is_bound_to_statement_basis_amount_and_filing(tmp_path, change):
    contexts = annual_contexts({'period_facts': [{**observation(), **change}]})
    assert not timeline(tmp_path, contexts)['rows'][0]['period_verified']


def test_conflicting_actual_years_are_not_arbitrarily_chosen(tmp_path):
    other = {**observation(), 'period_start': '2024-01-01', 'period_end': '2024-12-31'}
    contexts = annual_contexts({'period_facts': [observation(), other]})
    row = timeline(tmp_path, contexts)['rows'][0]
    assert not row['period_verified'] and row['period_reason'] == 'conflicting_period_contexts'


def test_reports_projection_keeps_only_small_verified_contexts_and_preserves_input():
    official = {'financial_reports': {}, 'xbrl_facts': [observation()]}
    before = deepcopy(official)
    result = project(official, 'reports')
    assert 'xbrl_facts' not in result and result['annual_period_contexts'][0]['exact_value'] == '100000000'
    assert official == before


@pytest.mark.parametrize('change', [
    {'published_at':None,'source_trace':{'source':'fixture_non_production','sha256':'a'*64,
                                        'available_at':'2026-10-13T10:00:00+09:00'}},
    {'source_trace':{'source':'fixture_non_production','sha256':'a'*64,
                    'available_at':'2026-10-13T10:00:00+09:00',
                    'quality_flags':['publication_time_unverified']}},
    {'quality_flags':['publication_time_unverified']},
    {'quality_flags':{'publication_time_unverified':True}},
])
def test_collection_clock_or_retracted_publication_preserves_amount_period_but_not_clock(tmp_path, change):
    fact={**observation(),**change}
    before=deepcopy(fact)
    contexts=annual_contexts({'period_facts':[fact]})
    assert contexts and contexts[0]['available_at'] is None
    result=timeline(tmp_path,contexts)
    row=result['rows'][0]
    assert row['value']==1 and row['period_verified']
    assert row['period_start']=='2024-04-01' and row['period_end']=='2025-03-31'
    assert row['available_at'] is None and not row['publication_verified']
    assert fact==before


def test_publication_before_period_end_is_held_without_hiding_reported_amount(tmp_path):
    fact={**observation(),'published_at':'2024-06-01'}
    contexts=annual_contexts({'period_facts':[fact]})
    row=timeline(tmp_path,contexts)['rows'][0]
    assert row['value']==1 and row['period_verified']
    assert row['available_at'] is None and not row['publication_verified']
    assert row['publication_reason']=='publication_before_period_end'


def test_explicit_filing_date_is_used_without_mutating_collection_trace(tmp_path):
    fact=observation()
    fact['source_trace']['available_at']='2026-10-13T10:00:00+09:00'
    before=deepcopy(fact)
    contexts=annual_contexts({'period_facts':[fact]})
    row=timeline(tmp_path,contexts)['rows'][0]
    assert row['available_at']=='2025-06-01' and row['publication_verified']
    assert row['publication_date_only'] and row['publication_reason'] is None
    assert fact==before


def test_cached_context_without_new_publication_contract_is_not_promoted(tmp_path):
    context=annual_contexts({'period_facts':[observation()]})[0]
    context.pop('publication_verified')
    context.pop('publication_reason')
    row=timeline(tmp_path,[context])['rows'][0]
    assert row['period_verified'] and row['available_at'] is None
    assert not row['publication_verified']


def test_conflicting_publication_clocks_are_not_arbitrarily_selected(tmp_path):
    facts=[observation(),{**observation(),'published_at':'2025-06-02'}]
    row=timeline(tmp_path,annual_contexts({'period_facts':facts}))['rows'][0]
    assert row['period_verified'] and row['available_at'] is None
    assert row['publication_reason']=='conflicting_publication_contexts'
