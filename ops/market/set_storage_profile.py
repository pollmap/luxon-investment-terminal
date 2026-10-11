"""Restore the approved bulk-collection storage gate without touching data services."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path('/srv/services/korea-stocks')


def run(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError('Scoped storage-policy operation failed')
    return result.stdout.strip()


def container_id(name):
    return run(['docker', 'inspect', '--format', '{{.Id}}', name])


def apply(expected, execute):
    if not re.fullmatch(r'[A-Za-z0-9-]{5,80}', expected):
        raise ValueError('Invalid expected release')
    release = ROOT / 'releases' / expected
    if (ROOT / 'current').resolve() != release:
        raise RuntimeError('Active release changed')
    env = ROOT / 'shared/secrets/release.env'
    before = env.read_bytes()
    lines = before.decode().splitlines()
    if lines.count('RELEASE=' + expected) != 1:
        raise RuntimeError('Release configuration changed')
    matches = [i for i, line in enumerate(lines) if line.startswith('MARKET_STORAGE_PROFILE=')]
    if len(matches) != 1 or lines[matches[0]] not in (
        'MARKET_STORAGE_PROFILE=high-utilization', 'MARKET_STORAGE_PROFILE=default'
    ):
        raise RuntimeError('Unexpected storage profile configuration')
    previous = lines[matches[0]].split('=', 1)[1]
    report = {'release': expected, 'previous': previous, 'profile': 'default',
              'stop_used_percent': 90, 'resume_below_used_percent': 80,
              'applied': execute, 'raw_and_database': 'unchanged'}
    if not execute or previous == 'default':
        return report
    protected = {name: container_id(name) for name in (
        'korea-stocks-market-api-1', 'korea-stocks-openalgo-1',
        'hannun-public-market-market-db-1', 'hannun-public-market-market-redis-1'
    )}
    worker = 'korea-stocks-market-worker-1'
    image = run(['docker', 'inspect', '--format', '{{.Config.Image}}', worker])
    if image != 'korea-stocks-api:' + expected:
        raise RuntimeError('Worker release differs')
    backup = env.with_name('release.env.before-default-storage-' + expected)
    with backup.open('xb') as stream:
        os.chmod(backup, 0o600)
        stream.write(before)
        stream.flush()
        os.fsync(stream.fileno())
    lines[matches[0]] = 'MARKET_STORAGE_PROFILE=default'
    updated = ('\n'.join(lines) + '\n').encode()
    compose = ['docker', 'compose', '--env-file', str(env), '-f',
               str(release / 'ops/market/compose.desktop.yml')]
    temporary = env.with_name('release.env.storage-next')
    try:
        if env.read_bytes() != before or (ROOT / 'current').resolve() != release:
            raise RuntimeError('Configuration changed during review')
        temporary.write_bytes(updated)
        temporary.chmod(0o600)
        temporary.replace(env)
        run([*compose, 'up', '-d', '--no-deps', 'market-worker'])
        values = json.loads(run(['docker', 'inspect', '--format', '{{json .Config.Env}}', worker]))
        if 'MARKET_STORAGE_PROFILE=default' not in values:
            raise RuntimeError('Storage profile did not activate')
        if any(container_id(name) != identifier for name, identifier in protected.items()):
            raise RuntimeError('A protected service changed')
        report['worker_recreated'] = True
        return report
    except Exception:
        temporary.write_bytes(before)
        temporary.chmod(0o600)
        temporary.replace(env)
        run([*compose, 'up', '-d', '--no-deps', 'market-worker'])
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-release', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(apply(args.expected_release, args.apply)))
