"""Synthetic files only. Never use these receipts against production backups."""
import hashlib
import json

import pytest

from ops.market import retire_duplicate_backup as retirement


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    names = ('market-20261009T153042Z.dump', 'market-20261009T192007Z.dump')
    for index, name in enumerate(names):
        path = tmp_path / name
        path.write_bytes(b'PGDMP fixture_non_production ' + bytes([index]))
        identity = retirement.file_identity(path)
        (tmp_path / (name + '.manifest.json')).write_text(json.dumps({
            'state': 'completed', 'source': name, 'bytes': identity['bytes'],
            'source_mtime_ns': identity['mtime_ns'], 'archive_list_verified': True,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        }))
    proof = {'source': names[0], 'bytes': (tmp_path / names[0]).stat().st_size,
             'sha256': hashlib.sha256((tmp_path / names[0]).read_bytes()).hexdigest()}
    monkeypatch.setattr(retirement, 'offsite_recovery_status', lambda _: {'state': 'verified'})
    monkeypatch.setattr(retirement, 'open_backup_files', lambda _: set())
    monkeypatch.setattr(retirement, 'sync_directory', lambda _: None)
    return tmp_path, *names, proof


def test_plan_never_deletes(prepared):
    root, source, successor, proof = prepared
    result = retirement.retire_locked(*prepared)
    assert not result['applied']
    assert (root / source).is_file() and (root / successor).is_file()


def test_exact_duplicate_only(prepared):
    root, source, successor, proof = prepared
    unrelated = root / 'predeploy-safe.dump'
    unrelated.write_bytes(b'preserve')
    result = retirement.retire_locked(*prepared, apply=True)
    assert result['applied'] and not (root / source).exists()
    assert (root / successor).is_file() and unrelated.read_bytes() == b'preserve'
    assert (root / (source + '.manifest.json')).is_file()
    assert len(list(root.glob('duplicate-retirement-*.completed.json'))) == 1


@pytest.mark.parametrize('change', ['older_successor', 'different_day', 'bad_hash', 'bad_size'])
def test_mismatches_preserve_source(prepared, change):
    root, source, successor, proof = prepared
    if change == 'older_successor':
        successor = source
    elif change == 'different_day':
        successor = 'market-20261010T192007Z.dump'
    elif change == 'bad_hash':
        proof['sha256'] = 'f' * 64
    else:
        proof['bytes'] += 1
    with pytest.raises(ValueError):
        retirement.retire_locked(root, source, successor, proof, apply=True)
    assert (root / source).is_file()


def test_tampered_newer_archive_preserves_old(prepared):
    root, source, successor, proof = prepared
    (root / successor).write_bytes(b'PGDMP corrupted')
    with pytest.raises(ValueError):
        retirement.retire_locked(*prepared, apply=True)
    assert (root / source).is_file()


def test_active_file_is_preserved(prepared, monkeypatch):
    monkeypatch.setattr(retirement, 'open_backup_files', lambda _: {'active'})
    with pytest.raises(ValueError):
        retirement.retire_locked(*prepared, apply=True)
    assert (prepared[0] / prepared[1]).is_file()


def test_new_reader_race_preserves_old(prepared, monkeypatch):
    observations = iter([set(), {'active'}])
    monkeypatch.setattr(retirement, 'open_backup_files', lambda _: next(observations))
    with pytest.raises(ValueError):
        retirement.retire_locked(*prepared, apply=True)
    assert (prepared[0] / prepared[1]).is_file()


def test_expired_receipt_preserves_old(prepared, monkeypatch):
    monkeypatch.setattr(retirement, 'offsite_recovery_status', lambda _: {'state': 'recheck_due'})
    with pytest.raises(ValueError):
        retirement.retire_locked(*prepared, apply=True)
    assert (prepared[0] / prepared[1]).is_file()


def test_directory_sync_brackets_unlink_and_completion(prepared, monkeypatch):
    root, source, _, _ = prepared
    observed = []
    def observe(directory):
        assert directory == root
        observed.append(((root / source).exists(),
                         len(list(root.glob('duplicate-retirement-*.json'))),
                         len(list(root.glob('*.completed.json')))))
    monkeypatch.setattr(retirement, 'sync_directory', observe)
    retirement.retire_locked(*prepared, apply=True)
    assert observed == [(True, 1, 0), (False, 1, 0), (False, 2, 1)]


def test_intent_directory_sync_failure_preserves_archive(prepared, monkeypatch):
    def fail(_):
        raise OSError('synthetic directory sync failure')
    monkeypatch.setattr(retirement, 'sync_directory', fail)
    with pytest.raises(OSError):
        retirement.retire_locked(*prepared, apply=True)
    root, source, successor, _ = prepared
    assert (root / source).is_file() and (root / successor).is_file()
    assert not list(root.glob('*.completed.json'))
