"""Fixture-only evidence: storage references must preserve every source field."""
import hashlib
import json
from copy import deepcopy

import pytest
from sqlalchemy import event, select

from services.market_dashboard.source_trace_store import (
    FORMAT,
    FORMAT_KEY,
    REF_KEY,
    SourceTraceIntegrityError,
    TraceRecord,
    compact_payloads,
    encode_json,
    storage_capabilities,
)
from services.market_dashboard.store import Store
from services.market_dashboard.time_series import put_series, stable


def trace(**changes):
    return {
        'source': 'fixture_non_production', 'source_url': 'https://example.invalid/공시',
        'sha256': 'a' * 64, 'filing_id': '20260303000001',
        'received_at': '2026-03-03T10:11:12+09:00', 'available_at': None,
        'unit': 'KRW', 'currency': 'KRW', 'formula': None,
        'precision': '0.000000000000000000001',
        'quality_flags': ['fixture_non_production', '한글 · 空白'],
        'adjustments': [{'verified': False, 'value': 0, 'factor': 1.25}],
        'input_fact_ids': [f'fixture-{i:04d}' for i in range(40)],
        **changes,
    }


@pytest.fixture
def reference_store(tmp_path, monkeypatch):
    monkeypatch.setenv('MARKET_SHARED_SOURCE_TRACES', '1')
    store = Store('sqlite://', tmp_path)
    yield store
    store.engine.dispose()


def stored_rows(store):
    with store.read_connection() as db:
        return db.execute(select(store.time_series.table.c.payload)).scalars().all()


def stored_traces(store):
    table = store.time_series.source_traces.table
    with store.read_connection() as db:
        return db.execute(select(table.c.digest, table.c.byte_count, table.c.payload)).all()


def seed_inline(store, key, row):
    digest = hashlib.sha256(
        json.dumps(stable(row), sort_keys=True, allow_nan=False).encode()).hexdigest()
    with store.engine.begin() as db:
        db.execute(store.time_series.table.insert().values(
            series=key, time=row['date'], digest=digest, payload=encode_json(row)))


def test_canonical_trace_hash_preserves_all_values_and_collection_clocks():
    original = trace()
    record = TraceRecord.from_value(original)
    reordered = {key: original[key] for key in reversed(list(original))}
    assert TraceRecord.from_value(reordered) == record
    assert record.byte_count == len(record.payload.encode('utf-8'))
    assert record.digest == hashlib.sha256(record.payload.encode('utf-8')).hexdigest()
    assert record.verified_value() == original
    assert TraceRecord.from_value(trace(received_at='2026-03-04')).digest != record.digest
    assert TraceRecord.from_value(trace(sha256='b' * 64)).digest != record.digest


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
def test_non_json_numeric_evidence_is_rejected(value):
    with pytest.raises(ValueError):
        TraceRecord.from_value(trace(value=value))


def test_reference_writes_are_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv('MARKET_SHARED_SOURCE_TRACES', raising=False)
    store = Store('sqlite://', tmp_path)
    row = {'date': '2026-03-03', 'close': 100, 'source_trace': trace()}
    store.time_series.upsert('fixture', [row])
    assert json.loads(stored_rows(store)[0]) == row
    assert stored_traces(store) == []


def test_exact_roundtrip_shared_source_restores_without_aliases(reference_store):
    store = reference_store
    rows = [{'date': f'2026-03-{day:02d}', 'close': day, 'source_trace': trace()}
            for day in range(1, 10)]
    original = deepcopy(rows)
    assert store.time_series.upsert('fixture', rows) == 9
    assert rows == original  # Compaction never mutates collector-owned values.
    assert len(stored_traces(store)) == 1
    bodies = [json.loads(body) for body in stored_rows(store)]
    assert all(body[FORMAT_KEY] == FORMAT for body in bodies)
    assert all(set(body['row']['source_trace']) == {REF_KEY} for body in bodies)
    restored = store.time_series.rows('fixture')
    assert restored == rows
    restored[0]['source_trace']['input_fact_ids'].append('changed-by-one-caller')
    assert restored[1]['source_trace'] == rows[1]['source_trace']
    assert store.time_series.rows('fixture') == rows


def test_legacy_inline_and_new_refs_share_order_filters_and_reverse(reference_store):
    store = reference_store
    rows = [{'date': f'2026-03-{day:02d}', 'close': day, 'source_trace': trace()}
            for day in range(1, 5)]
    seed_inline(store, 'fixture', rows[0])
    store.time_series.upsert('fixture', rows[1:])
    assert store.time_series.rows('fixture') == rows
    assert store.time_series.rows('fixture', start='2026-03-02', end='2026-03-03') == rows[1:3]
    assert store.time_series.rows('fixture', reverse=True, limit=2) == list(reversed(rows[2:]))
    assert json.loads(stored_rows(store)[0]) == rows[0]  # Reading did not migrate it.


def test_new_reader_restores_references_with_writes_disabled(tmp_path, monkeypatch):
    url = 'sqlite:///' + str(tmp_path / 'fixture.sqlite')
    monkeypatch.setenv('MARKET_SHARED_SOURCE_TRACES', '1')
    writer = Store(url, tmp_path)
    row = {'date': '2026-03-03', 'close': 100, 'source_trace': trace()}
    writer.time_series.upsert('fixture', [row])
    writer.engine.dispose()
    monkeypatch.setenv('MARKET_SHARED_SOURCE_TRACES', '0')
    reader = Store(url, tmp_path)
    assert not reader.time_series.shared_source_traces
    assert reader.time_series.rows('fixture') == [row]
    reader.engine.dispose()


def test_missing_null_and_small_evidence_keep_inline_shape(reference_store):
    rows = [{'date': '2026-03-01', 'close': 1},
            {'date': '2026-03-02', 'close': 2, 'source_trace': None},
            {'date': '2026-03-03', 'close': 3, 'source_trace': {}},
            {'date': '2026-03-04', 'close': 4, 'source_trace': {'source': 'fixture'}}]
    reference_store.time_series.upsert('fixture', rows)
    assert reference_store.time_series.rows('fixture') == rows
    assert [json.loads(body) for body in stored_rows(reference_store)] == rows
    assert stored_traces(reference_store) == []


def test_nested_evidence_and_marker_shaped_original_dict_roundtrip(reference_store):
    row = {'date': '2026-03-03', 'source_trace': trace(), 'investors': [
        {'name': 'foreign', 'source_trace': trace()},
        {'name': 'institution', 'source_trace': {REF_KEY: 'literal original value'}},
        {'name': 'individual', 'source_trace': None},
    ]}
    reference_store.time_series.upsert('fixture', [row])
    assert len(stored_traces(reference_store)) == 2
    assert reference_store.time_series.rows('fixture') == [row]


def test_unchanged_economic_row_does_not_write_new_trace_or_migrate(reference_store):
    store = reference_store
    original = {'date': '2026-03-03', 'close': 100, 'source_trace': trace()}
    seed_inline(store, 'fixture', original)
    replay = {**original, 'source_trace': trace(received_at='2026-03-04')}
    stats = {}
    assert store.time_series.upsert('fixture', [replay], stats=stats) == 0
    assert stats == {'inserted_rows': 0, 'updated_rows': 0, 'unchanged_rows': 1}
    assert stored_traces(store) == []
    assert store.time_series.rows('fixture') == [original]


def test_correction_keeps_original_evidence_and_changes_revision_atomically(reference_store):
    store = reference_store
    first = {'date': '2026-03-03', 'close': 100, 'source_trace': trace()}
    corrected = {**first, 'close': 101, 'source_trace': trace(sha256='b' * 64)}
    put_series(store, 'fixture', {'rows': [first], 'status': 'ready'})
    revision = store.revisions(['fixture'])
    put_series(store, 'fixture', {'rows': [corrected], 'status': 'ready'})
    assert store.revisions(['fixture']) != revision
    assert store.metadata('fixture')['row_count'] == 1
    assert store.time_series.rows('fixture') == [corrected]
    assert {json.loads(body)['sha256'] for _, _, body in stored_traces(store)} == {
        'a' * 64, 'b' * 64}


def test_row_reference_and_evidence_rollback_with_cursor_failure(reference_store, monkeypatch):
    store = reference_store
    def fail(*_args, **_kwargs):
        raise RuntimeError('fixture cursor failure')
    monkeypatch.setattr(store, 'put', fail)
    with pytest.raises(RuntimeError, match='fixture cursor'):
        put_series(store, 'fixture', {'rows': [
            {'date': '2026-03-03', 'close': 100, 'source_trace': trace()}
        ]})
    assert stored_rows(store) == []
    assert stored_traces(store) == []


@pytest.mark.parametrize('corruption', ['payload', 'byte_count', 'missing'])
def test_corrupt_evidence_blocks_read_without_fallback(reference_store, corruption):
    store = reference_store
    store.time_series.upsert('fixture', [
        {'date': '2026-03-03', 'close': 100, 'source_trace': trace()}])
    table = store.time_series.source_traces.table
    with store.engine.begin() as db:
        if corruption == 'missing':
            db.execute(table.delete())
        else:
            change = {'payload': '{}'} if corruption == 'payload' else {'byte_count': 1}
            db.execute(table.update().values(**change))
    with pytest.raises(SourceTraceIntegrityError):
        store.time_series.rows('fixture')


def test_conflicting_stored_hash_never_overwrites_evidence_or_inserts_row(reference_store):
    store = reference_store
    row = {'date': '2026-03-03', 'close': 100, 'source_trace': trace()}
    store.time_series.upsert('fixture', [row])
    table = store.time_series.source_traces.table
    with store.engine.begin() as db:
        db.execute(table.update().values(payload='{}', byte_count=2))
    with pytest.raises(SourceTraceIntegrityError):
        store.time_series.upsert('fixture', [{**row, 'date': '2026-03-04'}])
    assert len(stored_rows(store)) == 1
    assert stored_traces(store)[0].payload == '{}'


@pytest.mark.parametrize('envelope', [
    {FORMAT_KEY: 'future-format', 'row': {}},
    {FORMAT_KEY: FORMAT, 'row': {'source_trace': {REF_KEY: 'bad-reference'}}},
    {FORMAT_KEY: FORMAT, 'row': {'source_trace': {REF_KEY: 'a' * 64, 'extra': True}}},
    {FORMAT_KEY: FORMAT, 'row': None},
])
def test_invalid_reference_envelopes_fail_closed(reference_store, envelope):
    with pytest.raises(SourceTraceIntegrityError):
        reference_store.time_series.source_traces.restore_payloads([encode_json(envelope)])


def test_more_than_sqlite_999_references_use_bounded_bulk_queries(reference_store):
    store = reference_store
    rows = [{'date': f'2026-{i:06d}', 'value': i, 'source_trace': trace(filing_id=str(i))}
            for i in range(1100)]
    store.time_series.upsert('fixture', rows)
    queries = []
    def record(_db, _cursor, statement, parameters, _context, _many):
        if statement.startswith('SELECT') and 'market_source_traces' in statement:
            queries.append(len(parameters))
    event.listen(store.engine, 'before_cursor_execute', record)
    try:
        assert store.time_series.rows('fixture') == rows
    finally:
        event.remove(store.engine, 'before_cursor_execute', record)
    assert queries == [400, 400, 300]


def test_new_batch_reuses_trace_across_series_and_saves_payload_bytes(reference_store):
    store = reference_store
    rows = [{'date': f'2026-{i:06d}', 'close': i, 'source_trace': trace()} for i in range(100)]
    store.time_series.upsert('fixture:A', rows)
    store.time_series.upsert('fixture:B', rows)
    original_size = sum(len(encode_json(row).encode('utf-8')) for row in rows) * 2
    compact_size = sum(len(body.encode('utf-8')) for body in stored_rows(store))
    evidence_size = sum(len(body.encode('utf-8')) for _, _, body in stored_traces(store))
    assert len(stored_traces(store)) == 1
    assert compact_size + evidence_size < original_size * 0.3
    assert store.time_series.rows('fixture:A') == store.time_series.rows('fixture:B') == rows


def test_inline_reads_do_not_query_shared_evidence_table(reference_store):
    store = reference_store
    row = {'date': '2026-03-03', 'source_trace': {REF_KEY: 'legacy literal'}}
    seed_inline(store, 'fixture', row)
    statements = []
    def record(_db, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)
    event.listen(store.engine, 'before_cursor_execute', record)
    try:
        assert store.time_series.rows('fixture') == [row]
    finally:
        event.remove(store.engine, 'before_cursor_execute', record)
    assert not any('market_source_traces' in statement for statement in statements)


def test_small_rows_are_not_wrapped_just_to_create_a_reference():
    body = encode_json({'date': '2026-03-03', 'source_trace': {'source': 'fixture'}})
    assert compact_payloads([body]) == ([body], {})


def test_trace_hash_is_processed_after_connection_return(reference_store, monkeypatch):
    store = reference_store
    row = {'date': '2026-03-03', 'close': 100, 'source_trace': trace()}
    store.time_series.upsert('fixture', [row])
    checked_out = 0
    def checkout(*_args):
        nonlocal checked_out
        checked_out += 1
    def checkin(*_args):
        nonlocal checked_out
        checked_out -= 1
    original = TraceRecord.verified_value
    def verify_without_connection(record):
        assert checked_out == 0
        return original(record)
    event.listen(store.engine, 'checkout', checkout)
    event.listen(store.engine, 'checkin', checkin)
    monkeypatch.setattr(TraceRecord, 'verified_value', verify_without_connection)
    try:
        assert store.time_series.rows('fixture') == [row]
        assert checked_out == 0
    finally:
        event.remove(store.engine, 'checkout', checkout)
        event.remove(store.engine, 'checkin', checkin)


def test_deployment_capability_probe_does_not_need_store():
    assert storage_capabilities() == {
        'inline_time_series': True, 'read_formats': [FORMAT],
        'reference_write_env': 'MARKET_SHARED_SOURCE_TRACES',
        'reference_writes_default': False,
    }
