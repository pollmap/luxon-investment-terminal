"""Phase two: enable new-row compact writes after dual readers are deployed.

No existing data is rewritten or deleted. A durable reader requirement remains
even if worker activation fails: a short-lived worker may already have written
references, so removing that guard would make a later app rollback unsafe.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.request

ROOT = Path('/srv/services/korea-stocks')
FORMAT = 'source-trace-sha256-v1'


def atomic(path, data):
    if path.is_symlink():
        raise RuntimeError('Symlink configuration is not allowed')
    temporary = path.with_name(path.name + '.next')
    if temporary.exists() or temporary.is_symlink():
        raise RuntimeError('Pending configuration already exists')
    temporary.write_bytes(data)
    temporary.chmod(0o600)
    temporary.replace(path)


def enable_compose(content):
    existing = re.findall(r'^      MARKET_SHARED_SOURCE_TRACES:.*$', content, re.M)
    if existing:
        if len(existing) != 1 or existing[0] not in (
                "      MARKET_SHARED_SOURCE_TRACES: '0'", "      MARKET_SHARED_SOURCE_TRACES: '1'"):
            raise RuntimeError('Unexpected shared-trace setting')
        return content.replace(existing[0], "      MARKET_SHARED_SOURCE_TRACES: '1'", 1)
    marker = '      MARKET_RELEASE: ${RELEASE}\n'
    if content.count(marker) != 1 or 'environment: &common' not in content or '<<: *common' not in content:
        raise RuntimeError('Unexpected shared service environment')
    return content.replace(marker, marker + "      MARKET_SHARED_SOURCE_TRACES: '1'\n", 1)


def reader_state(service, expected):
    name = 'korea-stocks-' + service + '-1'
    info = subprocess.check_output(['docker', 'inspect', '--format',
        '{{.Id}}|{{.State.Status}}|{{.Config.Image}}|'
        '{{index .Config.Labels "com.docker.compose.project"}}|'
        '{{index .Config.Labels "com.docker.compose.service"}}', name], text=True).strip().split('|')
    if len(info) != 5 or info[1:] != ['running', 'korea-stocks-api:' + expected, 'korea-stocks', service]:
        raise RuntimeError('Unexpected active reader container')
    command = ('import json,os; from services.market_dashboard.source_trace_store import storage_capabilities; '
               'print(json.dumps({"capabilities":storage_capabilities(),'
               '"write_enabled":os.getenv("MARKET_SHARED_SOURCE_TRACES","0")=="1"}))')
    state = json.loads(subprocess.check_output(['docker', 'exec', name, 'python', '-c', command], text=True, timeout=15))
    if FORMAT not in state['capabilities'].get('read_formats', []):
        raise RuntimeError('Active app does not support shared-trace reads')
    return {'id': info[0], 'write_enabled': state['write_enabled']}


def require_reader(marker, expected):
    if marker.is_symlink():
        raise RuntimeError('Unexpected persistent storage contract path')
    if marker.exists():
        if marker.stat().st_size > 16384:
            raise RuntimeError('Unexpected persistent storage contract size')
        old = json.loads(marker.read_text())
        if old.get('version') != 1 or old.get('required_read_formats') != [FORMAT]:
            raise RuntimeError('Unexpected persistent storage contract')
        return old
    value = {'version': 1, 'required_read_formats': [FORMAT],
             'rollback_floor': expected, 'activated_at': datetime.now(timezone.utc).isoformat()}
    atomic(marker, (json.dumps(value, indent=2) + '\n').encode())
    return value


def enable(expected):
    if not re.fullmatch('[A-Za-z0-9-]{5,80}', expected):
        raise ValueError('Invalid release')
    os.umask(0o077)
    with (ROOT/'.deploy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        release = (ROOT/'current').resolve()
        if release != ROOT/'releases'/expected:
            raise RuntimeError('Active release changed')
        api = reader_state('market-api', expected)
        worker = reader_state('market-worker', expected)
        capability = json.loads((release/'services/market_dashboard/storage_capabilities.json').read_text())
        if capability != {'version': 1, 'read_formats': [FORMAT]}:
            raise RuntimeError('Release storage contract missing')
        config = release/'ops/market/compose.desktop.yml'
        if config.is_symlink():
            raise RuntimeError('Unexpected compose path')
        original = config.read_bytes()
        updated = enable_compose(original.decode()).encode()
        backup = ROOT/'backups/config'
        backup.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        saved = backup/f'compose-before-shared-traces-{stamp}.yml'
        saved.write_bytes(original)
        contract = require_reader(ROOT/'shared/data/storage-format.json', expected)
        compose = ['docker', 'compose', '--env-file', str(ROOT/'shared/secrets/release.env'), '-f', str(config)]
        try:
            atomic(config, updated)
            # Only the worker writes compact history; the API already has its
            # dual reader. Do not restart data services or the healthy API.
            subprocess.run([*compose, 'up', '-d', '--no-deps', 'market-worker'], check=True, timeout=90)
            deadline = time.monotonic() + 20
            while True:
                try:
                    active = reader_state('market-worker', expected)
                    if active['write_enabled']:
                        break
                except (RuntimeError, subprocess.SubprocessError):
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError('Compact worker readiness deadline exceeded')
                time.sleep(1)
            if reader_state('market-api', expected)['id'] != api['id']:
                raise RuntimeError('Protected API container changed')
            with urllib.request.urlopen('http://127.0.0.1:8211/api/market/v1/securities/005930?view=summary', timeout=15) as response:
                if json.load(response).get('security', {}).get('code') != '005930':
                    raise RuntimeError('Public reader smoke failed')
        except Exception:
            atomic(config, original)
            subprocess.run([*compose, 'up', '-d', '--no-deps', 'market-worker'], check=True, timeout=90)
            raise
        return {'status': 'enabled', 'release': expected, 'format': FORMAT,
                'worker_changed': active['id'] != worker['id'], 'api_unchanged': True,
                'rollback_floor': contract['rollback_floor'], 'backup': str(saved),
                'existing_rows_rewritten': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--expected', required=True)
    print(json.dumps(enable(parser.parse_args().expected)))
