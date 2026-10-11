"""fixture_non_production: financial evidence compaction must be lossless."""
import copy
import json

import pytest
from sqlalchemy import create_engine, event, func, select
from test_fact_collection_identity import seed_legacy

from services.market_dashboard.fact_store import FactStore, fact_identity, storage_capabilities
from services.market_dashboard.share_basis_bootstrap import read_eps_facts
from services.market_dashboard.source_trace_store import (
    COLLECTION_CLOCKS,
    FINANCIAL_FORMAT,
    FORMAT,
    FORMAT_KEY,
    INLINE_KEY,
    REF_KEY,
    SourceTraceIntegrityError,
    compact_payloads,
    encode_json,
)
from services.market_dashboard.store import Store


def observations(size=10):
    trace = {'source': 'fixture_non_production', 'filing_id': '20260303000001',
             'available_at': '2026-03-03', 'sha256': 'a' * 64,
             'source_url': 'https://example.invalid/filing?' + 'context=' * 200,
             'input_fact_ids': [], 'unit': '원', 'formula': None}
    return [{'account_id': '{ifrs}BasicEarningsLossPerShare', 'period_end': '2025-12-31',
             'period_start': '2025-01-01', 'value': str(i), 'basis': 'CFS',
             'unit': 'KRW/share', 'currency': 'KRW', 'published_at': '2026-03-03',
             'filing_id': '20260303000001', 'dimensions': {'segment': str(i)},
             'source_trace': {**trace, 'ingested_at': f'2026-03-04T00:00:00.{i:06d}+00:00',
                              'received_at': None}}
            for i in range(size)]


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv('MARKET_SHARED_FINANCIAL_TRACES', '1')
    store = Store('sqlite://', tmp_path)
    result = FactStore(store)
    yield result
    store.engine.dispose()


def stored(ledger):
    with ledger.store.read_connection() as db:
        rows = db.execute(select(ledger.facts.c.id, ledger.facts.c.payload)).all()
        traces = db.execute(select(ledger.source_traces.table)).all()
    return rows, traces


def test_default_off_and_time_series_flag_cannot_enable_financial_format(tmp_path, monkeypatch):
    monkeypatch.delenv('MARKET_SHARED_FINANCIAL_TRACES', raising=False)
    monkeypatch.setenv('MARKET_SHARED_SOURCE_TRACES', '1')
    store = Store('sqlite://', tmp_path)
    try:
        ledger = FactStore(store)
        assert not ledger.shared_source_traces
        assert ledger.append('fixture', observations()) == 10
        rows, traces = stored(ledger)
        assert traces == [] and all(FORMAT_KEY not in json.loads(body) for _, body in rows)
    finally:
        store.engine.dispose()
    assert storage_capabilities() == {
        'inline_financial_facts': True, 'read_formats': [FINANCIAL_FORMAT],
        'reference_write_env': 'MARKET_SHARED_FINANCIAL_TRACES', 'reference_writes_default': False,
    }


def test_exact_roundtrip_clocks_ids_dimensions_publication_and_filtered_read(ledger):
    inputs = observations()
    originals = copy.deepcopy(inputs)
    assert ledger.append('fixture', inputs) == 10
    rows, traces = stored(ledger)
    assert len(traces) == 1
    assert all(json.loads(body)[FORMAT_KEY] == FINANCIAL_FORMAT for _, body in rows)
    expected = [{**fact, 'company_id': 'fixture'} for fact in inputs]
    expected = [{**row, 'fact_id': fact_identity(row)} for row in expected]
    actual = ledger.read('fixture')
    assert (sorted(actual, key=lambda r: r['fact_id'])
            == sorted(expected, key=lambda r: r['fact_id']))
    assert inputs == originals
    assert len(ledger.read('fixture', account_names={'BasicEarningsLossPerShare'})) == 10
    assert ledger.read('fixture', account_names={'Revenue'}) == []
    assert all(not (COLLECTION_CLOCKS & set(json.loads(row.payload))) for row in traces)
    with ledger.store.read_connection() as db:
        assert set(db.execute(select(ledger.facts.c.available_at)).scalars()) == {'2026-03-03'}
    actual[0]['source_trace']['input_fact_ids'].append('caller-change')
    assert actual[1]['source_trace']['input_fact_ids'] == []
    assert ledger.read('fixture')[0]['source_trace']['input_fact_ids'] == []


def test_legacy_inline_corrections_and_replay_keep_all_existing_ids(ledger):
    original = observations()
    ledger.shared_source_traces = False
    assert ledger.append('fixture', original[:2]) == 2
    old = stored(ledger)[0]
    ledger.shared_source_traces = True
    assert ledger.append('fixture', original) == 8
    first = ledger.read('fixture')
    replay = [{**row, 'source_trace': {**row['source_trace'],
               'ingested_at': '2026-03-05', 'collected_at': '2026-03-05'}} for row in original]
    assert ledger.append('fixture', replay) == 0
    assert ledger.read('fixture') == first
    assert set(old) <= set(stored(ledger)[0])
    corrected = {**original[0], 'value': '100', 'published_at': '2026-03-06',
                 'filing_id': '20260306000001'}
    assert ledger.append('fixture', [corrected]) == 1
    after = ledger.read('fixture')
    assert {r['fact_id'] for r in first} < {r['fact_id'] for r in after}
    assert {r['published_at'] for r in after} == {'2026-03-03', '2026-03-06'}


def test_legacy_clock_based_ids_survive_reference_storage_and_replay(ledger):
    facts = observations()
    for fact in facts:
        fact['value'] = '100'  # The existing legacy seeder stores this exact value.
    identities = [seed_legacy(ledger, fact) for fact in facts]
    rows, _ = stored(ledger)
    compact, traces = compact_payloads([body for _, body in rows], format=FINANCIAL_FORMAT)
    with ledger.store.engine.begin() as db:
        ledger.source_traces.persist(traces, db)
        for (identity, _), body in zip(rows, compact, strict=True):
            db.execute(ledger.facts.update().where(ledger.facts.c.id == identity)
                       .values(payload=body))
    before = ledger.read('fixture')
    replay = [{**fact, 'source_trace': {**fact['source_trace'], 'ingested_at': 'later'}}
              for fact in facts]
    assert ledger.append('fixture', replay) == 0
    assert ledger.read('fixture') == before
    assert {row['fact_id'] for row in before} == set(identities)


def test_eps_observation_reader_restores_refs_without_schema_or_data_writes(ledger):
    ledger.append('fixture', observations())
    statements = []
    def record(_db, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)
    event.listen(ledger.store.engine, 'before_cursor_execute', record)
    try:
        actual = read_eps_facts(ledger.store, 'fixture')
    finally:
        event.remove(ledger.store.engine, 'before_cursor_execute', record)
    assert {r['fact_id'] for r in actual} == {r['fact_id'] for r in ledger.read('fixture')}
    assert all(statement.lstrip().upper().startswith('SELECT') for statement in statements)
    assert all('ingested_at' in row['source_trace'] for row in actual)


def test_clocked_replay_uses_existing_transaction_with_one_connection(tmp_path, monkeypatch):
    monkeypatch.setenv('MARKET_SHARED_FINANCIAL_TRACES', '1')
    url = 'sqlite:///' + str(tmp_path/'isolated.db').replace('\\', '/')
    store = Store(url, tmp_path/'raw')
    store.engine.dispose()
    store.engine = create_engine(url, pool_size=1, max_overflow=0, pool_timeout=.1)
    try:
        ledger = FactStore(store)
        assert ledger.append('fixture', observations(120)) == 120
        assert ledger.append('fixture', observations(120)) == 0
        assert len(ledger.read('fixture')) == 120
        assert len(read_eps_facts(store, 'fixture')) == 120
    finally:
        store.engine.dispose()


def test_fact_and_shared_evidence_roll_back_together_across_batches(ledger):
    writes = 0
    def fail_second(_db, _cursor, statement, _parameters, _context, _many):
        nonlocal writes
        if statement.startswith('INSERT INTO market_financial_facts'):
            writes += 1
            if writes == 2:
                raise RuntimeError('fixture rollback')
    event.listen(ledger.store.engine, 'before_cursor_execute', fail_second)
    try:
        with pytest.raises(RuntimeError, match='fixture rollback'):
            ledger.append('fixture', observations(120))
    finally:
        event.remove(ledger.store.engine, 'before_cursor_execute', fail_second)
    assert stored(ledger) == ([], [])


@pytest.mark.parametrize('corruption', ['missing', 'payload', 'byte_count'])
def test_missing_or_corrupt_shared_evidence_blocks_financial_reads(ledger, corruption):
    ledger.append('fixture', observations())
    table = ledger.source_traces.table
    with ledger.store.engine.begin() as db:
        if corruption == 'missing':
            db.execute(table.delete())
        else:
            db.execute(table.update().values(**{'payload': '{}'} if corruption == 'payload'
                                            else {'byte_count': 1}))
    with pytest.raises(SourceTraceIntegrityError):
        ledger.read('fixture')
    with pytest.raises(SourceTraceIntegrityError):
        read_eps_facts(ledger.store, 'fixture')


def test_unique_contexts_do_not_grow_storage_just_to_create_refs():
    bodies = [encode_json({**row, 'source_trace': {**row['source_trace'], 'context_id': str(i)}})
              for i, row in enumerate(observations())]
    assert compact_payloads(bodies, format=FINANCIAL_FORMAT) == (bodies, {})


@pytest.mark.parametrize('format,fields', [
    (FORMAT, {'ingested_at': '2026-03-03'}),
    (FINANCIAL_FORMAT, {'available_at': '1900-01-01'}),
    (FINANCIAL_FORMAT, None),
])
def test_inline_fields_cannot_override_publication_or_change_old_format(ledger, format, fields):
    body = encode_json({FORMAT_KEY: format, 'row': {
        'source_trace': {REF_KEY: 'a' * 64, INLINE_KEY: fields}}})
    with pytest.raises(SourceTraceIntegrityError):
        ledger.source_traces.restore_payloads([body])


def test_reference_rows_keep_company_and_semantic_differences_separate(ledger):
    rows = observations()
    ledger.append('fixture', rows)
    ledger.append('another', rows)
    assert len(ledger.read('fixture')) == len(ledger.read('another')) == 10
    assert not ({r['fact_id'] for r in ledger.read('fixture')}
                & {r['fact_id'] for r in ledger.read('another')})
    with ledger.store.read_connection() as db:
        count = db.execute(select(func.count()).select_from(ledger.source_traces.table)).scalar()
    assert count == 1
