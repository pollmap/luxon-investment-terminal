"""fixture_non_production: bounded DART maintenance, no SSH/Docker/provider calls."""

import gzip
import hashlib
import importlib.util
import io
import json
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.market_dashboard.raw_archive import COMPRESSED_SOURCES, compact_one, read_original
from services.market_dashboard.store import Store

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def compactor(monkeypatch):
    monkeypatch.setitem(sys.modules, 'fcntl', SimpleNamespace(
        LOCK_EX=1, LOCK_NB=2, flock=lambda *_: None))
    spec = importlib.util.spec_from_file_location(
        'dart_compactor_tests', ROOT / 'ops/market/compact_raw_archives.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def original(root, content=b'fixture_non_production' * 100, source='dart'):
    directory = root / source
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(content).hexdigest()
    envelope = directory / (digest + '.json')
    (directory / (digest + '.bin')).write_bytes(content)
    envelope.write_text(json.dumps({
        'sha256': digest, 'payload': content.decode(), 'bytes': len(content),
        'binary_file': digest + '.bin', 'received_at': '2020-01-01T00:00:00Z',
        'url': 'https://example.invalid?fixture_secret=do-not-print',
        'correction_receipt': 'original-correction-id',
    }), encoding='utf-8')
    return envelope


def hashes(directory):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.iterdir() if p.is_file()}


def test_default_sources_and_explicit_canary_selection(compactor, tmp_path):
    assert 'dart' not in COMPRESSED_SOURCES
    assert compactor.parser().parse_args([]).source is None
    for sources, selected in ((('dart',), ()), (('dart', 'toss'), ('0' * 64,)),
                              (('dart-xbrl',), ('0' * 64,))):
        with pytest.raises(ValueError):
            compactor.compact_batch(tmp_path, sources, hashes=selected, apply=True)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('parameter,value', [
    ('max_files', 26), ('max_files', 0), ('max_seconds', 121),
    ('max_total_bytes', 64 * 1024**2 + 1), ('max_original_bytes', 16 * 1024**2 + 1),
])
def test_budget_cannot_be_removed(compactor, tmp_path, parameter, value):
    envelope = original(tmp_path)
    before = hashes(envelope.parent)
    with pytest.raises(ValueError, match='budget'):
        compactor.compact_batch(tmp_path, ('dart',), hashes=[envelope.stem],
                                apply=True, **{parameter: value})
    assert hashes(envelope.parent) == before


def test_exact_hash_selection_and_dry_run_are_non_mutating(compactor, tmp_path):
    chosen = original(tmp_path)
    other = original(tmp_path, b'fixture_other')
    before = hashes(chosen.parent)
    result = compactor.compact_batch(tmp_path, ('dart',), hashes=[chosen.stem])
    assert result['files'] == 1 and result['errors'] == 0
    assert result['records'][0]['sha256'] == chosen.stem
    assert 'fixture_secret' not in json.dumps(result)
    assert hashes(chosen.parent) == before
    assert other.is_file()


def test_canary_preserves_original_metadata_and_is_idempotent(compactor, tmp_path):
    envelope = original(tmp_path)
    old = envelope.read_bytes()
    result = compactor.compact_batch(tmp_path, ('dart',), apply=True, hashes=[envelope.stem])
    assert result['files'] == 1 and result['errors'] == 0
    assert result['records'][0]['original_hash_verified']
    assert result['records'][0]['legacy_envelope_preserved']
    legacy = envelope.with_name(envelope.stem + '.json.legacy.gz')
    assert gzip.decompress(legacy.read_bytes()) == old
    first = hashes(envelope.parent)
    repeat = compactor.compact_batch(tmp_path, ('dart',), apply=True, hashes=[envelope.stem])
    assert repeat['reclaimed_bytes'] == 0 and repeat['errors'] == 0
    assert hashes(envelope.parent) == first


def test_file_and_byte_limits_stop_before_unselected_mutation(compactor, tmp_path):
    first = original(tmp_path, b'fixture_first' * 10)
    second = original(tmp_path, b'fixture_second' * 10)
    old = second.read_bytes()
    selected = [first.stem, second.stem]
    report = compactor.compact_batch(tmp_path, ('dart',), hashes=selected,
                                    apply=True, max_files=1)
    assert report['stop_reason'] == 'file_limit' and report['files'] == 1
    assert second.read_bytes() == old
    report = compactor.compact_batch(tmp_path, ('dart',), hashes=selected,
                                    apply=True, max_total_bytes=len(b'fixture_first' * 10))
    assert report['stop_reason'] == 'byte_limit' and report['files'] == 1
    assert second.read_bytes() == old


def test_corruption_and_expansion_cap_stop_without_deleting_original(compactor, tmp_path):
    envelope = original(tmp_path)
    body = envelope.with_name(envelope.stem + '.bin')
    body.write_bytes(b'wrong original')
    before = hashes(envelope.parent)
    report = compactor.compact_batch(tmp_path, ('dart',), hashes=[envelope.stem], apply=True)
    assert report['stop_reason'] == 'verification_failed' and report['errors'] == 1
    assert hashes(envelope.parent) == before
    assert 'fixture_secret' not in json.dumps(report)
    body.write_bytes(b'fixture_non_production' * 100)
    gz = envelope.with_name(envelope.stem + '.bin.gz')
    gz.write_bytes(gzip.compress(b'x' * 10000))
    before = hashes(envelope.parent)
    report = compactor.compact_batch(tmp_path, ('dart',), hashes=[envelope.stem],
                                    apply=True, max_original_bytes=4096)
    assert report['errors'] == 1
    assert hashes(envelope.parent) == before


def test_interrupted_atomic_file_is_not_reported_compacted(compactor, tmp_path, monkeypatch):
    envelope = original(tmp_path)
    before = hashes(envelope.parent)
    def interrupt(*_, **__):
        raise TimeoutError()
    monkeypatch.setattr(compactor, 'compact_one', interrupt)
    report = compactor.compact_batch(tmp_path, ('dart',), hashes=[envelope.stem], apply=True)
    assert report['stop_reason'] == 'time_limit'
    assert report['records'][0]['status'] == 'verified'
    assert hashes(envelope.parent) == before


def test_default_bounded_batch_advances_past_previously_compacted_files(compactor, tmp_path):
    first = original(tmp_path, b'fixture_first', 'naver')
    second = original(tmp_path, b'fixture_second', 'naver')
    compact_one(first)
    result = compactor.compact_batch(tmp_path, ('naver',), apply=True, max_files=1)
    assert result['files'] == 1 and result['errors'] == 0
    assert result['records'][0]['sha256'] == second.stem
    assert not second.with_suffix('.bin').exists()


def test_unix_deadline_interrupts_and_restores_signal_contract(compactor, monkeypatch):
    calls = []
    callbacks = []
    monkeypatch.setattr(compactor.signal, 'SIGALRM', 14, raising=False)
    monkeypatch.setattr(compactor.signal, 'ITIMER_REAL', 0, raising=False)
    def signal(number, callback):
        callbacks.append(callback)
        return 'previous_handler'
    def timer(*args):
        calls.append(args)
        return (0.0, 0.0)
    monkeypatch.setattr(compactor.signal, 'signal', signal)
    monkeypatch.setattr(compactor.signal, 'setitimer', timer, raising=False)
    with pytest.raises(TimeoutError):
        with compactor.deadline(12):
            callbacks[0]()
    assert calls == [(0, 12), (0, 0.0, 0.0)]
    assert callbacks[-1] == 'previous_handler'


@pytest.fixture
def releases(compactor, tmp_path, monkeypatch):
    root = tmp_path / 'service'
    current, rollback = 'candidate-v110', 'readers-v109'
    required = ['source-trace-sha256-v1', 'financial-source-trace-sha256-v1']
    for version in (current, rollback):
        release = root / 'releases' / version
        for name in compactor.READER_FILES:
            path = release / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('# fixture_non_production ' + name).encode())
        (release / compactor.READER_FILES[0]).write_text(json.dumps({
            'version': 1, 'read_formats': [*required, compactor.DART_FORMAT]}))
    pointer = root / 'current'
    pointer.write_text(str(root / 'releases' / current))
    resolved = Path.resolve
    def resolve(path, *args, **kwargs):
        if path == pointer:
            return resolved(Path(path.read_text()), *args, **kwargs)
        return resolved(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'resolve', resolve)
    monkeypatch.setattr(compactor, '__file__', str(
        root / 'releases' / current / 'ops/market/compact_raw_archives.py'))
    monkeypatch.setattr(compactor, 'SERVICE_ROOT', root)
    marker = root / 'shared/data/storage-format.json'
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({'version': 1, 'required_read_formats': required,
                                 'rollback_floor': 'previous-v108', 'unrelated_metadata': 'kept'}))
    (root / '.deploy.lock').touch()
    calls = []
    def run(command, **_):
        calls.append(command)
        if command[:2] == ['docker', 'inspect']:
            service = command[-1].removeprefix('korea-stocks-').removesuffix('-1')
            return SimpleNamespace(returncode=0, stdout=(
                f'running|korea-stocks-api:{current}|korea-stocks|{service}|market'))
        version = rollback if command[:2] == ['docker', 'run'] else current
        receipts = compactor._release_receipt(root / 'releases' / version,
                                              [*required, compactor.DART_FORMAT])
        return SimpleNamespace(returncode=0, stdout=json.dumps(receipts))
    monkeypatch.setattr(compactor.subprocess, 'run', run)
    return SimpleNamespace(root=root, current=current, rollback=rollback, marker=marker,
                           required=required, calls=calls)


def test_both_images_and_all_existing_storage_formats_are_required(compactor, releases):
    r = releases
    _, original_marker, result = compactor.validate_dart_readers(r.current, r.rollback)
    assert result['required_read_formats'] == [*r.required, compactor.DART_FORMAT]
    assert result['rollback_floor'] == r.rollback
    assert result['unrelated_metadata'] == 'kept'
    assert r.marker.read_bytes() == original_marker  # verification never activates
    assert len(r.calls) == 5
    isolated = next(call for call in r.calls if call[:2] == ['docker', 'run'])
    assert all(value in isolated for value in ('--rm', '--network', 'none', '--read-only'))


@pytest.mark.parametrize('reason', [
    'same_release', 'missing_script', 'old_capability', 'financial_missing'])
def test_older_incompatible_rollback_is_blocked(compactor, releases, reason):
    r = releases
    fallback = r.root / 'releases' / r.rollback
    rollback = r.rollback
    if reason == 'same_release':
        rollback = r.current
    elif reason == 'missing_script':
        (fallback / 'scripts/replay_dart_period_archives.py').unlink()
    else:
        formats = r.required if reason == 'old_capability' else [compactor.DART_FORMAT]
        (fallback / compactor.READER_FILES[0]).write_text(json.dumps({
            'version': 1, 'read_formats': formats}))
    original_marker = r.marker.read_bytes()
    with pytest.raises((ValueError, RuntimeError)):
        compactor.validate_dart_readers(r.current, rollback)
    assert r.marker.read_bytes() == original_marker
    assert r.calls == []


def test_actual_image_must_match_release_files(compactor, releases, monkeypatch):
    r = releases
    run = compactor.subprocess.run
    def mismatch(command, **kwargs):
        result = run(command, **kwargs)
        if command[:2] == ['docker', 'run']:
            values = json.loads(result.stdout)
            values['scripts/replay_dart_period_archives.py'] = '0' * 64
            result.stdout = json.dumps(values)
        return result
    monkeypatch.setattr(compactor.subprocess, 'run', mismatch)
    with pytest.raises(RuntimeError, match='hashes'):
        compactor.validate_dart_readers(r.current, r.rollback)
    assert compactor.DART_FORMAT not in r.marker.read_text()


def test_root_container_cannot_create_unreadable_archive_files(compactor, releases, monkeypatch):
    r = releases
    run = compactor.subprocess.run
    def as_root(command, **kwargs):
        result = run(command, **kwargs)
        if command[:2] == ['docker', 'inspect']:
            result.stdout = result.stdout.removesuffix('|market') + '|root'
        return result
    monkeypatch.setattr(compactor.subprocess, 'run', as_root)
    with pytest.raises(RuntimeError, match='live DART reader'):
        compactor.validate_dart_readers(r.current, r.rollback)
    assert compactor.DART_FORMAT not in r.marker.read_text()


def test_failed_canary_keeps_durable_rollback_guard(compactor, releases, tmp_path, monkeypatch):
    r = releases
    raw = tmp_path / 'raw'
    envelope = original(raw)
    monkeypatch.setattr(compactor, 'HOST_RAW', raw)
    def fail(args):
        state = json.loads(r.marker.read_bytes())
        assert state['rollback_floor'] == r.rollback
        assert compactor.DART_FORMAT in state['required_read_formats']
        return {'errors': 1, 'stop_reason': 'verification_failed'}
    monkeypatch.setattr(compactor, 'run_container_batch', fail)
    args = compactor.parser().parse_args([
        '--source', 'dart', '--expected', r.current, '--rollback', r.rollback,
        '--sha256', envelope.stem, '--apply'])
    before = hashes(envelope.parent)
    result = compactor.run_dart(args)
    assert result['errors'] == 1 and result['durable_reader_requirement']
    assert json.loads(r.marker.read_bytes())['required_read_formats'][-1] == compactor.DART_FORMAT
    assert hashes(envelope.parent) == before


def test_canary_uses_existing_container_user_and_no_provider_or_mounts(compactor, monkeypatch):
    args = compactor.parser().parse_args([
        '--source', 'dart', '--expected', 'candidate-v110', '--rollback', 'readers-v109',
        '--sha256', 'a' * 64, '--apply'])
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        compile(command[-1], '<container canary>', 'exec')
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            'sources': ['dart'], 'apply': True, 'errors': 0}))
    monkeypatch.setattr(compactor.subprocess, 'run', run)
    compactor.run_container_batch(args)
    command, kwargs = calls[0]
    assert command[:4] == ['docker', 'exec', '-i', 'korea-stocks-market-api-1']
    assert '--user' not in command and '--mount' not in command and '--volume' not in command
    assert json.loads(kwargs['input'])['sha256'] == ['a' * 64]
    assert kwargs['timeout'] == args.max_seconds + 15


def test_only_named_replay_script_can_be_extracted(compactor, tmp_path):
    spec = importlib.util.spec_from_file_location('promoter_dart_tests',
                                                 ROOT / 'ops/market/promote_backend_patch.py')
    promoter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(promoter)
    required = ['services/market_dashboard/source_support.py',
                'services/market_dashboard/toss_backfill.py',
                *promoter.RUNTIME_SCRIPTS, *promoter.RUNTIME_OPERATIONS]
    archive = tmp_path / 'patch.tar.gz'
    with tarfile.open(archive, 'w:gz') as stream:
        for name in [*required, 'scripts/unrelated_or_private.py']:
            info = tarfile.TarInfo(name)
            info.size = 7
            stream.addfile(info, io.BytesIO(b'fixture'))
    with pytest.raises(ValueError, match='configuration'):
        promoter.safe_extract(archive, tmp_path / 'candidate')
    assert not (tmp_path / 'candidate').exists()


def test_compacted_dart_refetch_never_recreates_binary_or_retimes_metadata(tmp_path, monkeypatch):
    monkeypatch.delenv('MARKET_COMPRESS_DART_RAW', raising=False)
    store = Store('sqlite://', tmp_path)
    content = b'fixture_non_production' * 100
    digest = store.raw('dart', 'https://example.invalid/original', content)
    directory = tmp_path / 'raw/dart'
    compact_one(directory / (digest + '.json'), opt_in_sources=('dart',))
    before = hashes(directory)
    assert store.raw('dart', 'https://example.invalid/refetched', content) == digest
    assert hashes(directory) == before
    assert not (directory / (digest + '.bin')).exists()


def test_new_dart_writer_is_default_off_and_exact_source_only(tmp_path, monkeypatch):
    monkeypatch.delenv('MARKET_COMPRESS_DART_RAW', raising=False)
    store = Store('sqlite://', tmp_path)
    first = store.raw('dart', 'https://example.invalid', b'fixture_old')
    assert (tmp_path / 'raw/dart' / (first + '.bin')).is_file()
    monkeypatch.setenv('MARKET_COMPRESS_DART_RAW', '1')
    second = store.raw('dart', 'https://example.invalid', b'fixture_new')
    assert (tmp_path / 'raw/dart' / (second + '.bin.gz')).is_file()
    assert not (tmp_path / 'raw/dart' / (second + '.bin')).exists()
    assert read_original(tmp_path / 'raw/dart', second) == b'fixture_new'
    xbrl = store.raw('dart-xbrl', 'https://example.invalid', b'PK\x03\x04fixture_zip')
    assert (tmp_path / 'raw/dart-xbrl' / (xbrl + '.bin')).is_file()


def test_refetch_does_not_hide_corrupt_dart_or_replace_receipt(tmp_path, monkeypatch):
    monkeypatch.delenv('MARKET_COMPRESS_DART_RAW', raising=False)
    store = Store('sqlite://', tmp_path)
    content = b'fixture_non_production'
    digest = store.raw('dart', 'https://example.invalid/original', content)
    directory = tmp_path / 'raw/dart'
    compact_one(directory / (digest + '.json'), opt_in_sources=('dart',))
    (directory / (digest + '.bin.gz')).write_bytes(b'broken gzip')
    before = hashes(directory)
    with pytest.raises(ValueError):
        store.raw('dart', 'https://example.invalid/refetched', content)
    assert hashes(directory) == before
    assert not (directory / (digest + '.bin')).exists()
