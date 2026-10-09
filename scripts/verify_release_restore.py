"""Restore one archived inactive release on the PC and verify every file byte."""
import hashlib
import json
from pathlib import Path
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'work/operations-20261008'


def main():
    manifest = json.loads((OUT/'release-archive-verified.json').read_text())
    archive = Path(manifest['local_archive'])
    if hashlib.file_digest(archive.open('rb'), 'sha256').hexdigest() != manifest['archive_sha256']:
        raise ValueError('Archive checksum changed')
    candidate = manifest['candidates'][0]
    # A short scratch root avoids Win32's legacy path limit for Next runtime files.
    target = (Path(tempfile.gettempdir())/'stock-restore-v89').resolve()
    target.mkdir(exist_ok=True)
    total, count = 0, 0
    with tarfile.open(archive, 'r:gz') as tar:
        members = [m for m in tar.getmembers() if m.name.split('/')[0] == candidate['name']]
        for m in members:
            if not (target/m.name).resolve().is_relative_to(target) or m.issym():
                raise ValueError('Unsafe restoration path')
        tar.extractall(target, members=members, filter='data')
        for m in members:
            if not (m.isfile() or m.islnk()):
                continue
            saved = target/m.name
            with tar.extractfile(m) as original, saved.open('rb') as restored:
                if hashlib.file_digest(original, 'sha256').digest() != hashlib.file_digest(restored, 'sha256').digest():
                    raise ValueError('Restored content mismatch')
            total += saved.stat().st_size
            count += 1
    if total != candidate['bytes']:
        raise ValueError('Restored release is incomplete')
    result = {'release': candidate['name'], 'restored_files': count, 'restored_bytes': total,
              'archive_sha256': manifest['archive_sha256'], 'status': 'verified',
              'scope': 'inactive application files; not a database restore'}
    (OUT/'release-restore-verified.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
