"""fixture_non_production: independent investor providers must not block Toss."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, timedelta
from threading import Event

import pytest

from services.market_dashboard.store import Store
from services.market_dashboard.time_series import put_series
from services.market_dashboard.trading_projection import (
    REFERENCE,
    TradingProjectionUnavailable,
    compact_projection,
    normalize_rows,
    read_flows,
)

CODE = '035900'
RAW = f'toss:investor-trading:{CODE}'
NORMALIZED = f'toss-normalized:flows:{CODE}'
DETAIL = f'detail:{CODE}'
MARKER = f'toss-projection:investor-trading:{CODE}'
AUDIT = f'toss-compaction:investor-trading:{CODE}'
STATE = {'status': 'ready'}


@pytest.fixture
def store(tmp_path):
    value = Store('sqlite://', tmp_path)
    yield value
    value.engine.dispose()


def raw(day, value, digest='a'):
    return {'date': day, 'individual': {'netBuyVolume': value},
            'foreigner': {'netBuyVolume': -value}, 'institution': {'netBuyVolume': 0},
            'source_trace': {'source': 'toss', 'source_document_id': digest * 64,
                             'fixture_non_production': True}}


def arrange(store):
    prior = {'company': {'name': 'fixture_non_production'}, 'flows': [
        {'date': '2026-10-02', 'individual': 80, 'foreign': -80, 'institution': None,
         'source_trace': {'source': 'naver', 'scope': 'KRX',
                          'fixture_non_production': True}},
        {'date': '2026-09-30', 'individual': 9, 'foreign': -9, 'institution': None,
         'source_trace': {'source': 'naver', 'scope': 'KRX',
                          'fixture_non_production': True}},
    ]}
    store.put(DETAIL, prior)
    store.put(f'naver:flows:{CODE}', {'rows': prior['flows']})
    store.put(f'collection:{CODE}', {'sources': {'flows': {
        'status': 'ready', 'source': 'naver', 'as_of': '2026-10-02',
        'received_at': '2026-10-03T00:00:00+00:00'}}})
    put_series(store, RAW, {'rows': [raw('2026-10-02', 2), raw('2026-10-08', 8)],
                           'status': 'ready', 'backfill_complete': False,
                           'head_received_at': '2026-10-10T05:07:58+00:00'}, reverse=True)
    return deepcopy(prior)


def test_first_toss_projection_preserves_naver_and_records_actual_checked_rows(store):
    prior = arrange(store)
    originals = store.metadata(RAW), store.time_series.rows(RAW)
    compact_projection(store, CODE, 'investor-trading', STATE)
    assert store.metadata(DETAIL) == prior
    assert store.metadata(f'naver:flows:{CODE}') == {'rows': prior['flows']}
    assert (store.metadata(RAW), store.time_series.rows(RAW)) == originals
    normalized = store.metadata(NORMALIZED, {})
    assert normalized.get(REFERENCE, {}).get('fields', {}).get('rows', {}).get('source_key') == RAW
    assert [(row['date'], row['individual'], row['source_trace']['source'])
            for row in read_flows(store, CODE)] == [
                ('2026-10-08', 8, 'toss'), ('2026-10-02', 2, 'toss'),
                ('2026-09-30', 9, 'naver')]
    receipt = store.metadata(f'collection:{CODE}')['sources']['flows']
    assert (receipt['source'], receipt['status'], receipt['as_of'], receipt['validated_rows']) == (
        'toss', 'ready', '2026-10-08', 2)
    assert receipt['period'] == {'start': '2026-10-02', 'end': '2026-10-08'}
    assert receipt['received_at'] == '2026-10-10T05:07:58+00:00'
    assert receipt['backfill_complete'] is False


def test_previous_preservation_attempt_does_not_permanently_block_new_policy(store):
    prior = arrange(store)
    source = store.metadata(RAW)
    store.put(AUDIT, {'status': 'legacy_preserved', 'input': {
        'version': 3, 'normalizer': 'toss-trading-reference-v1',
        'revision': source['series_revision'], 'rows': source['row_count'], 'status': 'ready'},
        'output_revisions': {key: store.revisions([key]).get(key) for key in (NORMALIZED, DETAIL)}})
    compact_projection(store, CODE, 'investor-trading', STATE)
    assert store.metadata(DETAIL) == prior
    assert store.metadata(NORMALIZED, {}).get(REFERENCE)
    assert store.metadata(MARKER)['collection_receipt']['flows']['as_of'] == '2026-10-08'


def test_unchanged_retry_keeps_projection_small_and_preserved_values_exact(store, monkeypatch):
    prior = arrange(store)
    compact_projection(store, CODE, 'investor-trading', STATE)
    before = store.metadata(NORMALIZED), store.revisions([NORMALIZED, DETAIL])
    def no_history_read(*args, **kwargs):
        raise AssertionError('Unchanged projection must not read the history')
    monkeypatch.setattr(store.time_series, 'rows', no_history_read)
    compact_projection(store, CODE, 'investor-trading', STATE)
    assert (store.metadata(NORMALIZED), store.revisions([NORMALIZED, DETAIL])) == before
    marker = store.metadata(MARKER)
    assert marker['rebuilt'] == 1 and marker['skipped'] == 1
    assert set(marker['outputs']) == {NORMALIZED}
    assert set(marker['preserved_outputs']) == {DETAIL}
    assert len(store.metadata(NORMALIZED)['rows']) == 1
    assert store.metadata(DETAIL) == prior


@pytest.mark.parametrize('change', ['new_day', 'historical_correction'])
def test_later_source_revision_advances_from_persisted_proof_without_replacing_naver(store, change):
    prior = arrange(store)
    compact_projection(store, CODE, 'investor-trading', STATE)
    day = '2026-10-09' if change == 'new_day' else '2026-10-02'
    source = store.metadata(RAW)
    put_series(store, RAW, {**source, 'rows': [raw(day, 42, 'b')],
                           'head_received_at': '2026-10-11T01:00:00+00:00'}, reverse=True)
    # The function has no process-local state: the stored proof is sufficient.
    compact_projection(store, CODE, 'investor-trading', STATE)
    result = {row['date']: row for row in read_flows(store, CODE)}
    assert result[day]['individual'] == 42
    assert result[day]['source_trace']['source_document_id'] == 'b' * 64
    assert result['2026-09-30'] == prior['flows'][1]
    assert store.metadata(DETAIL) == prior
    assert len(store.metadata(NORMALIZED)['rows']) == 1
    receipt = store.metadata(f'collection:{CODE}')['sources']['flows']
    assert receipt['as_of'] == max(day, '2026-10-08')
    assert receipt['source_revision'] == store.metadata(RAW)['series_revision']


@pytest.mark.parametrize('damage', ['untraced', 'foreign_source', 'missing_values', 'empty'])
def test_independent_source_must_not_turn_invalid_raw_into_a_ready_receipt(store, damage):
    prior = arrange(store)
    with store.engine.begin() as db:
        db.execute(store.time_series.table.delete().where(store.time_series.table.c.series == RAW))
        db.execute(store.snapshots.delete().where(store.snapshots.c.key == RAW))
    row = raw('2026-10-08', 8)
    if damage == 'untraced':
        row['source_trace'].pop('source_document_id')
    elif damage == 'foreign_source':
        row['source_trace']['source'] = 'naver'
    elif damage == 'missing_values':
        for field in ('individual', 'foreigner', 'institution'):
            row[field] = {'netBuyVolume': None}
    put_series(store, RAW, {'rows': [] if damage == 'empty' else [row], 'status': 'ready'},
               reverse=True)
    compact_projection(store, CODE, 'investor-trading', STATE)
    assert store.metadata(DETAIL) == prior
    assert store.metadata(NORMALIZED) is None
    assert read_flows(store, CODE) == prior['flows']
    receipt = store.metadata(f'collection:{CODE}')['sources']['flows']
    assert (receipt['source'], receipt['as_of'], receipt['status']) == (
        'naver', '2026-10-02', 'stale')


def test_unmatched_toss_normalized_values_are_not_certified_by_foreign_detail_exception(store):
    prior = arrange(store)
    source = store.metadata(RAW)
    normalized = normalize_rows('investor-trading', store.time_series.rows(RAW), source, 'ready')
    normalized['rows'][0]['individual'] = 12345
    store.put(NORMALIZED, normalized)
    compact_projection(store, CODE, 'investor-trading', STATE)
    assert store.metadata(NORMALIZED) == normalized
    assert store.metadata(DETAIL) == prior
    assert store.metadata(MARKER) is None
    assert store.metadata(AUDIT)['reason'] == 'owned_fields_not_equivalent'


def test_mixed_legacy_detail_remains_byte_equivalent_with_all_source_identities(store):
    prior = arrange(store)
    prior['flows'].append({
        'date': '2026-09-29', 'individual': 33,
        'source_trace': {'source': 'toss', 'fixture_non_production': True}})
    store.put(DETAIL, prior)
    compact_projection(store, CODE, 'investor-trading', STATE)
    assert store.metadata(DETAIL) == prior
    observed = {row['date']: row for row in read_flows(store, CODE)}
    assert observed['2026-09-29'] == prior['flows'][-1]
    assert observed['2026-10-02']['source_trace']['source'] == 'toss'
    assert store.metadata(DETAIL)['flows'][0]['source_trace']['source'] == 'naver'


@pytest.mark.parametrize('racing_key', [RAW, DETAIL, NORMALIZED])
def test_concurrent_changes_abort_independent_reference_and_receipt(store, monkeypatch, racing_key):
    arrange(store)
    from services.market_dashboard import trading_projection as module
    summary = module._summary
    def race(*args):
        result = summary(*args)
        if racing_key == RAW:
            put_series(store, RAW, {**store.metadata(RAW), 'rows': [raw('2026-10-09', 9)]},
                       reverse=True)
        else:
            store.put(racing_key, {**store.metadata(racing_key, {}), 'independent': 'keep'})
        return result
    monkeypatch.setattr(module, '_summary', race)
    compact_projection(store, CODE, 'investor-trading', STATE)
    assert store.metadata(MARKER) is None
    assert REFERENCE not in store.metadata(NORMALIZED, {})
    assert store.metadata(f'collection:{CODE}')['sources']['flows']['source'] == 'naver'
    if racing_key != RAW:
        assert store.metadata(racing_key)['independent'] == 'keep'


def test_failed_receipt_commit_rolls_back_independent_projection(store, monkeypatch):
    prior = arrange(store)
    put = store.put
    def fail(key, *args, **kwargs):
        if key == f'collection:{CODE}':
            raise RuntimeError('fixture_non_production interrupted transaction')
        return put(key, *args, **kwargs)
    monkeypatch.setattr(store, 'put', fail)
    with pytest.raises(RuntimeError):
        compact_projection(store, CODE, 'investor-trading', STATE)
    assert store.metadata(NORMALIZED) is None
    assert store.metadata(MARKER) is None
    assert store.metadata(AUDIT) is None
    assert store.metadata(DETAIL) == prior


def test_regressed_or_cross_security_reference_remains_blocked_with_naver_detail(store):
    prior = arrange(store)
    compact_projection(store, CODE, 'investor-trading', STATE)
    normalized = store.metadata(NORMALIZED)
    normalized[REFERENCE]['fields']['rows']['source_key'] = 'toss:investor-trading:005930'
    store.put(NORMALIZED, normalized)
    with pytest.raises(TradingProjectionUnavailable):
        compact_projection(store, CODE, 'investor-trading', STATE)
    with pytest.raises(TradingProjectionUnavailable):
        read_flows(store, CODE)
    assert store.metadata(DETAIL) == prior


def test_isolated_projection_survives_reopen_and_shared_trace_storage(tmp_path, monkeypatch):
    monkeypatch.setenv('MARKET_SHARED_SOURCE_TRACES', '1')
    url = 'sqlite:///' + (tmp_path / 'restart.sqlite3').as_posix()
    first = Store(url, tmp_path / 'raw')
    prior = arrange(first)
    compact_projection(first, CODE, 'investor-trading', STATE)
    expected = read_flows(first, CODE)
    first.engine.dispose()
    # Reopening the persisted database also restores normalized trace references.
    second = Store(url, tmp_path / 'raw')
    try:
        assert read_flows(second, CODE) == expected
        compact_projection(second, CODE, 'investor-trading', STATE)
        assert second.metadata(MARKER)['skipped'] == 1
        assert second.metadata(DETAIL) == prior
        assert second.metadata(f'collection:{CODE}')['sources']['flows']['as_of'] == '2026-10-08'
    finally:
        second.engine.dispose()


def test_removed_retained_source_date_is_rejected_and_never_erases_naver(store):
    prior = arrange(store)
    compact_projection(store, CODE, 'investor-trading', STATE)
    source = store.metadata(RAW)
    with store.engine.begin() as db:
        db.execute(store.time_series.table.delete().where(
            store.time_series.table.c.series == RAW,
            store.time_series.table.c.time == '2026-10-02'))
    store.put(RAW, {**source, 'row_count': 1, 'first_time': '2026-10-08',
                    'series_revision': 'fixture_non_production_removed_range'})
    for operation in (
        lambda: compact_projection(store, CODE, 'investor-trading', STATE),
        lambda: read_flows(store, CODE),
    ):
        with pytest.raises(TradingProjectionUnavailable):
            operation()
    assert store.metadata(DETAIL) == prior


@pytest.mark.parametrize('view', ['activity', 'full'])
def test_public_detail_reads_new_source_and_correction_with_preserved_legacy(
        store, monkeypatch, view):
    from fastapi.testclient import TestClient

    from services.market_dashboard import app as market
    from services.market_dashboard.response_cache import RevisionResponseCache
    prior = arrange(store)
    store.put('universe', {'rows': [
        {'code': CODE, 'name': 'fixture_non_production', 'market': 'KOSDAQ',
         'price': 100, 'as_of': '2026-10-08', 'source_trace': {}}
    ]})
    monkeypatch.setattr(market, 'get_store', lambda: store)
    monkeypatch.setattr(market, '_detail_responses', RevisionResponseCache())
    market._rows_cache.clear()
    try:
        with TestClient(market.app) as client:
            path = market.BASE + f'/securities/{CODE}?view={view}'
            old = client.get(path)
            compact_projection(store, CODE, 'investor-trading', STATE)
            new = client.get(path, headers={'If-None-Match': old.headers['etag']})
            assert new.status_code == 200
            assert new.json()['detail']['flows'][0]['date'] == '2026-10-08'
            assert new.json()['detail']['flows'][0]['individual'] == 8
            assert new.headers['etag'] != old.headers['etag']
            assert REFERENCE not in new.text
            put_series(store, RAW, {**store.metadata(RAW),
                                   'rows': [raw('2026-10-08', 12, 'b')]}, reverse=True)
            corrected = client.get(path, headers={'If-None-Match': new.headers['etag']})
            assert corrected.status_code == 200
            assert corrected.json()['detail']['flows'][0]['individual'] == 12
            assert store.metadata(DETAIL) == prior
    finally:
        market._rows_cache.clear()


def test_markerless_toss_prefix_can_add_verified_dates_while_preserving_mixed_detail(store):
    prior = arrange(store)
    days = [(date(2026, 5, 8) + timedelta(days=i)).isoformat() for i in range(103)]
    data = [raw(day, i, 'c') for i, day in enumerate(days)]
    with store.engine.begin() as db:
        db.execute(store.time_series.table.delete().where(store.time_series.table.c.series == RAW))
        db.execute(store.snapshots.delete().where(store.snapshots.c.key == RAW))
    put_series(store, RAW, {'rows': data, 'status': 'ready'}, reverse=True)
    source = store.metadata(RAW)
    normalized = normalize_rows('investor-trading', data[:100], source, 'ready')
    store.put(NORMALIZED, normalized)
    prior['flows'].extend(deepcopy(normalized['rows']))
    store.put(DETAIL, prior)
    assert store.metadata(MARKER) is None
    compact_projection(store, CODE, 'investor-trading', STATE)
    assert store.metadata(AUDIT)['verification']['method'] == 'retained-toss-row-subset-v1'
    assert len(store.metadata(NORMALIZED)['rows']) == 1
    assert store.metadata(DETAIL) == prior
    actual = {row['date']: row for row in read_flows(store, CODE)}
    for row in normalize_rows('investor-trading', data, source, 'ready')['rows']:
        assert actual[row['date']] == row
    assert store.metadata(f'collection:{CODE}')['sources']['flows']['validated_rows'] == 103


@pytest.mark.parametrize('damage', ['value', 'document', 'unit', 'scope', 'missing_date',
                                  'duplicate_date', 'missing_trace'])
def test_markerless_prefix_requires_every_date_value_and_complete_evidence(store, damage):
    prior = arrange(store)
    source = store.metadata(RAW)
    normalized = normalize_rows('investor-trading', store.time_series.rows(RAW), source, 'ready')
    normalized['rows'] = normalized['rows'][1:]
    row = normalized['rows'][0]
    if damage == 'value':
        row['individual'] = 99
    elif damage in {'document', 'unit', 'scope'}:
        key = 'source_document_id' if damage == 'document' else damage
        row['source_trace'][key] = 'b' * 64 if damage == 'document' else 'different'
    elif damage == 'missing_date':
        row['date'] = '2025-01-01'
    elif damage == 'duplicate_date':
        normalized['rows'].append(deepcopy(row))
    elif damage == 'missing_trace':
        row.pop('source_trace')
    store.put(NORMALIZED, normalized)
    compact_projection(store, CODE, 'investor-trading', STATE)
    assert store.metadata(NORMALIZED) == normalized
    assert store.metadata(DETAIL) == prior
    assert store.metadata(MARKER) is None
    assert store.metadata(AUDIT)['reason'] == 'owned_fields_not_equivalent'


def null_flow(day):
    return {'date': day, 'source_trace': {'source': 'toss', 'source_document_id': 'd' * 64,
                                         'fixture_non_production': True}}


def test_one_valid_date_does_not_certify_another_all_null_date(store):
    prior = arrange(store)
    put_series(store, RAW, {**store.metadata(RAW), 'rows': [null_flow('2026-10-02')]},
               reverse=True)
    compact_projection(store, CODE, 'investor-trading', STATE)
    assert store.metadata(NORMALIZED) is None
    assert store.metadata(MARKER) is None
    assert read_flows(store, CODE) == prior['flows']
    assert store.metadata(AUDIT)['reason'] == 'isolated_source_unverified'


@pytest.mark.parametrize('day', ['2026-10-02', '2026-10-08', '2026-10-09'])
def test_later_null_source_never_masks_last_normal_observation(store, day):
    prior = arrange(store)
    compact_projection(store, CODE, 'investor-trading', STATE)
    if day == '2026-10-09':
        prior['flows'].insert(0, {**prior['flows'][0], 'date': day})
        store.put(DETAIL, prior)
    expected = {row['date']: row for row in read_flows(store, CODE)}[day]
    if day == '2026-10-02':
        # This date is no longer in the one-row Toss summary. The retained
        # Naver observation stays Naver; we cannot reconstruct old Toss facts.
        expected = prior['flows'][0]
    put_series(store, RAW, {**store.metadata(RAW), 'rows': [null_flow(day)]}, reverse=True)
    # References immediately see revisions, before the projection worker runs.
    assert {row['date']: row for row in read_flows(store, CODE)}[day] == expected
    compact_projection(store, CODE, 'investor-trading', STATE)
    assert {row['date']: row for row in read_flows(store, CODE)}[day] == expected
    assert store.metadata(DETAIL) == prior
    receipt = store.metadata(f'collection:{CODE}')['sources']['flows']
    if day >= '2026-10-08':
        assert receipt['status'] == 'stale'
        assert receipt['as_of'] == '2026-10-08'


def test_unchanged_receipt_and_marker_cannot_overwrite_newer_collector(tmp_path, monkeypatch):
    url = 'sqlite:///' + (tmp_path / 'concurrent.sqlite3').as_posix()
    old = Store(url, tmp_path / 'raw')
    new = Store(url, tmp_path / 'raw')
    start, attempted, committed = Event(), Event(), Event()
    try:
        arrange(old)
        compact_projection(old, CODE, 'investor-trading', STATE)
        def concurrent_collector():
            assert start.wait(3)
            attempted.set()
            put_series(new, RAW, {**new.metadata(RAW), 'rows': [raw('2026-10-09', 9, 'b')],
                                 'head_received_at': '2026-10-11T01:00:00+00:00'}, reverse=True)
            compact_projection(new, CODE, 'investor-trading', STATE)
            committed.set()
        put = old.put
        def pause_after_final_revision_check(key, *args, **kwargs):
            if key == MARKER:
                start.set()
                assert attempted.wait(3)
                # The new collector either finishes first (old unsafe code) or
                # waits for this atomic transaction, then correctly wins last.
                committed.wait(0.15)
            return put(key, *args, **kwargs)
        monkeypatch.setattr(old, 'put', pause_after_final_revision_check)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(concurrent_collector)
            compact_projection(old, CODE, 'investor-trading', STATE)
            future.result(timeout=5)
        assert old.metadata(MARKER)['input']['revision'] == old.metadata(RAW)['series_revision']
        assert old.metadata(f'collection:{CODE}')['sources']['flows']['as_of'] == '2026-10-09'
        assert old.metadata(f'collection:{CODE}')['sources']['flows']['received_at'] == (
            '2026-10-11T01:00:00+00:00')
    finally:
        old.engine.dispose()
        new.engine.dispose()


def documented_row(day, value, digest, observed):
    row = raw(day, value, digest)
    row['source_trace'].update({
        'filing_id': digest * 64, 'accession_number': digest * 64, 'ingested_at': observed,
        'source_url': f'https://openapi.tossinvest.com/api/v1/stocks/{CODE}/investor-trading?count=100',
        'unit': '주', 'currency': 'KRW'})
    return row


@pytest.mark.parametrize('prior_marker', [False, True])
def test_reobserved_equal_values_keep_old_complete_evidence_atomically(store, prior_marker):
    from services.market_dashboard.toss import projection_field_hash
    prior = arrange(store)
    old_row = documented_row('2026-10-08', 8, 'a', '2026-10-09T00:00:00Z')
    new_row = documented_row('2026-10-08', 8, 'b', '2026-10-10T00:00:00Z')
    put_series(store, RAW, {**store.metadata(RAW), 'rows': [{**new_row, 'providerExtra': 1}]},
               reverse=True)
    source = store.metadata(RAW)
    old = normalize_rows('investor-trading', [old_row], source, 'ready')
    store.put(NORMALIZED, old)
    if prior_marker:
        store.put(MARKER, {'input': {'version': 2, 'revision': 'prior_document', 'rows': 1},
                           'output_field_hashes': {NORMALIZED: projection_field_hash(
                               old, ('rows', 'status'))}})
    compact_projection(store, CODE, 'investor-trading', STATE)
    marker = store.metadata(MARKER)
    archive_keys = marker['retained_observation_keys']
    assert len(archive_keys) == 1 and len(archive_keys[0]) <= 128
    saved = store.metadata(archive_keys[0])
    assert saved['rows'] == old['rows']
    assert saved['source_key'] == RAW and saved['code'] == CODE
    assert store.metadata(DETAIL) == prior
    assert read_flows(store, CODE)[0]['source_trace']['source_document_id'] == 'b' * 64
    revisions = store.revisions(archive_keys)
    compact_projection(store, CODE, 'investor-trading', STATE)
    assert store.revisions(archive_keys) == revisions
    assert store.metadata(MARKER)['retained_observation_keys'] == archive_keys


@pytest.mark.parametrize('damage', ['older_time', 'invalid_time', 'filing_identity',
                                  'other_company', 'other_host', 'scope', 'value'])
def test_reobserved_metadata_is_not_a_blanket_evidence_exception(store, damage):
    prior = arrange(store)
    old_row = documented_row('2026-10-08', 8, 'a', '2026-10-09T00:00:00Z')
    new_row = documented_row('2026-10-08', 8, 'b', '2026-10-10T00:00:00Z')
    if damage == 'older_time':
        new_row['source_trace']['ingested_at'] = '2026-10-08T00:00:00Z'
    elif damage == 'invalid_time':
        new_row['source_trace']['ingested_at'] = '2026-10-10T00:00:00'
    elif damage == 'filing_identity':
        old_row['source_trace']['filing_id'] = 'c' * 64
    elif damage in {'other_company', 'other_host'}:
        address = ('https://openapi.tossinvest.com/api/v1/stocks/005930/investor-trading'
                   if damage == 'other_company' else 'https://invalid.test/api/investor-trading')
        for row in (old_row, new_row):
            row['source_trace']['source_url'] = address
    elif damage == 'scope':
        old_row['source_trace']['unit'] = '백만원'
    else:
        old_row['individual']['netBuyVolume'] = 99
    put_series(store, RAW, {**store.metadata(RAW), 'rows': [{**new_row, 'providerExtra': 1}]},
               reverse=True)
    old = normalize_rows('investor-trading', [old_row], store.metadata(RAW), 'ready')
    store.put(NORMALIZED, old)
    compact_projection(store, CODE, 'investor-trading', STATE)
    assert store.metadata(NORMALIZED) == old
    assert store.metadata(DETAIL) == prior
    assert store.metadata(MARKER) is None


def test_retained_evidence_is_insert_only_and_rolls_back_with_projection(store, monkeypatch):
    from services.market_dashboard.trading_projection import _retain_flow_observations
    prior = arrange(store)
    old_row = documented_row('2026-10-08', 8, 'a', '2026-10-09T00:00:00Z')
    new_row = documented_row('2026-10-08', 8, 'b', '2026-10-10T00:00:00Z')
    put_series(store, RAW, {**store.metadata(RAW), 'rows': [{**new_row, 'providerExtra': 1}]},
               reverse=True)
    old = normalize_rows('investor-trading', [old_row], store.metadata(RAW), 'ready')
    store.put(NORMALIZED, old)
    put = store.put
    def fail(key, *args, **kwargs):
        if key == f'collection:{CODE}':
            raise RuntimeError('fixture_non_production collection failure')
        return put(key, *args, **kwargs)
    monkeypatch.setattr(store, 'put', fail)
    with pytest.raises(RuntimeError):
        compact_projection(store, CODE, 'investor-trading', STATE)
    from sqlalchemy import select
    with store.read_connection() as db:
        assert not db.execute(select(store.snapshots.c.key).where(
            store.snapshots.c.key.like('toss-retained-flows:%'))).all()
    assert store.metadata(NORMALIZED) == old
    assert store.metadata(DETAIL) == prior
    # A corrupted pre-existing content address is never overwritten.
    with store.engine.begin() as db:
        key = _retain_flow_observations(store, db, CODE, old['rows'])
    put(key, {'invalid': 'fixture_non_production'})
    with store.engine.begin() as db, pytest.raises(TradingProjectionUnavailable):
        _retain_flow_observations(store, db, CODE, old['rows'])
    assert store.metadata(key) == {'invalid': 'fixture_non_production'}
