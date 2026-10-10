"""fixture_non_production: exact facts, independently retained page evidence."""
from copy import deepcopy

import pytest

from services.market_dashboard import trading_projection as p
from services.market_dashboard.store import Store
from services.market_dashboard.time_series import put_series
from services.market_dashboard.toss import TRADING_PROJECTION_FIELDS

CODE = '035900'
ENDPOINTS = ('credit-trades', 'securities-lending', 'short-selling')


@pytest.fixture
def store(tmp_path):
    result = Store('sqlite://', tmp_path)
    yield result
    result.engine.dispose()


def raw(endpoint, day, digest='a', stamp='2026-10-03T00:00:00+00:00'):
    return {'date': day, 'marginLoan': {'balanceQuantity': 20},
            'stockLoan': {'balanceQuantity': 0}, 'balanceQuantity': 30,
            'shortSellingVolume': 40, 'shortSellingAmount': 800,
            'source_trace': {'source': 'toss', 'source_document_id': digest * 64,
                'filing_id': digest * 64, 'accession_number': digest * 64,
                'ingested_at': stamp, 'source_url': f'https://openapi.tossinvest.com/api/v1/stocks/{CODE}/{endpoint}',
                'unit': '주', 'currency': 'KRW', 'scope': '국내', 'fixture_non_production': True}}


def arrange(store, endpoint):
    key = f'toss:{endpoint}:{CODE}'
    old_rows = [raw(endpoint, '2026-10-02')]
    old = p.normalize_rows(endpoint, old_rows, {}, 'ready')
    for prefix in TRADING_PROJECTION_FIELDS[endpoint]:
        store.put(prefix + CODE, deepcopy(old))
    newer = [raw(endpoint, day, 'b', '2026-10-10T00:00:00+00:00')
             for day in ('2026-10-02', '2026-10-08')]
    put_series(store, key, {'rows': newer, 'status': 'ready', 'backfill_complete': False},
               reverse=True)
    source = store.metadata(key)
    store.put(f'toss-compaction:{endpoint}:{CODE}', {'status': 'legacy_preserved',
        'reason': 'owned_fields_not_equivalent',
        'input': {'version': 3, 'normalizer': p.FORMAT, 'revision': source['series_revision'],
                  'rows': source['row_count'], 'status': 'ready'},
        'output_revisions': store.revisions([k+CODE for k in TRADING_PROJECTION_FIELDS[endpoint]])})
    return old, newer


@pytest.mark.parametrize('endpoint', ENDPOINTS)
def test_reobserved_section_keeps_old_evidence_and_new_exact_history(store, endpoint):
    old, _ = arrange(store, endpoint)
    key = f'toss:{endpoint}:{CODE}'
    original = store.metadata(key), store.time_series.rows(key)
    p.compact_projection(store, CODE, endpoint, {'status': 'ready'})
    assert (store.metadata(key), store.time_series.rows(key)) == original
    marker = store.metadata(f'toss-projection:{endpoint}:{CODE}')
    assert marker['section_evidence_verified'] is True
    assert len(marker['retained_observation_keys']) == 1
    retained = store.metadata(marker['retained_observation_keys'][0])
    assert retained['source_key'] == key
    assert sorted(retained['observations'], key=lambda v: v['field']) == [
        {'field': field, 'row': old[field]['rows'][0]} for field in sorted(old)]
    for section in marker['collection_receipt'].values():
        assert section['status'] == 'ready' and section['as_of'] == '2026-10-08'
    assert store.metadata(f'toss-compaction:{endpoint}:{CODE}')['verification']['method'] == (
        'retained-toss-section-evidence-v1')


@pytest.mark.parametrize('endpoint', ENDPOINTS)
@pytest.mark.parametrize('damage', ['value', 'date', 'meaning', 'source', 'endpoint', 'clock'])
def test_reobservation_never_certifies_changed_fact_or_wrong_evidence(store, endpoint, damage):
    old, _ = arrange(store, endpoint)
    field = next(iter(old))
    for prefix in TRADING_PROJECTION_FIELDS[endpoint]:
        body = deepcopy(old)
        row = body[field]['rows'][0]
        if damage == 'value':
            row['balance' if field != 'trades' else 'volume'] = 999
        elif damage == 'date':
            row['date'] = '2000-01-01'
        elif damage == 'meaning':
            row['source_trace']['unit'] = '백만주'
        elif damage == 'source':
            row['source_trace']['source'] = 'naver'
        elif damage == 'endpoint':
            row['source_trace']['source_url'] += '/wrong'
        else:
            row['source_trace']['ingested_at'] = '2026-10-11T00:00:00+00:00'
        store.put(prefix + CODE, body)
    before = {k+CODE: store.metadata(k+CODE) for k in TRADING_PROJECTION_FIELDS[endpoint]}
    p.compact_projection(store, CODE, endpoint, {'status': 'ready'})
    assert all(store.metadata(key) == value for key, value in before.items())
    assert store.metadata(f'toss-compaction:{endpoint}:{CODE}')['status'] == 'legacy_preserved'
    assert not store.list_prefix('toss-retained-sections:')


def test_other_financing_section_and_short_balance_remain_independent(store):
    arrange(store, 'credit-trades')
    for key in (f'financing:{CODE}', f'toss-normalized:financing:{CODE}'):
        payload = store.metadata(key)
        payload['lending'] = {'status': 'ready', 'rows': [{'date': '2000-01-01', 'balance': 7}]}
        store.put(key, payload)
    p.compact_projection(store, CODE, 'credit-trades', {'status': 'ready'})
    assert store.metadata(f'financing:{CODE}')['lending']['rows'][0]['balance'] == 7
    arrange(store, 'short-selling')
    key = f'short-selling:{CODE}'
    store.put(key, {**store.metadata(key), 'balance': {'status': 'error', 'rows': []}})
    p.compact_projection(store, CODE, 'short-selling', {'status': 'ready'})
    assert store.metadata(key)['balance'] == {'status': 'error', 'rows': []}
    assert p.read_short_selling(store, CODE, 'all')['balance']['status'] == 'error'


@pytest.mark.parametrize('failure', ['cas', 'write'])
def test_retained_evidence_and_mirrors_commit_together(store, monkeypatch, failure):
    arrange(store, 'credit-trades')
    before = store.list_prefix('')
    if failure == 'cas':
        monkeypatch.setattr(p, '_projection_unchanged_locked', lambda *a: False)
        p.compact_projection(store, CODE, 'credit-trades', {'status': 'ready'})
    else:
        original = store.put
        def fail(key, *args, **kwargs):
            if key.startswith('toss-projection:'):
                raise RuntimeError('fixture_non_production')
            return original(key, *args, **kwargs)
        monkeypatch.setattr(store, 'put', fail)
        with pytest.raises(RuntimeError):
            p.compact_projection(store, CODE, 'credit-trades', {'status': 'ready'})
    assert store.list_prefix('') == before


@pytest.mark.parametrize('endpoint', ENDPOINTS)
def test_unchanged_receipt_reuse_does_not_read_full_history(store, monkeypatch, endpoint):
    arrange(store, endpoint)
    p.compact_projection(store, CODE, endpoint, {'status': 'ready'})
    monkeypatch.setattr(store.time_series, 'rows', lambda *a, **k: pytest.fail('history read'))
    p.compact_projection(store, CODE, endpoint, {'status': 'ready'})
    assert store.metadata(f'toss-projection:{endpoint}:{CODE}')['skipped'] == 1


@pytest.mark.parametrize('endpoint', ENDPOINTS)
def test_null_latest_cannot_erase_last_good_summary(store, endpoint):
    _, rows = arrange(store, endpoint)
    p.compact_projection(store, CODE, endpoint, {'status': 'ready'})
    keys = [k+CODE for k in TRADING_PROJECTION_FIELDS[endpoint]]
    before = {key: store.metadata(key) for key in keys}
    bad = deepcopy(rows[-1])
    for field in ('marginLoan', 'stockLoan'):
        bad[field] = {'balanceQuantity': None}
    for field in ('balanceQuantity', 'shortSellingVolume', 'shortSellingAmount'):
        bad[field] = None
    put_series(store, f'toss:{endpoint}:{CODE}', {'rows': [bad], 'status': 'ready'}, reverse=True)
    p.compact_projection(store, CODE, endpoint, {'status': 'ready'})
    assert all(store.metadata(key) == value for key, value in before.items())
    observed = (p.read_short_selling(store, CODE, 'all') if endpoint == 'short-selling'
                else p.read_financing(store, CODE, 'all'))
    fields = next(iter(TRADING_PROJECTION_FIELDS[endpoint].values()))
    for field in fields:
        row = observed[field]['rows'][-1]
        assert row['date'] == '2026-10-08'
        assert row['volume' if field == 'trades' else 'balance'] is not None
        assert observed[field]['status'] == 'stale'


@pytest.mark.parametrize('endpoint', ENDPOINTS)
def test_empty_latest_page_keeps_stored_last_good_rows_and_incomplete_history(store, endpoint):
    arrange(store, endpoint)
    p.compact_projection(store, CODE, endpoint, {'status': 'ready'})
    key = f'toss:{endpoint}:{CODE}'
    before = store.time_series.rows(key)
    put_series(store, key, {**store.metadata(key), 'rows': [], 'status': 'stale',
                           'backfill_complete': False}, reverse=True)
    p.compact_projection(store, CODE, endpoint, {'status': 'stale'})
    assert store.time_series.rows(key) == before
    receipts = store.metadata(f'collection:{CODE}')['sources']
    names = ('credit', 'stock_loan') if endpoint == 'credit-trades' else (
        ('lending',) if endpoint == 'securities-lending' else ('short_trades',))
    assert all(receipts[name]['status'] == 'stale' and receipts[name]['as_of'] == '2026-10-08'
               for name in names)
