import asyncio
from copy import deepcopy

import pytest

from services.market_dashboard.store import Store


from services.market_dashboard import worker, collection


def test_process_liveness_does_not_freshen_market_data():
    value = {'status': 'collecting', 'updated_at': '2026-10-06T16:50:21Z',
             'process_heartbeat_at': '2026-10-08T16:20:00Z', 'count': 2766, 'phase': 'universe'}
    original = deepcopy(value)
    result = collection.worker_snapshot(value, checked_at='2026-10-08T16:20:30Z')
    assert result['status'] == 'collecting'
    assert result['heartbeat_status'] == 'current'
    assert result['heartbeat_source'] == 'process_heartbeat_at'
    assert result['heartbeat_age_seconds'] == 30
    assert result['heartbeat_stale_after_seconds'] == 120
    assert result['updated_at'] == original['updated_at']
    assert value == original


@pytest.mark.parametrize('stamp', ['2026-10-08T16:00:00Z', 'invalid', None])
def test_stalled_or_invalid_supervisor_cannot_use_fresh_market_timestamp(stamp):
    value = {'status': 'collecting', 'updated_at': '2026-10-08T16:20:20Z',
             'process_heartbeat_at': stamp}
    result = collection.worker_snapshot(value, checked_at='2026-10-08T16:20:30Z')
    assert result['status'] == 'degraded'
    assert result['heartbeat_source'] == 'process_heartbeat_at'


@pytest.mark.parametrize('status', ['error', 'capacity_limited', 'stopped'])
def test_fresh_process_does_not_clear_market_failure(status):
    value = {'status': status, 'updated_at': '2026-10-01T00:00:00Z',
             'process_heartbeat_at': '2026-10-08T16:20:00Z', 'error': 'fixture_error'}
    result = collection.worker_snapshot(value, checked_at='2026-10-08T16:20:30Z')
    assert result['status'] == status and result['error'] == 'fixture_error'
    assert result['heartbeat_status'] == 'current'


def test_legacy_worker_uses_its_original_timestamp():
    result = collection.worker_snapshot({'status': 'ready', 'updated_at': '2026-10-08T16:19:00Z'},
                                        checked_at='2026-10-08T16:20:30Z')
    assert result['status'] == 'ready'
    assert result['heartbeat_source'] == 'updated_at'


def test_market_result_update_keeps_process_heartbeat(tmp_path):
    store = Store('sqlite://', tmp_path)
    original = {'process_heartbeat_at': '2026-10-08T16:20:00Z', 'phase': 'universe', 'count': 2766}
    store.put('worker', original)
    worker.update_worker_state(store, status='error', updated_at='2026-10-08T16:20:10Z', error='fixture')
    result = store.get('worker')
    assert all(result[k] == v for k, v in original.items())
    assert result['status'] == 'error'


def test_supervisor_refreshes_only_process_timestamp_and_can_be_cancelled(tmp_path, monkeypatch):
    store = Store('sqlite://', tmp_path)
    original = {'status': 'error', 'error': 'old-source-error', 'updated_at': '2026-10-06T16:50:21Z',
                'count': 2766, 'phase': 'universe'}
    store.put('worker', original)
    moments = iter(['2026-10-08T16:20:00Z', '2026-10-08T16:20:30Z'])
    monkeypatch.setattr(worker, 'now', lambda: next(moments))
    snapshots = []
    async def sleep(interval):
        assert interval == 30
        snapshots.append(deepcopy(store.get('worker')))
        if len(snapshots) == 2:
            raise asyncio.CancelledError
    monkeypatch.setattr(worker.asyncio, 'sleep', sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker.worker_heartbeat_loop(store))
    assert len(snapshots) == 2
    for snapshot in snapshots:
        assert all(snapshot[k] == v for k, v in original.items())
    assert snapshots[0]['process_heartbeat_at'] != snapshots[1]['process_heartbeat_at']
