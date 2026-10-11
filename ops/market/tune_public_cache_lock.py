"""Disable redundant Nginx cache-miss waiting; application singleflight remains.

Preserves quote lifetime, API routes, TLS, authentication and other virtual hosts.
Run only after the expected API release is active. The original file is retained.
"""

import argparse
import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path('/srv/services/korea-stocks')
CONFIG = Path('/etc/nginx/sites-available/hannun')


def digest(value):
    return hashlib.sha256(value).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-release', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9-]{1,80}', args.expected_release):
        raise ValueError('Invalid release')
    if (ROOT / 'current').resolve().name != args.expected_release:
        raise RuntimeError('Active release changed')
    original = CONFIG.read_bytes()
    old = b'  proxy_cache_lock on;'
    if original.count(old) != 1:
        raise RuntimeError('Expected one unchanged project cache lock')
    updated = original.replace(old, b'  proxy_cache_lock off;', 1)
    assert updated.count(b'  proxy_cache_valid 200 1s;') == 1
    report = {'release': args.expected_release, 'applied': args.apply,
              'before_sha256': digest(original), 'after_sha256': digest(updated),
              'quote_cache_seconds': 1, 'application_singleflight': 'retained'}
    if args.apply:
        stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
        folder = ROOT / 'backups/nginx'
        folder.mkdir(parents=True, exist_ok=True)
        backup = folder / ('hannun-' + stamp + '-before-cache-lock-off.conf')
        with backup.open('xb') as stream:
            stream.write(original)
        try:
            CONFIG.write_bytes(updated)
            subprocess.run(['nginx', '-t'], check=True, capture_output=True, timeout=15)
            subprocess.run(['systemctl', 'reload', 'nginx'], check=True, timeout=15)
        except BaseException:
            CONFIG.write_bytes(original)
            subprocess.run(['nginx', '-t'], check=True, capture_output=True, timeout=15)
            subprocess.run(['systemctl', 'reload', 'nginx'], check=True, timeout=15)
            raise
        report['backup'] = str(backup)
        (ROOT / 'current/cache-lock-tuning.json').write_text(
            json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
