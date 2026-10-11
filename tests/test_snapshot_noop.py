"""No-op writes preserve content revisions and invalidate only changed values."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from services.market_dashboard.store import Store


class RedisSpy:
    def __init__(self):
        self.events = []

    def delete(self, key):
        self.events.append(("delete", key))

    def publish(self, channel, key):
        self.events.append((channel, key))


def test_fresh_db_read_avoids_redis_and_reencoding(tmp_path, monkeypatch):
    import services.market_dashboard.store as module
    store = Store(url='sqlite://', root=tmp_path)
    value = {'company':'검증용 fixture_non_production', 'rows':[{'value':12.5}]}
    store.put('fixture', value)
    class RedisForbidden:
        def get(self, *args):
            pytest.fail('Redis read was disabled')
        def setex(self, *args):
            pytest.fail('Redis write was disabled')
    store.redis = RedisForbidden()
    def forbidden(*args, **kwargs):
        pytest.fail('Ordinary snapshot was serialized again during read')
    monkeypatch.setattr(module.json, 'dumps', forbidden)
    assert store.get('fixture', use_redis=False) == value
    assert store.cache_bytes > 0


def test_unchanged_semantic_payload_preserves_revision_and_notifications(tmp_path):
    store = Store(url="sqlite://", root=tmp_path)
    store.redis = RedisSpy()
    assert store.put("fixture", {"price": 100, "date": "2025-01-02"}) is True
    revision = store.revisions(["fixture"])
    assert store.put("fixture", {"date": "2025-01-02", "price": 100}) is False
    assert store.revisions(["fixture"]) == revision
    assert len(store.redis.events) == 2
    assert store.put("fixture", {"date": "2025-01-02", "price": 101}) is True
    assert store.revisions(["fixture"]) != revision
    assert len(store.redis.events) == 4
    with pytest.raises(ValueError):
        store.put("fixture", {"price": float("nan")})
    with store.read_connection() as db:
        assert "101" in db.execute(select(store.snapshots.c.payload)).scalar_one()


def test_separate_workers_can_race_to_insert_without_resetting_revisions(tmp_path):
    stores = [Store(root=tmp_path) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        changed = list(pool.map(lambda s: s.put("fixture", {"same": True}), stores))
    assert sum(changed) == 1
    assert len({s.revisions(["fixture"])["fixture"] for s in stores}) == 1
    for store in stores:
        store.engine.dispose()


def test_changed_payload_advances_frozen_or_backwards_clock_but_noop_preserves_it(tmp_path, monkeypatch):
    import services.market_dashboard.store as module
    source = Store('sqlite://', tmp_path)
    stamp = '2026-10-09T13:03:08.207527+00:00'
    monkeypatch.setattr(module, 'now', lambda: stamp)
    source.put('fixture', {'price': 100, 'as_of': '2026-10-02', 'received_at': stamp})
    original = source.revisions(['fixture'])['fixture']
    source.put('fixture', {'price': 101, 'as_of': '2026-10-02', 'received_at': stamp})
    changed = source.revisions(['fixture'])['fixture']
    assert datetime.fromisoformat(changed) == datetime.fromisoformat(original) + timedelta(microseconds=1)
    assert source.get('fixture')['received_at'] == stamp
    assert source.put('fixture', source.get('fixture')) is False
    assert source.revisions(['fixture'])['fixture'] == changed
    monkeypatch.setattr(module, 'now', lambda: '2020-01-01T00:00:00+00:00')
    source.put('fixture', {'price': 102})
    assert datetime.fromisoformat(source.revisions(['fixture'])['fixture']) > datetime.fromisoformat(changed)


def test_independent_stores_atomically_advance_revision_on_same_clock(tmp_path, monkeypatch):
    import services.market_dashboard.store as module
    start = datetime(2026, 10, 9, 13, 0, tzinfo=UTC)
    monkeypatch.setattr(module, 'now', lambda: start.isoformat())
    stores = [Store(root=tmp_path) for _ in range(4)]
    try:
        stores[0].put('fixture', {'value': 0})
        with ThreadPoolExecutor(max_workers=4) as pool:
            changed = list(pool.map(lambda i: stores[i % 4].put('fixture', {'value': i + 1}), range(20)))
        assert all(changed)
        final = datetime.fromisoformat(stores[0].revisions(['fixture'])['fixture'])
        assert final == start + timedelta(microseconds=20)
    finally:
        for source in stores:
            source.engine.dispose()


def test_derived_official_views_reference_actual_monotonic_source_revision(tmp_path, monkeypatch):
    import services.market_dashboard.store as module
    from services.market_dashboard.official_views import read_view
    source = Store('sqlite://', tmp_path)
    monkeypatch.setattr(module, 'now', lambda: '2026-10-09T00:00:00+00:00')
    for value in (100, 200, 300):
        source.put('official:005930', {'statements': [{'value': value}]})
        revision = source.revisions(['official:005930'])['official:005930']
        assert source.get('official-view:005930:fundamentals')['source_revision'] == revision
        assert read_view(source, '005930', 'fundamentals')['statements'][0]['value'] == value


def test_future_revision_never_moves_back_and_exhaustion_preserves_old_value(tmp_path, monkeypatch):
    import services.market_dashboard.store as module
    from sqlalchemy.exc import DataError, OperationalError
    source = Store('sqlite://', tmp_path)
    monkeypatch.setattr(module, 'now', lambda: '9999-12-31T23:59:59.999999+00:00')
    source.put('fixture', {'value': 1})
    monkeypatch.setattr(module, 'now', lambda: '2026-01-01T00:00:00+00:00')
    with pytest.raises((DataError, OperationalError)):
        source.put('fixture', {'value': 2})
    assert source.get('fixture') == {'value': 1}
