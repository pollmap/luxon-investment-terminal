"""Retire one same-day VPS duplicate only after verified offsite recovery.

The newer backup and all other daily/weekly recovery points remain on the VPS.
No database access, recursive deletion, or automatic retention-policy change.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from ops.market.backup import (  # noqa: E402
    BACKUP_ROOTS,
    REGULAR,
    file_identity,
    metadata,
    offsite_recovery_status,
    open_backup_files,
)


def hash_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        if stream.read(5) != b'PGDMP':
            raise ValueError('Not a PostgreSQL custom archive')
        stream.seek(0)
        for chunk in iter(lambda: stream.read(1024**2), b''):
            digest.update(chunk)
    return digest.hexdigest()


def sync_directory(root):
    descriptor = os.open(root, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def checked_archive(root, name):
    if not isinstance(name, str) or not REGULAR.fullmatch(name):
        raise ValueError('Invalid exact archive name')
    dt.datetime.strptime(name[7:-5], '%Y%m%dT%H%M%SZ')
    path = root / name
    identity = file_identity(path)
    proof = metadata(root / (name + '.manifest.json'))
    if (proof.get('state') != 'completed' or proof.get('source') != name
            or proof.get('bytes') != identity['bytes']
            or proof.get('source_mtime_ns') != identity['mtime_ns']
            or proof.get('archive_list_verified') is not True
            or not re.fullmatch('[0-9a-f]{64}', str(proof.get('sha256', '')))
            or hash_file(path) != proof['sha256'] or file_identity(path) != identity):
        raise ValueError('Archive bytes and completion manifest differ')
    return identity, proof


def plan(root, source, successor, offsite):
    root = Path(root)
    if root.is_symlink() or not root.is_dir() or root.resolve() != root.absolute():
        raise ValueError('Invalid backup root')
    status = offsite_recovery_status(offsite)
    if status['state'] != 'verified' or offsite['source'] != source:
        raise ValueError('Fresh independent recovery proof required')
    if not REGULAR.fullmatch(successor or '') or source >= successor:
        raise ValueError('A strictly newer archive is required')
    # Requiring the same day prevents removing a unique daily/weekly point.
    if source[7:15] != successor[7:15]:
        raise ValueError('Unique daily recovery points are preserved')
    original, proof = checked_archive(root, source)
    newer, new_proof = checked_archive(root, successor)
    if original['bytes'] != offsite['bytes'] or proof['sha256'] != offsite['sha256']:
        raise ValueError('Offsite recovery does not match the exact archive')
    return {'source': source, 'successor': successor, 'source_identity': original,
            'successor_identity': newer, 'sha256': proof['sha256'],
            'successor_sha256': new_proof['sha256'],
            'offsite_receipt_sha256': hashlib.sha256(json.dumps(
                offsite, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
            'reclaimable_allocated_bytes': original['allocated_bytes'],
            'unique_recovery_points_removed': 0, 'applied': False}


def retire_locked(root, source, successor, offsite, *, apply=False):
    record = plan(root, source, successor, offsite)
    identities = {source: record['source_identity'], successor: record['successor_identity']}
    if open_backup_files(identities):
        raise ValueError('A backup archive is currently in use')
    if not apply:
        return record
    stamp = dt.datetime.now(dt.UTC).strftime('%Y%m%dT%H%M%S%fZ')
    report = root / ('duplicate-retirement-' + stamp + '.json')
    # Preserve the proof together with the intent BEFORE unlinking the one file.
    with report.open('x', encoding='utf-8') as stream:
        json.dump({'planned_at': dt.datetime.now(dt.UTC).isoformat(),
                   **record, 'offsite_recovery': offsite}, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(root)
    for name, expected in identities.items():
        if file_identity(root / name) != expected:
            raise ValueError('Backup changed after verification')
    if open_backup_files(identities):
        raise ValueError('Backup became active before retirement')
    (root / source).unlink()
    sync_directory(root)
    if (root / source).exists() or file_identity(root / successor) != record['successor_identity']:
        raise ValueError('Retirement post-check failed')
    record.update(applied=True, source_removed=True, successor_preserved=True,
                  completed_at=dt.datetime.now(dt.UTC).isoformat(), intent=report.name)
    with report.with_suffix('.completed.json').open('x', encoding='utf-8') as stream:
        json.dump(record, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(root)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=BACKUP_ROOTS[-1])
    parser.add_argument('--source', required=True)
    parser.add_argument('--successor', required=True)
    parser.add_argument('--offsite-receipt', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.root not in BACKUP_ROOTS or args.root.resolve() != args.root:
        raise ValueError('Only the exact owned backup root is supported')
    lock = args.root / '.backup.lock'
    if lock.is_symlink():
        raise ValueError('Linked backup lock')
    import fcntl
    with lock.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = retire_locked(args.root, args.source, args.successor,
                               metadata(args.offsite_receipt), apply=args.apply)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
