import json

from sqlalchemy import event, select

from services.market_dashboard.store import Store
from services.market_dashboard.time_series import put_series
from services.market_dashboard.toss import TossClient


def test_new_history_payload_is_compact_json_readable_by_previous_releases(tmp_path):
    store = Store('sqlite://', tmp_path)
    key = 'bars:005930:통합:raw'
    row = {'date': '2026-01-01', 'close': 100, 'source_trace': {
        'source': 'fixture_non_production', 'label': 'space inside value stays',
        'quality_flags': ['one', 'two'], 'available_at': None,
    }}
    put_series(store, key, {'rows': [row]})
    with store.read_connection() as db:
        payload = db.execute(select(store.time_series.table.c.payload)).scalar_one()
    assert json.loads(payload) == row
    assert len(payload.encode()) < len(json.dumps(row, ensure_ascii=False, sort_keys=True).encode())
    assert store.time_series.rows(key) == [row]
    revision = store.revisions([key])
    put_series(store, key, {'rows': [row]})
    assert store.revisions([key]) == revision  # Serialization is not a data revision.


def test_append_and_correction_keep_exact_incremental_count_and_bounds(tmp_path):
    store = Store('sqlite://', tmp_path)
    key = 'bars:005930:통합:raw'
    initial = [{'date': f'2020-01-{day:02d}', 'close': day} for day in range(1, 21)]
    put_series(store, key, {'rows': initial, 'status': 'ready'})
    metadata = store.metadata(key)
    assert (metadata['row_count'], metadata['first_time'], metadata['last_time']) == (
        20, '2020-01-01', '2020-01-20')

    statements = []
    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lower())
    event.listen(store.engine, 'before_cursor_execute', capture)
    try:
        put_series(store, key, {'rows': [
            {'date': '2019-12-31', 'close': 9},
            {'date': '2020-01-20', 'close': 21},
            {'date': '2020-01-21', 'close': 22},
        ], 'status': 'ready'})
    finally:
        event.remove(store.engine, 'before_cursor_execute', capture)

    metadata = store.metadata(key)
    assert (metadata['row_count'], metadata['first_time'], metadata['last_time']) == (
        22, '2019-12-31', '2020-01-21')
    assert not any('count(' in statement for statement in statements)
    assert store.time_series.rows(key, start='2020-01-20', end='2020-01-20')[0]['close'] == 21


def test_legacy_metadata_uses_one_recount_then_returns_to_incremental_updates(tmp_path):
    store = Store('sqlite://', tmp_path)
    key = 'bars:005930:통합:raw'
    store.time_series.upsert(key, [
        {'date': '2020-01-01', 'close': 10},
        {'date': '2020-01-02', 'close': 11},
    ])
    store.put(key, {'rows': [{'date': '2020-01-01', 'close': 10}], 'status': 'legacy'})
    put_series(store, key, {'rows': [{'date': '2020-01-03', 'close': 12}], 'status': 'ready'})
    metadata = store.metadata(key)
    assert (metadata['row_count'], metadata['first_time'], metadata['last_time']) == (
        3, '2020-01-01', '2020-01-03')

    statements = []
    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lower())
    event.listen(store.engine, 'before_cursor_execute', capture)
    try:
        put_series(store, key, {'rows': [{'date': '2020-01-04', 'close': 13}], 'status': 'ready'})
    finally:
        event.remove(store.engine, 'before_cursor_execute', capture)
    assert store.metadata(key)['row_count'] == 4
    assert not any('count(' in statement for statement in statements)


def test_series_write_metrics_are_persisted_without_credentials_or_request_data(tmp_path):
    store = Store('sqlite://', tmp_path)
    client = TossClient(store, client=object())
    stats = {}
    put_series(store, 'bars:005930:통합:raw', {
        'rows': [{'date': '2026-01-01', 'close': 100}], 'status': 'ready'
    }, stats=stats)
    client.record_time_series_write('/api/v1/candles', stats)
    metrics = store.get('toss-api:/api/v1/candles')['metrics']
    assert metrics['time_series_inserted_rows'] == 1
    assert metrics['time_series_updated_rows'] == 0
    assert metrics['time_series_write_ms_total'] > 0
    assert not {'client_id', 'client_secret', 'params'} & metrics.keys()
