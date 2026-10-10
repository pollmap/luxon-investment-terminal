"""Package API/worker and optional compiled web on the active shared VPS release."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = 'root@62.171.141.206'
REMOTE_ROOT = '/srv/services/korea-stocks'
IDENTITY = Path.home() / '.ssh/openclaw_vmi3138243_ed25519'
RUNTIME_SCRIPTS = ('scripts/replay_dart_period_archives.py',)


def execute(command, *, timeout=60, capture_output=True):
    result = subprocess.run(command, capture_output=capture_output, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Deployment command failed; existing services were not assumed changed')
    return result.stdout.decode(errors='replace').strip()


def package(destination, frontend=False):
    destination = Path(destination)
    files = []
    for folder in ('services/market_dashboard',):
        for parent, dirs, names in os.walk(ROOT / folder):
            dirs[:] = [name for name in dirs if name != '__pycache__' and not name.startswith('.next')]
            for name in names:
                path = Path(parent) / name
                if path.suffix in ('.pyc', '.tsbuildinfo') or name.startswith('.env'):
                    continue
                if path.is_symlink():
                    raise RuntimeError('Symlink is not allowed in an application patch')
                files.append(path)
    operations = [ROOT / path for path in (
        'ops/market/audit_toss_coverage.py',
        'ops/market/promote_backend_patch.py',
        'ops/market/monitor.py',
        'ops/market/backup.py',
        'ops/market/retire_duplicate_backup.py',
        'ops/market/retain_releases.py',
        'ops/market/compact_raw_archives.py',
        'ops/market/enable_financial_traces.py',
        'ops/market/review_samsung_comparative.py',
    )]
    if any(not path.is_file() or path.is_symlink() for path in operations):
        raise RuntimeError('Required plain operations source is missing or linked')
    files.extend(operations)
    readers = [ROOT / name for name in RUNTIME_SCRIPTS]
    if any(not path.is_file() or path.is_symlink() or path.parent.is_symlink()
           or getattr(path.parent, 'is_junction', lambda: False)() for path in readers):
        raise RuntimeError('Required plain archive reader is missing or linked')
    files.extend(readers)
    build = ROOT / 'apps/web/.next'
    if frontend:
        required = [build/'BUILD_ID', build/'standalone/apps/web/server.js',
                    build/'server/app/stocks/page.js']
        if not all(path.is_file() and not path.is_symlink() for path in required):
            raise RuntimeError('Complete production frontend build missing')
        if (not any(path.is_file() and not path.is_symlink() for path in (build/'static').rglob('*.js'))
                or not any(path.is_file() and not path.is_symlink() for path in (ROOT/'apps/web/public').rglob('*'))):
            raise RuntimeError('Frontend static or public assets missing')
        build_id = (build/'BUILD_ID').read_text().strip()
        if not build_id:
            raise RuntimeError('Frontend BUILD_ID is empty')
    temporary = destination.with_name(destination.name + '.partial')
    try:
        _write_package(temporary, files, frontend, build)
        if frontend and (build/'BUILD_ID').read_text().strip() != build_id:
            raise RuntimeError('Frontend build changed while packaging')
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return hashlib.sha256(destination.read_bytes()).hexdigest()


def _write_package(destination, files, frontend, build):
    with tarfile.open(destination, 'w:gz') as archive:
        for path in sorted(files):
            archive.add(path, arcname=path.relative_to(ROOT).as_posix(), recursive=False)
        if frontend:
            for folder, dirs, names in os.walk(build):
                if Path(folder) == build:
                    dirs[:] = [d for d in dirs if d not in ('cache', 'standalone', 'dev', 'diagnostics')]
                for name in names:
                    path = Path(folder)/name
                    if not name.endswith('.map'):
                        if path.is_symlink():
                            raise RuntimeError('Symlink is not allowed in compiled frontend')
                        archive.add(path, arcname='web-build/'+path.relative_to(build).as_posix(), recursive=False)
            archive.add(build/'standalone/apps/web/server.js', arcname='web-server.js', recursive=False)
            public = ROOT/'apps/web/public'
            for path in public.rglob('*'):
                if path.is_file() and not path.is_symlink():
                    archive.add(path, arcname='web-public/'+path.relative_to(public).as_posix(), recursive=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--release', required=True)
    parser.add_argument('--expected', required=True)
    parser.add_argument('--frontend', action='store_true')
    args = parser.parse_args()
    for value in (args.release, args.expected):
        if not re.fullmatch(r'[A-Za-z0-9-]{5,80}', value):
            raise SystemExit('Invalid release identifier')
    if not IDENTITY.is_file():
        raise SystemExit('Authorized SSH identity is unavailable')

    ssh = ['ssh', '-i', str(IDENTITY), '-o', 'BatchMode=yes', '-o',
           'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=yes', HOST]
    scp = ['scp', '-i', str(IDENTITY), '-o', 'BatchMode=yes', '-o',
           'StrictHostKeyChecking=yes']
    work = ROOT / 'work/releases' / args.release
    work.mkdir(parents=True, exist_ok=False)
    archive = work / 'backend-patch.tar.gz'
    digest = package(archive, args.frontend)
    remote_archive = f'{REMOTE_ROOT}/incoming/{args.release}.tar.gz'
    remote_helper = f'{REMOTE_ROOT}/incoming/promote_backend_patch.py'

    execute([*ssh, f'test "$(readlink -f {REMOTE_ROOT}/current)" = '
             f'"{REMOTE_ROOT}/releases/{args.expected}"'])
    execute([*ssh, f'install -d -m 700 {REMOTE_ROOT}/incoming'])
    execute([*scp, str(archive), f'{HOST}:{remote_archive}'])
    execute([*scp, str(ROOT / 'ops/market/promote_backend_patch.py'),
             f'{HOST}:{remote_helper}'])
    remote_digest = execute([*ssh, f'sha256sum {remote_archive}']).split()[0]
    if remote_digest != digest:
        raise RuntimeError('Uploaded patch checksum did not match')
    output = execute([*ssh, f'python3 {remote_helper} --version {args.release} '
                      f'--expected {args.expected} --archive {remote_archive}'
                      + (' --frontend' if args.frontend else '')], timeout=1800)
    print(json.dumps({'status': 'deployed', 'release': args.release,
                      'archive_sha256': digest, 'result': output}, ensure_ascii=False))


if __name__ == '__main__':
    main()
