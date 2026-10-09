"""Fixture-only read-model consistency and incremental loading."""
import pytest
from sqlalchemy import event
from services.market_dashboard.store import Store
from services.market_dashboard.enriched_view import publish, read, rebuild, PREFIX


@pytest.fixture
def store(tmp_path):
    result = Store(url='sqlite://', root=tmp_path)
    result.put('enriched', {'005930': {'price':100,'facts':{}},
                            '000660': {'price':200,'facts':{}}})
    yield result
    result.engine.dispose()


def test_incremental_update_preserves_other_security_and_matches_original(store, monkeypatch):
    assert rebuild(store) == 2
    assert read(store) == store.get('enriched', use_redis=False)
    original_other = store.revisions([PREFIX+'000660'])
    publish(store, {'code':'005930', 'facts':{'price':{'value':150,'source':'fixture_non_production'}}})
    assert store.revisions([PREFIX+'000660']) == original_other
    expected = store.get('enriched', use_redis=False)
    def forbidden(*args, **kwargs):
        pytest.fail('Incremental read loaded the full compatibility snapshot')
    monkeypatch.setattr(store, 'get', forbidden)
    payload_requests = []
    def capture(conn, cursor, statement, parameters, context, many):
        if (statement.startswith('SELECT') and 'market_snapshots.payload' in statement.split('FROM')[0]
                and parameters and str(parameters[0]).startswith(PREFIX)):
            payload_requests.append(parameters)
    event.listen(store.engine, 'before_cursor_execute', capture)
    try:
        assert read(store) == expected
    finally:
        event.remove(store.engine, 'before_cursor_execute', capture)
    assert payload_requests == [(PREFIX+'005930',)]


def test_direct_writer_invalidates_projection_and_rebuild_handles_removal(store):
    rebuild(store)
    read(store)
    store.put('enriched', {'005930':{'price':400}})
    assert read(store) == {'005930':{'price':400}}
    assert rebuild(store) == 1
    assert read(store) == {'005930':{'price':400}}
    assert store.get(PREFIX+'000660') is None


def test_projection_failure_rolls_back_source(store, monkeypatch):
    rebuild(store)
    before = store.get('enriched', use_redis=False)
    original = store.put
    def fail(key, value, **kwargs):
        if key.startswith(PREFIX):
            raise RuntimeError('fixture projection failure')
        return original(key, value, **kwargs)
    monkeypatch.setattr(store, 'put', fail)
    with pytest.raises(RuntimeError):
        publish(store, {'code':'005930','facts':{'price':{'value':500}}})
    assert store.get('enriched', use_redis=False) == before
    assert read(store) == before


def test_legacy_publish_without_projection_remains_compatible(store):
    publish(store, {'code':'005930','facts':{'price':{'value':600}}})
    assert read(store)['005930']['price'] == 600
