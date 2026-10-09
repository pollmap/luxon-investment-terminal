import sqlite3

import pytest
from sqlalchemy.exc import OperationalError

from services.market_dashboard.official_views import read_view
from services.market_dashboard import official_views
from services.market_dashboard.store import Store


@pytest.mark.parametrize('error', [
    OSError('sensitive path must not be logged'),
    sqlite3.OperationalError('database is read only'),
    OperationalError('SQL with sensitive payload', {}, Exception('disk full')),
])
def test_cache_write_failure_keeps_valid_readable_source(tmp_path, monkeypatch, caplog, error):
    store = Store('sqlite://', tmp_path)
    source = {'company': {'name': 'fixture'}, 'statements': [{'revenue': 123}],
              'source_trace': {'source': 'fixture_non_production'}}
    store.put('official:005930', source)
    cached = store.get('official-view:005930:fundamentals')
    monkeypatch.setattr(official_views, 'PROJECTION_VERSION', official_views.PROJECTION_VERSION + 1)
    def fail_cache(key, payload):
        assert key == 'official-view:005930:fundamentals'
        raise error
    monkeypatch.setattr(store, 'put', fail_cache)
    result = read_view(store, '005930', 'fundamentals')
    assert result == source
    assert store.get('official:005930') == source
    assert store.get('official-view:005930:fundamentals') == cached
    assert type(error).__name__ in caplog.text
    assert str(error) not in caplog.text


def test_programming_errors_are_not_silenced_as_cache_failures(tmp_path, monkeypatch):
    store = Store('sqlite://', tmp_path)
    store.put('official:005930', {'company': {'name': 'fixture'}})
    monkeypatch.setattr(official_views, 'PROJECTION_VERSION', official_views.PROJECTION_VERSION + 1)
    def fail_cache(key, payload):
        raise ValueError('bad cache contract')
    monkeypatch.setattr(store, 'put', fail_cache)
    with pytest.raises(ValueError, match='bad cache contract'):
        read_view(store, '005930', 'fundamentals')
