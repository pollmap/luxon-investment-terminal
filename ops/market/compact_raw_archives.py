"""Bounded maintenance. DART needs two deployed readers and a durable rollback guard.

Default sources stay Toss/Naver/FnGuide. DART runs on the service host with exact
hashes, never a prefix scan. No provider calls, database writes, or app restarts.
"""

import argparse
import gzip
import hashlib
import json
import re
import shutil
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.market_dashboard.raw_archive import (  # noqa: E402
    COMPRESSED_SOURCES,
    OPT_IN_COMPRESSED_SOURCES,
    atomic_bytes,
    compact_one,
    read_original_verified,
)

SERVICE_ROOT = Path('/srv/services/korea-stocks')
CONTAINER_RAW = Path('/app/storage/public-market/raw')
HOST_RAW = Path('/var/lib/docker/volumes/hannun-public-market_market-sources/_data/raw')
DART_FORMAT = 'raw-dart-gzip-v2'
READER_FILES = (
    'services/market_dashboard/storage_capabilities.json',
    'services/market_dashboard/raw_archive.py',
    'services/market_dashboard/store.py',
    'scripts/replay_dart_period_archives.py',
    'ops/market/compact_raw_archives.py',
)
MAX_FILES = 25
MAX_SECONDS = 120
MAX_ORIGINAL_BYTES = 16 * 1024**2
MAX_TOTAL_BYTES = 64 * 1024**2


def plain_file(path, limit):
    if (path.is_symlink() or path.parent.is_symlink() or not path.is_file()
            or path.stat().st_size > limit):
        raise ValueError('Missing, linked, or oversized maintenance input')
    return path.read_bytes()


def check_limits(max_files, max_seconds, max_original_bytes, max_total_bytes):
    for value, ceiling in ((max_files, MAX_FILES), (max_seconds, MAX_SECONDS),
                           (max_original_bytes, MAX_ORIGINAL_BYTES),
                           (max_total_bytes, MAX_TOTAL_BYTES)):
        if type(value) is not int or not 0 < value <= ceiling:
            raise ValueError('Compaction limit exceeds the canary budget')


def checked_original(envelope, limit):
    """Bound decompression before using the strict, all-copy hash reader."""
    directory, digest = envelope.parent, envelope.stem
    if not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('Invalid source hash')
    metadata_limit = limit * 8 + 65536  # legacy JSON may escape UTF-8 characters
    meta = json.loads(plain_file(envelope, metadata_limit))
    if not isinstance(meta, dict) or meta.get('sha256') != digest:
        raise ValueError('Archive identity mismatch')
    size = meta.get('bytes')
    if size is not None and (type(size) is not int or size < 0 or size > limit):
        raise ValueError('Original exceeds the file budget')
    for suffix, bound in (('.bin', limit), ('.bin.gz', limit),
                          ('.json.legacy.gz', metadata_limit)):
        path = directory / (digest + suffix)
        if not path.exists() and not path.is_symlink():
            continue
        plain_file(path, bound + 65536)
        if suffix.endswith('.gz'):
            with gzip.open(path, 'rb') as stream:
                if len(stream.read(bound + 1)) > bound:
                    raise ValueError('Expanded archive exceeds the file budget')
    content = read_original_verified(directory, digest)
    if len(content) > limit:
        raise ValueError('Original exceeds the file budget')
    return content, meta


@contextmanager
def deadline(seconds):
    """Unix hard deadline; interrupted atomic files remain restartable."""
    if not hasattr(signal, 'setitimer'):
        raise RuntimeError('A Unix hard deadline is required for archive maintenance')
    def expired(*_):
        raise TimeoutError('Archive maintenance deadline')
    previous = signal.signal(signal.SIGALRM, expired)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        signal.signal(signal.SIGALRM, previous)


def compact_batch(root, sources, *, apply=False, hashes=(), max_files=5, max_seconds=30,
                  max_original_bytes=MAX_ORIGINAL_BYTES, max_total_bytes=MAX_TOTAL_BYTES):
    """Testable bounded batch. The CLI supplies the hard deadline and locks."""
    check_limits(max_files, max_seconds, max_original_bytes, max_total_bytes)
    selected = tuple(sources)
    if not selected or not set(selected) <= COMPRESSED_SOURCES | OPT_IN_COMPRESSED_SOURCES:
        raise ValueError('Unsupported archive source')
    if 'dart' in selected and (selected != ('dart',) or not hashes):
        raise ValueError('DART requires an exact source and explicit original hashes')
    if (len(hashes) > MAX_FILES or len(set(hashes)) != len(hashes)
            or any(not re.fullmatch('[0-9a-f]{64}', digest) for digest in hashes)):
        raise ValueError('Invalid or excessive original hash selection')
    started = time.monotonic()
    report = {'apply': apply, 'sources': list(selected), 'files': 0, 'verified_bytes': 0,
              'reclaimed_bytes': 0, 'errors': 0, 'error_types': {}, 'records': [],
              'stop_reason': 'selection_finished'}
    try:
        for source in selected:
            directory = root / source
            if directory.is_symlink() or directory.resolve().parent != root.resolve():
                raise ValueError('Unexpected source directory')
            paths = (directory / (digest + '.json') for digest in hashes) if hashes else (
                directory.glob('*.json'))
            for envelope in paths:
                if report['files'] >= max_files:
                    report['stop_reason'] = 'file_limit'
                    return report
                if time.monotonic() - started >= max_seconds:
                    report['stop_reason'] = 'time_limit'
                    return report
                if not hashes:
                    # Default batches resume past completed files; this is a
                    # routing check, not a new claim that skipped files passed
                    # today's verification. Explicit canaries always reverify.
                    meta = json.loads(plain_file(envelope, max_original_bytes * 8 + 65536))
                    if (isinstance(meta, dict) and meta.get('archive_version') == 2
                            and meta.get('payload') is None
                            and meta.get('binary_file') == envelope.stem + '.bin.gz'
                            and envelope.with_suffix('.bin.gz').is_file()
                            and not envelope.with_suffix('.bin').exists()):
                        continue
                remaining = max_total_bytes - report['verified_bytes']
                if remaining <= 0:
                    report['stop_reason'] = 'byte_limit'
                    return report
                before, metadata = checked_original(envelope, min(max_original_bytes, remaining))
                report['verified_bytes'] += len(before)
                record = {'source': source, 'sha256': envelope.stem,
                          'original_bytes': len(before), 'status': 'verified'}
                report['records'].append(record)
                report['files'] += 1
                if apply:
                    old_envelope = envelope.read_bytes()
                    saved = compact_one(
                        envelope, opt_in_sources=('dart',) if source == 'dart' else ())
                    after, new_meta = checked_original(envelope, max_original_bytes)
                    physical = {'payload', 'binary_file', 'archive_version'}
                    if (after != before or {k: v for k, v in metadata.items() if k not in physical}
                            != {k: v for k, v in new_meta.items() if k not in physical}):
                        raise ValueError('Compacted original or metadata changed')
                    if metadata.get('archive_version') != 2:
                        legacy = envelope.with_name(envelope.stem + '.json.legacy.gz')
                        if gzip.decompress(legacy.read_bytes()) != old_envelope:
                            raise ValueError('Legacy envelope changed')
                    record.update(status='compacted', original_hash_verified=True,
                                  metadata_preserved=True, legacy_envelope_preserved=True)
                    report['reclaimed_bytes'] += saved
        return report
    except TimeoutError:
        report['stop_reason'] = 'time_limit'
        return report
    except (ValueError, OSError, EOFError) as error:
        # Never return parser messages containing original text or URLs.
        kind = type(error).__name__
        report.update(errors=1, error_types={kind: 1}, stop_reason='verification_failed')
        return report
    finally:
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)


def _read_contract():
    path = SERVICE_ROOT / 'shared/data/storage-format.json'
    raw = plain_file(path, 16384)
    state = json.loads(raw)
    formats = state.get('required_read_formats')
    if (state.get('version') != 1 or not isinstance(formats, list)
            or any(not isinstance(item, str) for item in formats)):
        raise RuntimeError('Existing persistent storage contract is invalid')
    return path, raw, state


def _release_receipt(release, required):
    if any((release / name).resolve() != release / name for name in READER_FILES):
        raise RuntimeError('Linked deployed reader path')
    receipts = {name: hashlib.sha256(plain_file(release / name, 4 * 1024**2)).hexdigest()
                for name in READER_FILES}
    capability = json.loads((release / READER_FILES[0]).read_bytes())
    formats = capability.get('read_formats')
    if (capability.get('version') != 1 or not isinstance(formats, list)
            or any(not isinstance(item, str) for item in formats)
            or not set(required) <= set(formats)):
        raise RuntimeError('DART compaction requires a compatible deployed replay reader')
    return receipts


def _image_receipt(command, expected_receipt):
    code = (
        'import hashlib,json; from pathlib import Path; '
        'from services.market_dashboard.raw_archive import read_original_verified; '
        f'files={READER_FILES!r}; '
        '[compile((Path("/app")/p).read_bytes(),p,"exec") for p in files if p.endswith(".py")]; '
        'print(json.dumps({p:hashlib.sha256((Path("/app")/p).read_bytes()).hexdigest() '
        'for p in files}))'
    )
    result = subprocess.run([*command, 'python', '-c', code], capture_output=True, timeout=20)
    if result.returncode or json.loads(result.stdout) != expected_receipt:
        raise RuntimeError('Image and deployed DART reader hashes do not match')


def validate_dart_readers(expected, rollback):
    if (any(not re.fullmatch('[A-Za-z0-9-]{5,80}', value or '')
            for value in (expected, rollback)) or expected == rollback):
        raise ValueError('Distinct active and rollback release identifiers are required')
    release = SERVICE_ROOT / 'releases' / expected
    fallback = SERVICE_ROOT / 'releases' / rollback
    if ((SERVICE_ROOT / 'current').resolve(strict=True) != release
            or release.is_symlink() or fallback.is_symlink()
            or not fallback.is_dir()):
        raise RuntimeError('Active or rollback release does not match')
    path, raw, state = _read_contract()
    required = [*state['required_read_formats']]
    if DART_FORMAT not in required:
        required.append(DART_FORMAT)
    current_receipt = _release_receipt(release, required)
    fallback_receipt = _release_receipt(fallback, required)
    if Path(__file__).resolve() != release / 'ops/market/compact_raw_archives.py':
        raise RuntimeError('Run the active release compactor')
    for service in ('market-api', 'market-worker'):
        name = 'korea-stocks-' + service + '-1'
        template = ('{{.State.Status}}|{{.Config.Image}}|'
                    '{{index .Config.Labels "com.docker.compose.project"}}|'
                    '{{index .Config.Labels "com.docker.compose.service"}}|{{.Config.User}}')
        result = subprocess.run(['docker', 'inspect', '--format', template, name],
                                capture_output=True, text=True, timeout=10)
        if (result.returncode or result.stdout.strip().split('|') != [
                'running', 'korea-stocks-api:' + expected, 'korea-stocks', service, 'market']):
            raise RuntimeError('Unexpected live DART reader')
        _image_receipt(['docker', 'exec', name], current_receipt)
    _image_receipt(['docker', 'run', '--rm', '--network', 'none', '--read-only',
                   '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                   '--entrypoint', '', 'korea-stocks-api:' + rollback], fallback_receipt)
    return path, raw, {**state, 'required_read_formats': required,
                       'previous_rollback_floor': state.get('rollback_floor'),
                       'rollback_floor': rollback,
                       'raw_dart': {'activated_release': expected, 'rollback_release': rollback,
                                    'checked_at': datetime.now(UTC).isoformat(),
                                    'reader_sha256': current_receipt,
                                    'rollback_reader_sha256': fallback_receipt}}


def run_dart(args):
    """Host opt-in. A durable rollback floor precedes any archive mutation."""
    import fcntl
    if args.root is not None and args.root != HOST_RAW:
        raise ValueError('DART canary uses only the owned host archive volume')
    root = HOST_RAW.resolve(strict=True)
    if root != HOST_RAW or HOST_RAW.is_symlink():
        raise ValueError('Unexpected owned raw archive mount')
    if (not args.sha256 or len(args.sha256) > MAX_FILES
            or len(set(args.sha256)) != len(args.sha256)
            or any(not re.fullmatch('[0-9a-f]{64}', digest) for digest in args.sha256)):
        raise ValueError('DART requires a bounded exact hash selection')
    check_limits(args.max_files, args.max_seconds, args.max_original_bytes, args.max_total_bytes)
    deploy_lock = SERVICE_ROOT / '.deploy.lock'
    if deploy_lock.is_symlink():
        raise ValueError('Linked deployment lock')
    with deploy_lock.open('r') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path, original, contract = validate_dart_readers(args.expected, args.rollback)
        if args.apply:
            if path.read_bytes() != original:
                raise RuntimeError('Persistent storage contract changed')
            atomic_bytes(path, (json.dumps(contract, indent=2) + '\n').encode())
            # Keep the guard even after a later timeout or verification failure.
        report = run_container_batch(args)
        report.update(release=args.expected, rollback_release=args.rollback,
                      durable_reader_requirement=args.apply)
        return report


def run_container_batch(args):
    """Keep original file ownership: Docker's existing market user writes files."""
    options = {name: getattr(args, name) for name in (
        'apply', 'sha256', 'max_files', 'max_seconds', 'max_original_bytes', 'max_total_bytes')}
    options['expected'] = args.expected
    code = (
        'import json,os,runpy,sys; from pathlib import Path; from types import SimpleNamespace; '
        'a=SimpleNamespace(**json.load(sys.stdin)); '
        'assert os.getenv("MARKET_RELEASE")==a.expected, "Active reader changed"; '
        'm=runpy.run_path("/app/ops/market/compact_raw_archives.py"); '
        'r=m["run_batch_locked"](Path("/app/storage/public-market/raw"),("dart",),a); '
        'r["new_dart_writer_enabled"]=os.getenv("MARKET_COMPRESS_DART_RAW","0")=="1"; '
        'print(json.dumps(r))'
    )
    result = subprocess.run(
        ['docker', 'exec', '-i', 'korea-stocks-market-api-1', 'python', '-c', code],
        input=json.dumps(options).encode(), capture_output=True, timeout=args.max_seconds + 15)
    if result.returncode:
        raise RuntimeError('Bounded DART canary did not return a verification receipt')
    report = json.loads(result.stdout)
    if (not isinstance(report, dict) or report.get('sources') != ['dart']
            or report.get('apply') is not args.apply or type(report.get('errors')) is not int):
        raise RuntimeError('Unexpected DART verification receipt')
    return report


def run_batch_locked(root, sources, args):
    import fcntl
    check_limits(args.max_files, args.max_seconds, args.max_original_bytes, args.max_total_bytes)
    lock_path = root / '.compaction.lock'
    if lock_path.is_symlink():
        raise ValueError('Linked archive lock')
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with deadline(args.max_seconds):
            report = compact_batch(root, sources, apply=args.apply, hashes=args.sha256,
                                   max_files=args.max_files, max_seconds=args.max_seconds,
                                   max_original_bytes=args.max_original_bytes,
                                   max_total_bytes=args.max_total_bytes)
    disk = shutil.disk_usage(root)
    report['disk_used_percent'] = round((disk.total - disk.free) / disk.total * 100, 2)
    return report


def parser():
    result = argparse.ArgumentParser()
    result.add_argument('--root', type=Path)
    result.add_argument('--apply', action='store_true')
    result.add_argument('--source', action='append',
                        choices=sorted(COMPRESSED_SOURCES | OPT_IN_COMPRESSED_SOURCES))
    result.add_argument('--sha256', action='append', default=[])
    result.add_argument('--expected')
    result.add_argument('--rollback')
    result.add_argument('--max-files', type=int, default=5)
    result.add_argument('--max-seconds', type=int, default=30)
    result.add_argument('--max-original-bytes', type=int, default=MAX_ORIGINAL_BYTES)
    result.add_argument('--max-total-bytes', type=int, default=MAX_TOTAL_BYTES)
    return result


def main():
    args = parser().parse_args()
    sources = tuple(args.source or sorted(COMPRESSED_SOURCES))
    if 'dart' in sources:
        if sources != ('dart',):
            raise ValueError('DART cannot be combined with other sources')
        report = run_dart(args)
    else:
        root = (args.root or CONTAINER_RAW).resolve(strict=True)
        if root != CONTAINER_RAW:
            raise ValueError('Unexpected mounted raw archive root')
        report = run_batch_locked(root, sources, args)
    print(json.dumps(report), flush=True)
    if report['errors']:
        raise SystemExit(2)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({'status': 'blocked', 'error_type': type(error).__name__}), flush=True)
        raise SystemExit(2) from None
