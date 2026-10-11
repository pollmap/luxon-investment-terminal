"""Apply a reviewed source/precompiled-web patch without touching database volumes."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import time
import urllib.request

ROOT = Path('/srv/services/korea-stocks')


def run(*args):
    subprocess.run(args, check=True)


def promote(version, expected, archive):
    if any(not re.fullmatch(r'[a-zA-Z0-9-]{1,80}', s) for s in (version, expected)):
        raise ValueError('Invalid release name')
    os.umask(0o077)
    with (ROOT / '.deploy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = (ROOT / 'current').resolve()
        if previous != ROOT / 'releases' / expected:
            raise RuntimeError('Active release changed')
        release = ROOT / 'releases' / version
        shutil.copytree(previous, release)
        with tarfile.open(archive) as source:
            for item in source.getmembers():
                target = (release / item.name).resolve()
                if not target.is_relative_to(release) or item.issym() or item.islnk():
                    raise ValueError('Unsafe patch member')
            source.extractall(release, filter='data')
        manifest = release / 'ops/market/retired-files.json'
        if manifest.exists():
            for relative in json.loads(manifest.read_text()):
                target = (release / relative).resolve()
                if not target.is_relative_to(release) or target.suffix not in ('.py','.ps1'):
                    raise ValueError('Unsafe retired file')
                target.unlink(missing_ok=True)
        (release / 'Dockerfile.patch').write_text(
            'FROM korea-stocks-api:' + expected + '\n'
            'USER root\n'
            # COPY alone leaves removed Python modules in the base image.
            'RUN rm -rf /app/services/market_dashboard\n'
            'COPY --chown=market:market services/market_dashboard /app/services/market_dashboard\n'
            'USER market\n')
        run('docker', 'build', '-q', '-t', 'korea-stocks-api:' + version,
            '-f', str(release / 'Dockerfile.patch'), str(release))
        with tarfile.open(archive) as source:
            frontend = any(m.name.startswith('web-build/') for m in source.getmembers())
        if frontend:
            (release / 'Dockerfile.frontend').write_text(
                'FROM korea-stocks-web:' + expected + '\n'
                'COPY --chown=node:node web-build /app/apps/web/.next\n'
                'COPY --chown=node:node web-server.js /app/apps/web/server.js\n')
            run('docker', 'build', '-q', '-t', 'korea-stocks-web:' + version,
                '-f', str(release / 'Dockerfile.frontend'), str(release))
        else:
            run('docker', 'tag', 'korea-stocks-web:' + expected, 'korea-stocks-web:' + version)
        run('docker', 'tag', 'korea-stocks-openalgo:' + expected, 'korea-stocks-openalgo:' + version)
        env = ROOT / 'shared/secrets/release.env'
        old = env.read_text()
        if 'RELEASE=' + expected not in old:
            raise RuntimeError('Release environment mismatch')
        compose = ['docker', 'compose', '--env-file', str(env), '-f', str(release / 'ops/market/compose.desktop.yml')]
        try:
            env.write_text(old.replace('RELEASE=' + expected, 'RELEASE=' + version))
            run(*compose, 'up', '-d')
            for _ in range(40):
                try:
                    with urllib.request.urlopen('http://127.0.0.1:8211/api/market/v1/health', timeout=5) as r:
                        assert json.load(r)['release'] == version
                    with urllib.request.urlopen('http://127.0.0.1:3211/stocks', timeout=10) as r:
                        assert r.status == 200
                    with urllib.request.urlopen('http://127.0.0.1:8211/api/market/v1/securities/005930?view=summary', timeout=10) as r:
                        assert json.load(r)['security']['code'] == '005930'
                    break
                except Exception:
                    time.sleep(2)
            else:
                raise RuntimeError('Release health failed')
            pending = ROOT / 'current.next'
            pending.symlink_to(release, target_is_directory=True)
            pending.replace(ROOT / 'current')
            (release / 'deployment.json').write_text(json.dumps({'release': version, 'previous': expected,
                'state': 'active', 'frontend_changed': frontend}))
            print('ACTIVATED', version)
        except Exception:
            env.write_text(old)
            run('docker', 'compose', '--env-file', str(env), '-f', str(previous / 'ops/market/compose.desktop.yml'), 'up', '-d')
            raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', required=True)
    parser.add_argument('--expected', required=True)
    parser.add_argument('--archive', type=Path, required=True)
    args = parser.parse_args()
    promote(args.version, args.expected, args.archive)
