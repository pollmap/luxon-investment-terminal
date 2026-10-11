"""Fixture facts: collection replay must not create another disclosure revision."""
import hashlib
import json

import pytest
from sqlalchemy import select

from services.market_dashboard.fact_store import FactStore
from services.market_dashboard.store import Store


def observation():
    return {
        'account_id': 'IS:영업이익', 'period_start': '2025-01-01',
        'period_end': '2025-12-31', 'value': '100', 'basis': 'CFS',
        'unit': '원', 'currency': 'KRW', 'dimensions': {},
        'filing_id': '20260303000001', 'published_at': '2026-03-03',
        'source_trace': {'source': 'fixture_non_production', 'sha256': 'original-document',
                         'ingested_at': '2026-03-05T00:00:00+00:00',
                         'received_at': '2026-03-05T00:00:00+00:00'},
    }


@pytest.fixture
def ledger(tmp_path):
    source = Store('sqlite://', tmp_path)
    facts = FactStore(source)
    yield facts
    source.engine.dispose()


def seed_legacy(ledger, fact):
    payload = {**fact, 'company_id': 'fixture', 'value': '100'}
    identity_payload = {**payload, 'source_trace': {
        k: v for k, v in payload['source_trace'].items() if k not in ('received_at', 'collected_at')
    }}
    legacy_id = hashlib.sha256(json.dumps(
        identity_payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')
    ).encode()).hexdigest()
    with ledger.store.engine.begin() as db:
        db.execute(ledger.facts.insert().values(
            id=legacy_id, company_id='fixture', account_id=fact['account_id'],
            period_end=fact['period_end'], filing_id=fact['filing_id'],
            payload=json.dumps(payload, ensure_ascii=False), available_at='2026-03-03',
        ))
    return legacy_id


def test_old_fact_id_and_original_evidence_survive_repeated_collection(ledger):
    fact = observation()
    legacy_id = seed_legacy(ledger, fact)
    for day in ('2026-03-06', '2026-03-07', '2026-03-08'):
        replay = {**fact, 'source_trace': {**fact['source_trace'],
                  'ingested_at': day, 'received_at': day, 'collected_at': day}}
        assert ledger.append('fixture', [replay]) == 0
    stored = ledger.read('fixture')
    assert len(stored) == 1 and stored[0]['fact_id'] == legacy_id
    assert stored[0]['source_trace'] == fact['source_trace']


def test_new_facts_share_identity_across_collection_clocks_and_batch_boundaries(ledger):
    fact = observation()
    replay = [{**fact, 'source_trace': {**fact['source_trace'], 'ingested_at': str(i)}}
              for i in range(205)]
    assert ledger.append('fixture', replay) == 1
    assert ledger.append('fixture', [fact]) == 0
    assert len(ledger.read('fixture')) == 1


@pytest.mark.parametrize('change', [
    {'value': '101'}, {'basis': 'OFS'}, {'unit': '천원'}, {'currency': 'USD'},
    {'dimensions': {'Segment': 'A'}}, {'period_start': '2025-04-01'},
    {'filing_id': '20260304000001'}, {'published_at': '2026-03-04'},
    {'source_trace': {'source': 'fixture_non_production', 'sha256': 'corrected-document',
                      'ingested_at': '2026-03-06'}},
])
def test_economic_dimensions_disclosure_time_and_corrections_are_not_deduplicated(ledger, change):
    fact = observation()
    seed_legacy(ledger, fact)
    assert ledger.append('fixture', [{**fact, **change}]) == 1
    assert len(ledger.read('fixture')) == 2


def test_company_identity_is_not_shared_when_values_match(ledger):
    fact = observation()
    seed_legacy(ledger, fact)
    assert ledger.append('another-issuer', [fact]) == 1
    assert len(ledger.read('fixture')) == len(ledger.read('another-issuer')) == 1


def test_facts_without_new_clock_keep_previous_identity_contract(ledger):
    fact = observation()
    fact['source_trace'].pop('ingested_at')
    previous_id = seed_legacy(ledger, fact)
    assert ledger.append('fixture', [fact]) == 0
    with ledger.store.read_connection() as db:
        assert db.execute(select(ledger.facts.c.id)).scalars().all() == [previous_id]
