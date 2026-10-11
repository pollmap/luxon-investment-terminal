"""fixture_non_production: opt-in capacity gates without provider or database activity."""

from types import SimpleNamespace

import pytest

from services.market_dashboard import storage_budget as budget

GIB = 1024**3
MIB = 1024**2


@pytest.fixture(autouse=True)
def default_profile(monkeypatch):
    monkeypatch.delenv(budget.PROFILE_ENV, raising=False)


def usage(percent, total=100 * GIB):
    return SimpleNamespace(total=total, free=int(total * (100 - percent) / 100))


class MetadataStore:
    def __init__(self, root):
        self.root = root
        self.rows = {}
        self.writes = []

    def get(self, key, fallback):
        return self.rows.get(key, fallback)

    def put(self, key, value):
        self.rows[key] = value
        self.writes.append(key)


def test_unset_and_explicit_default_preserve_original_limits(tmp_path, monkeypatch):
    for setting in (None, 'default'):
        if setting is not None:
            monkeypatch.setenv(budget.PROFILE_ENV, setting)
        assert budget.backfill_capacity(tmp_path, usage(90), 'ready')['limited']
        assert budget.backfill_capacity(tmp_path, usage(80), 'capacity_limited')['limited']
        resumed = budget.backfill_capacity(tmp_path, usage(79), 'capacity_limited')
        assert not resumed['limited'] and resumed['write_verified'] is True
        assert (resumed['profile'], resumed['stop_used_percent'],
                resumed['resume_below_used_percent']) == ('default', 90, 80)
        assert resumed['minimum_free_bytes'] == resumed['resume_minimum_free_bytes'] == 768 * MIB
        assert resumed['configuration_error'] is None
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(('free', 'limited'), [(768 * MIB - 1, True), (768 * MIB, False)])
def test_default_minimum_reserve_boundary(tmp_path, free, limited):
    state = budget.backfill_capacity(tmp_path, SimpleNamespace(total=2 * GIB, free=free), None)
    assert state['limited'] is limited


@pytest.mark.parametrize('previous', [None, 'ready', 'capacity_limited'])
def test_high_profile_checks_stop_limit_even_when_not_previously_paused(tmp_path, monkeypatch,
                                                                    previous):
    monkeypatch.setenv(budget.PROFILE_ENV, 'high-utilization')
    state = budget.backfill_capacity(tmp_path, usage(97, 200 * GIB), previous)
    assert state['limited'] and state['write_verified'] is None
    assert state['free_bytes'] > 4 * GIB  # Ratio independently enforces the stop.
    assert state['minimum_free_bytes'] == 3 * GIB
    assert state['resume_minimum_free_bytes'] == 4 * GIB
    assert state['stop_used_percent'] == 97 and state['resume_below_used_percent'] == 95


def test_high_profile_hysteresis_survives_queue_restart(tmp_path, monkeypatch):
    monkeypatch.setenv(budget.PROFILE_ENV, 'high-utilization')
    store = MetadataStore(tmp_path)
    for percent, limited in ((97, True), (96, True), (95, True), (94, False)):
        monkeypatch.setattr(budget.shutil, 'disk_usage', lambda _, p=percent: usage(p, 200 * GIB))
        state = budget.collection_capacity(store, 'xbrl')
        assert state['limited'] is limited
        assert state['profile'] == 'high-utilization'
    assert state['write_verified'] is True
    assert store.rows['storage-capacity:xbrl']['status'] == 'ready'
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(('previous', 'limited'), [
    (None, False), ('ready', False), ('capacity_limited', True),
])
def test_high_profile_keeps_ready_queue_running_inside_hysteresis_band(tmp_path, monkeypatch,
                                                                     previous, limited):
    monkeypatch.setenv(budget.PROFILE_ENV, 'high-utilization')
    state = budget.backfill_capacity(tmp_path, usage(96, 200 * GIB), previous)
    assert state['limited'] is limited
    assert state['warning'] is True and state['write_verified'] is None


@pytest.mark.parametrize(('previous', 'free', 'limited'), [
    ('ready', 3 * GIB - 1, True), ('ready', 3 * GIB, False),
    ('capacity_limited', 4 * GIB - 1, True), ('capacity_limited', 4 * GIB, False),
])
def test_high_absolute_reserve_and_resume_reserve_are_independent(tmp_path, monkeypatch,
                                                                previous, free, limited):
    monkeypatch.setenv(budget.PROFILE_ENV, 'high-utilization')
    state = budget.backfill_capacity(tmp_path, SimpleNamespace(total=10 * GIB, free=free), previous)
    assert state['limited'] is limited
    assert state['write_verified'] is (True if previous == 'capacity_limited' and not limited
                                      else None)


@pytest.mark.parametrize('failure', ['create', 'fsync', 'cleanup'])
def test_resume_requires_probe_fsync_and_cleanup(tmp_path, monkeypatch, failure):
    monkeypatch.setenv(budget.PROFILE_ENV, 'high-utilization')

    def fail(*args, **kwargs):
        raise OSError('fixture_non_production')

    if failure == 'create':
        monkeypatch.setattr(budget.tempfile, 'NamedTemporaryFile', fail)
    elif failure == 'fsync':
        monkeypatch.setattr(budget.os, 'fsync', fail)
    else:
        monkeypatch.setattr(budget.Path, 'unlink', fail)
    state = budget.backfill_capacity(tmp_path, usage(89, 144 * GIB), 'capacity_limited')
    assert state['limited'] is True and state['write_verified'] is False


@pytest.mark.parametrize('configured', ['', 'HIGH-UTILIZATION', 'invalid-fixture-value'])
def test_invalid_configuration_is_fail_closed_and_value_is_not_echoed(tmp_path, monkeypatch,
                                                                    configured):
    monkeypatch.setenv(budget.PROFILE_ENV, configured)
    monkeypatch.setattr(budget.shutil, 'disk_usage', lambda _: usage(10))
    store = MetadataStore(tmp_path)
    state = budget.collection_capacity(store, 'financial-reports')
    assert state['status'] == 'capacity_limited' and state['limited'] is True
    assert state['profile'] == 'invalid'
    assert state['configuration_error'] == 'invalid_storage_profile'
    assert state['write_verified'] is None
    if configured:
        assert configured not in str(state)
    assert not list(tmp_path.iterdir())
    # A corrected configuration must still pass the durable recovery probe.
    monkeypatch.setenv(budget.PROFILE_ENV, 'high-utilization')
    resumed = budget.collection_capacity(store, 'financial-reports')
    assert resumed['status'] == 'ready' and resumed['write_verified'] is True
    assert resumed['configuration_error'] is None


def test_policy_change_persists_metadata_without_losing_per_queue_pause(tmp_path, monkeypatch):
    store = MetadataStore(tmp_path)
    monkeypatch.setattr(budget.shutil, 'disk_usage', lambda _: usage(50))
    budget.collection_capacity(store, 'xbrl')
    assert len(store.writes) == 1
    budget.collection_capacity(store, 'xbrl')
    assert len(store.writes) == 1
    monkeypatch.setenv(budget.PROFILE_ENV, 'high-utilization')
    state = budget.collection_capacity(store, 'xbrl')
    assert len(store.writes) == 2 and state['profile'] == 'high-utilization'
    assert state['write_verified'] is None  # A ready queue is not a new probe receipt.
    monkeypatch.setenv(budget.PROFILE_ENV, 'default')
    monkeypatch.setattr(budget.shutil, 'disk_usage', lambda _: usage(91))
    assert budget.collection_capacity(store, 'xbrl')['limited']
    monkeypatch.setattr(budget.shutil, 'disk_usage', lambda _: usage(89, 144 * GIB))
    assert budget.collection_capacity(store, 'xbrl')['limited']
    monkeypatch.setenv(budget.PROFILE_ENV, 'high-utilization')
    resumed = budget.collection_capacity(store, 'xbrl')
    assert not resumed['limited'] and resumed['write_verified'] is True
    assert resumed['profile'] == 'high-utilization'
