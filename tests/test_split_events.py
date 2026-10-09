"""Fixture-only bridge from reviewed share units to performance actions."""
from copy import deepcopy
import pytest
from services.market_dashboard.store import Store
from services.market_dashboard.split_events import project_split_events
from services.market_dashboard.performance import performance

TRACE = {'source': 'fixture_non_production', 'source_url': 'https://example.test/fixture', 'sha256': 'a'*64}
BASIS = {'verified': True, 'complete_split_history': True, 'security_code': '005930',
    'company_id': 'corp', 'basis_id': 'fixture', 'start': '2024-01-01', 'end': '2025-12-31',
    'basis_date': '2025-12-31', 'source_trace': TRACE,
    'splits': [{'kind': 'split', 'effective_date': '2025-03-20', 'old_shares': '1',
                'new_shares': '50', 'verified': True, 'source_trace': TRACE}]}


def setup(store, basis=None):
    store.put('dart-corporations', {'codes': {'005930': 'corp'}})
    store.put('share-basis:005930', deepcopy(basis or BASIS))
    store.put('events:005930', {'venue': 'KRX', 'rows': [],
                              'coverage': {'verified': False, 'reason': 'fixture_incomplete'}})


def test_action_is_exact_idempotent_and_not_blanket_coverage(tmp_path):
    store = Store('sqlite://', tmp_path); setup(store)
    original = store.get('share-basis:005930')
    assert project_split_events(store, '005930')['added'] == 1
    event = store.get('events:005930')['rows'][0]
    assert event['ratio'] == '50' and event['effective_date'] == '2025-03-20'
    assert event['formula'] == 'new_shares / old_shares' and event['source_trace'] == TRACE
    assert not store.get('events:005930')['coverage']['verified']
    assert project_split_events(store, '005930')['added'] == 0
    assert len(store.get('events:005930')['rows']) == 1
    assert store.get('share-basis:005930') == original


@pytest.mark.parametrize('change', ['wrong_security', 'wrong_company', 'invalid_hash', 'invalid_ratio'])
def test_invalid_identity_or_source_does_not_commit(tmp_path, change):
    store = Store('sqlite://', tmp_path); basis = deepcopy(BASIS)
    if change == 'wrong_security': basis['security_code'] = '005935'
    elif change == 'wrong_company': basis['company_id'] = 'different'
    elif change == 'invalid_hash': basis['splits'][0]['source_trace']['sha256'] = 'not-a-hash'
    else: basis['splits'][0]['old_shares'] = '0'
    setup(store, basis)
    project_split_events(store, '005930')
    assert not store.get('events:005930')['rows']


def test_conflict_keeps_original_and_blocks_previously_verified_coverage(tmp_path):
    store = Store('sqlite://', tmp_path); setup(store)
    original = {'id': 'old', 'kind': 'split', 'effective_date': '2025-03-20',
                'security_code': '005930', 'ratio': '10', 'verified': True}
    store.put('events:005930', {'rows': [original], 'coverage': {'verified': True}})
    assert project_split_events(store, '005930')['status'] == 'conflicting_split_evidence'
    assert store.get('events:005930')['rows'] == [original]
    assert not store.get('events:005930')['coverage']['verified']
    assert store.get('split-event-review:005930')['rows'][0]['reviewed_ratio'] == '50'


@pytest.mark.parametrize('new_shares,last_price', [('50', 2), ('0.1', 1000)])
def test_verified_fixture_split_and_reverse_split_preserve_economic_return(tmp_path, new_shares, last_price):
    store = Store('sqlite://', tmp_path); basis = deepcopy(BASIS)
    basis['splits'][0]['new_shares'] = new_shares; setup(store, basis)
    project_split_events(store, '005930')
    events = store.get('events:005930')
    # Explicit fixture coverage for this two-day test, never a production claim.
    events['coverage'] = {'verified': True, 'start': '2025-03-19', 'end': '2025-03-20'}
    result = performance({'adjustment': 'raw', 'venue': 'KRX', 'rows': [
        {'date': '2025-03-19', 'close': 100}, {'date': '2025-03-20', 'close': last_price}]},
        events, code='005930')
    assert result['status'] == 'ready' and result['price_return'] == 0
    assert result['total_return'] == 0
