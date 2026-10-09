"""Promote verified static assets on the existing stocks virtual host only.

Run on the VPS after the backend release is active. The archive contains the
flat apps/web/out directory, including build-manifest.json (tar -C out ... .).
No DB, shared volume, API location, container image or certificate is changed.
"""
import argparse
import errno
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import time

ROOT = Path('/srv/services/korea-stocks')
PUBLIC_ROOT = Path('/var/www/korea-stocks')
NGINX = Path('/etc/nginx/sites-available/hannun')
HOST = 'stocks.62-171-141-206.sslip.io'
WEB = 'korea-stocks-market-web-1'
MARKER = '# BEGIN KOREA STOCKS STATIC WEB'


def run(*args):
    return subprocess.check_output(args, timeout=90)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def member_name(name):
    while name.startswith('./'):
        name = name[2:]
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name or any(part.startswith('.') for part in path.parts):
        raise ValueError('Unsafe static archive path')
    return path.as_posix()


def inspect_archive(archive_path, expected_release, expected_sha):
    if not re.fullmatch(r'[a-fA-F0-9]{64}', expected_sha) or sha256(archive_path) != expected_sha.lower():
        raise ValueError('Static archive checksum mismatch')
    with tarfile.open(archive_path, 'r:gz') as archive:
        files = {}
        total = 0
        for member in archive.getmembers():
            name = member_name(member.name)
            if member.isdir():
                continue
            if not member.isfile() or name in files or name == '.':
                raise ValueError('Link, device or duplicate static member')
            if member.size > 32 * 1024**2:
                raise ValueError('Static asset unexpectedly large')
            total += member.size
            if total > 256 * 1024**2 or len(files) >= 10000:
                raise ValueError('Static archive unexpectedly large')
            files[name] = member
        if 'build-manifest.json' not in files:
            raise ValueError('Build manifest missing')
        manifest = json.load(archive.extractfile(files['build-manifest.json']))
        if manifest.get('profile') != 'static' or manifest.get('release') != expected_release:
            raise ValueError('Static build does not match active release')
        expected = {}
        for row in manifest.get('files', []):
            name = member_name(row['path'])
            if name in expected or name == 'build-manifest.json':
                raise ValueError('Duplicate manifest entry')
            if not isinstance(row.get('bytes'), int) or row['bytes'] < 0 or not re.fullmatch(r'[a-f0-9]{64}', row.get('sha256', '')):
                raise ValueError('Invalid manifest metadata')
            expected[name] = row
        if set(files) != set(expected) | {'build-manifest.json'}:
            raise ValueError('Archive differs from manifest file list')
        required = {'stocks.html', '404.html', 'legacy.html', 'legacy-entry.mjs', 'legacy-routes.mjs',
                    'stocks/sw.js', 'stocks/manifest.webmanifest'}
        if not required <= set(files) or not any(name.startswith('_next/static/') and name.endswith('.js') for name in files):
            raise ValueError('Required page, compatibility or PWA assets missing')
        for name, row in expected.items():
            member = files[name]
            if member.size != row['bytes']:
                raise ValueError('Static file size mismatch')
            with archive.extractfile(member) as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if digest != row['sha256']:
                raise ValueError('Static file checksum mismatch')
        if manifest.get('bytes') != sum(row['bytes'] for row in expected.values()):
            raise ValueError('Manifest total differs')
        return manifest


def extract_archive(archive_path, target):
    # The fresh directory cannot contain symlink parents. Entries were completely
    # validated before this function is called; never use tar extraction paths.
    target.mkdir(mode=0o755)
    with tarfile.open(archive_path, 'r:gz') as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            destination = target / member_name(member.name)
            destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            with archive.extractfile(member) as source, destination.open('xb') as output:
                shutil.copyfileobj(source, output)
            destination.chmod(0o644)


def block_end(text, start):
    depth = 0
    quote = None
    escape = False
    comment = False
    opened = False
    for index in range(start, len(text)):
        char = text[index]
        if comment:
            if char == '\n':
                comment = False
            continue
        if escape:
            escape = False
            continue
        if char == '\\':
            escape = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in ('"', "'"):
            quote = char
        elif char == '#':
            comment = True
        elif char == '{':
            depth += 1
            opened = True
        elif char == '}':
            depth -= 1
            if opened and depth == 0:
                return index + 1
    raise ValueError('Unbalanced Nginx block')


def static_locations(root):
    safe_root = str(root).replace('\\', '/')
    if any(char in safe_root for char in ' \n;{}"\''):
        raise ValueError('Unsafe Nginx static root')
    return f''' {MARKER}
 root {safe_root};
 location = / {{ try_files /legacy.html =404; add_header Cache-Control no-cache; }}
 location ~ ^/(terminal|screener|portfolio|system)/?$ {{ try_files /legacy.html =404; add_header Cache-Control no-cache; }}
 location ~ ^/company/[A-Za-z0-9.]+(?:/[A-Za-z-]+)?/?$ {{ try_files /legacy.html =404; add_header Cache-Control no-cache; }}
 location = /legacy.html {{ add_header Cache-Control no-cache; }}
 location = /legacy-entry.mjs {{ default_type application/javascript; add_header Cache-Control no-cache; }}
 location = /legacy-routes.mjs {{ default_type application/javascript; add_header Cache-Control no-cache; }}
 location = /stocks/sw.js {{ add_header Cache-Control no-cache; }}
 location = /stocks {{ try_files /stocks.html =404; add_header Cache-Control no-cache; }}
 location = /stocks/ {{ return 302 /stocks$is_args$args; }}
 location ^~ /_next/static/ {{ try_files $uri =404; add_header Cache-Control "public, max-age=31536000, immutable"; }}
 location / {{ try_files $uri $uri.html =404; }}
 # END KOREA STOCKS STATIC WEB
'''


def rewrite_nginx(original, root):
    if MARKER in original:
        if ' root ' + str(root).replace('\\', '/') + ';' not in original:
            raise ValueError('Different static root already configured')
        return original
    servers = []
    for match in re.finditer(r'(?m)^server\s*\{', original):
        end = block_end(original, match.start())
        block = original[match.start():end]
        if re.search(r'listen\s+443\s+ssl', block) and f'server_name {HOST};' in block:
            servers.append((match.start(), end, block))
    if len(servers) != 1:
        raise ValueError('Expected stocks HTTPS server not found exactly once')
    start, end, block = servers[0]
    patterns = [r'(?m)^\s*location = /\s*\{\s*return 302 /stocks;\s*\}',
                r'(?m)^\s*location /\s*\{\s*return 404;\s*\}',
                r'(?m)^\s*location ~ \^/\(stocks[^\n]+ \{']
    spans = []
    for index, pattern in enumerate(patterns):
        matches = list(re.finditer(pattern, block))
        if len(matches) != 1:
            raise ValueError('Unexpected web location; refusing broad config rewrite')
        match = matches[0]
        last = block_end(block, match.start()) if index == 2 else match.end()
        text = block[match.start():last]
        if index == 2 and ('proxy_pass http://127.0.0.1:3211;' not in text or '_next/' not in text):
            raise ValueError('Web upstream ownership mismatch')
        spans.append((match.start(), last))
    for first, last in sorted(spans, reverse=True):
        block = block[:first] + '\n' + block[last:]
    block = block[:-1] + static_locations(root) + '}'
    return original[:start] + block + original[end:]


def web_state():
    value = run('docker', 'inspect', '--format', '{{.Id}}|{{.State.Status}}|{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.service"}}', WEB).decode().strip()
    identity, state, project, service = value.split('|')
    if project != 'korea-stocks' or service != 'market-web' or state not in ('running', 'exited'):
        raise RuntimeError('Unexpected web container ownership/state')
    return {'id': identity, 'state': state}


def static_probe(route, loopback, remaining):
    base = ['curl', '--silent', '--show-error', '--compressed', '--max-time',
            str(max(0.05, min(4, remaining))), '--connect-timeout', str(max(0.05, min(2, remaining))),
            '--dump-header', '-', '--write-out', '\n__STATIC_SMOKE__%{http_code}\n']
    if loopback:
        base.extend(['--resolve', f'{HOST}:443:127.0.0.1'])
    result = run(*base, f'https://{HOST}/{route}')
    wire, separator, status = result.rpartition(b'\n__STATIC_SMOKE__')
    if not separator:
        raise RuntimeError('Static probe metadata missing')
    headers, separator, body = wire.partition(b'\r\n\r\n')
    if not separator:
        raise RuntimeError('Static probe headers missing')
    # Handle an informational/CONNECT header block without logging response body.
    while body.startswith(b'HTTP/'):
        following_headers, separator, following_body = body.partition(b'\r\n\r\n')
        if not separator:
            break
        headers, body = following_headers, following_body
    values = {}
    for line in headers.decode('latin1').splitlines()[1:]:
        if ':' in line:
            key, value = line.split(':', 1)
            values[key.lower()] = value.strip()
    return body, {'route': route, 'via': 'loopback' if loopback else 'public', 'status': int(status.strip()),
                  'actual_bytes': len(body), 'actual_sha256': hashlib.sha256(body).hexdigest(),
                  **{key: values[key] for key in ('content-type', 'content-encoding', 'age', 'x-market-cache', 'etag') if key in values}}


def smoke(manifest, timeout=15, interval=0.5):
    """Wait briefly for graceful Nginx worker replacement, then fail closed.

    Both routes must return the exact manifest bytes on loopback AND public
    HTTPS in one full pass. A status 200 or the prior web shell is not enough.
    Diagnostic output contains hashes/headers only, never HTML or API payloads.
    """
    if timeout <= 0 or interval <= 0:
        raise ValueError('Readiness timeout and interval must be positive')
    static_js = next(row for row in manifest['files'] if row['path'].startswith('_next/static/') and row['path'].endswith('.js'))
    html = next(row for row in manifest['files'] if row['path'] == 'stocks.html')
    began = time.monotonic()
    deadline = began + timeout
    attempts = 0
    last_mismatch = None
    while time.monotonic() < deadline:
        attempts += 1
        successful = []
        mismatch = None
        for loopback in (True, False):
            for route, expected in [('stocks?code=005930', html), (static_js['path'], static_js), ('build-manifest.json', None)]:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    mismatch = {'route': route, 'via': 'loopback' if loopback else 'public', 'reason': 'readiness_deadline'}
                    break
                try:
                    data, info = static_probe(route, loopback, remaining)
                    if info['status'] != 200:
                        mismatch = {**info, 'reason': 'http_status'}
                    elif expected and (info['actual_sha256'] != expected['sha256'] or len(data) != expected['bytes']):
                        mismatch = {**info, 'reason': 'content_mismatch', 'expected_bytes': expected['bytes'], 'expected_sha256': expected['sha256']}
                    elif expected is None:
                        build = json.loads(data)
                        if build.get('release') != manifest['release'] or build.get('files') != manifest['files']:
                            mismatch = {**info, 'reason': 'manifest_mismatch', 'expected_release': manifest['release'], 'actual_release': build.get('release')}
                    if mismatch:
                        break
                    successful.append(info)
                except Exception as error:
                    mismatch = {'route': route, 'via': 'loopback' if loopback else 'public', 'reason': type(error).__name__}
                    break
            if mismatch:
                break
        if not mismatch:
            return {'attempts': attempts, 'elapsed_seconds': round(time.monotonic() - began, 3),
                    'checks': successful, 'initial_mismatch': last_mismatch}
        if last_mismatch is None:
            last_mismatch = mismatch
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(interval, remaining))
    raise RuntimeError('Static readiness deadline exceeded: ' + json.dumps({'attempts': attempts,
        'elapsed_seconds': round(time.monotonic() - began, 3), 'first_mismatch': last_mismatch,
        'last_mismatch': mismatch}, ensure_ascii=False))


@contextmanager
def deployment_lock():
    import fcntl
    with (ROOT / '.deploy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def install_config(content, suffix):
    pending = NGINX.with_name(NGINX.name + suffix)
    if pending.exists() or pending.is_symlink():
        raise RuntimeError('Nginx temporary config already exists')
    try:
        pending.write_text(content, encoding='utf8')
        pending.chmod(NGINX.stat().st_mode & 0o777)
        pending.replace(NGINX)
    finally:
        pending.unlink(missing_ok=True)


def publish_assets(source, name):
    """Expose only verified public assets; never relax private service modes."""
    for directory in (PUBLIC_ROOT, PUBLIC_ROOT / 'releases'):
        if directory.is_symlink():
            raise RuntimeError('Public static directory must not be a symlink')
        directory.mkdir(mode=0o755, parents=True, exist_ok=True)
        directory.chmod(0o755)
    target = PUBLIC_ROOT / 'releases' / name
    if target.exists() or target.is_symlink():
        raise RuntimeError('Public static release already exists')
    def link_or_copy(src, dst):
        try:
            os.link(src, dst)
        except OSError as error:
            if error.errno != errno.EXDEV:
                raise
            shutil.copyfile(src, dst)
    shutil.copytree(source, target, copy_function=link_or_copy)
    for directory, _, _ in os.walk(target):
        Path(directory).chmod(0o755)
    return target


def switch_public(target, token):
    current = PUBLIC_ROOT / 'current'
    pending = PUBLIC_ROOT / ('current.' + token)
    if current.exists() and not current.is_symlink() or pending.exists() or pending.is_symlink():
        raise RuntimeError('Unexpected public static pointer')
    if not Path(target).resolve().is_relative_to((PUBLIC_ROOT / 'releases').resolve()):
        raise RuntimeError('Public static pointer escapes release directory')
    pending.symlink_to(target, target_is_directory=True)
    pending.replace(current)


def promote(expected_release, archive_path, expected_sha, dry_run=False):
    if not re.fullmatch(r'[A-Za-z0-9-]{5,80}', expected_release):
        raise ValueError('Invalid release identifier')
    release = ROOT / 'releases' / expected_release
    archive_path = Path(archive_path)
    if archive_path.is_symlink() or not archive_path.resolve().is_relative_to((ROOT / 'incoming').resolve()):
        raise ValueError('Archive must be a regular file inside service incoming')
    if release.is_symlink() or (ROOT / 'current').resolve() != release.resolve() or release.resolve().parent != (ROOT / 'releases').resolve():
        raise RuntimeError('Active release changed or escaped release directory')
    if NGINX.is_symlink():
        raise RuntimeError('Expected a regular stocks Nginx config')
    manifest = inspect_archive(archive_path, expected_release, expected_sha)
    original = NGINX.read_text(encoding='utf8')
    candidate = rewrite_nginx(original, PUBLIC_ROOT / 'current')
    state = web_state()
    if dry_run:
        return {'status': 'dry-run', 'release': expected_release, 'files': len(manifest['files']), 'bytes': manifest['bytes'], 'web': state['state']}
    with deployment_lock():
        if (ROOT / 'current').resolve() != release.resolve() or NGINX.read_text(encoding='utf8') != original:
            raise RuntimeError('Deployment/config changed during preflight')
        if sha256(archive_path) != expected_sha.lower():
            raise RuntimeError('Archive changed after preflight')
        if shutil.disk_usage(ROOT).free < manifest['bytes'] * 2 + 64 * 1024**2:
            raise RuntimeError('Insufficient disk space for static promotion')
        target = release / 'static-web'
        marker = release / 'static-web-deployment.json'
        if target.is_symlink() or marker.is_symlink() or target.exists() and not target.is_dir():
            raise RuntimeError('Unexpected static target or marker')
        previous_marker = marker.read_bytes() if marker.exists() else None
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        previous_static = release / ('static-web.previous-' + stamp) if target.exists() else None
        stage = release / ('.static-web-' + expected_sha[:12])
        if stage.exists() or stage.is_symlink():
            raise RuntimeError('Static staging directory already exists')
        extract_archive(archive_path, stage)
        backup = ROOT / 'backups' / 'nginx' / f'hannun-{stamp}-static-{expected_release}.conf'
        backup.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        backup.write_text(original, encoding='utf8')
        backup.chmod(0o600)
        changed = False
        stop_attempted = False
        swapped = False
        public_swapped = False
        previous_public = None
        public_target = None
        pending_marker = marker.with_suffix('.json.partial')
        marker_created = False
        current_public = PUBLIC_ROOT / 'current'
        if current_public.exists() or current_public.is_symlink():
            if not current_public.is_symlink() or not current_public.resolve().is_relative_to((PUBLIC_ROOT / 'releases').resolve()):
                raise RuntimeError('Unexpected existing public static pointer')
            previous_public = current_public.resolve()
        try:
            if previous_static:
                target.rename(previous_static)
            try:
                stage.rename(target)
                swapped = True
            except Exception:
                if previous_static:
                    previous_static.rename(target)
                raise
            public_target = publish_assets(target, expected_release + '-' + stamp)
            switch_public(public_target, 'candidate-' + stamp)
            public_swapped = True
            install_config(candidate, '.static-candidate')
            changed = True
            run('nginx', '-t')
            run('systemctl', 'reload', 'nginx')
            first_readiness = smoke(manifest)
            if state['state'] == 'running':
                if web_state()['id'] != state['id']:
                    raise RuntimeError('Web container changed before stop')
                stop_attempted = True
                run('docker', 'stop', '--time', '20', WEB)
            final_readiness = smoke(manifest)
            if pending_marker.exists() or pending_marker.is_symlink():
                raise RuntimeError('Static deployment marker temporary file exists')
            with pending_marker.open('x', encoding='utf8') as stream:
                marker_created = True
                stream.write(json.dumps({'state': 'active', 'release': expected_release,
                'archive_sha256': expected_sha.lower(), 'manifest_sha256': sha256(target / 'build-manifest.json'),
                'nginx_backup': str(backup), 'web_container_id': state['id'],
                'public_root': str(public_target),
                'readiness_before_stop': first_readiness, 'readiness_after_stop': final_readiness,
                'verified_at': datetime.now(timezone.utc).isoformat()}, indent=2))
            pending_marker.chmod(0o644)
            pending_marker.replace(marker)
        except Exception as error:
            rollback_errors = []
            if public_swapped:
                try:
                    if previous_public:
                        switch_public(previous_public, 'restore-' + stamp)
                    else:
                        current_public.unlink()
                except Exception as recovery:
                    rollback_errors.append(type(recovery).__name__)
            if swapped:
                try:
                    target.rename(release / ('static-web.failed-' + stamp))
                    if previous_static:
                        previous_static.rename(target)
                    if previous_marker is not None:
                        marker.write_bytes(previous_marker)
                    else:
                        marker.unlink(missing_ok=True)
                except Exception as recovery:
                    rollback_errors.append(type(recovery).__name__)
            if stop_attempted:
                try:
                    run('docker', 'start', WEB)
                except Exception as recovery:
                    rollback_errors.append(type(recovery).__name__)
            if changed:
                try:
                    install_config(original, '.static-restore')
                    run('nginx', '-t')
                    run('systemctl', 'reload', 'nginx')
                except Exception as recovery:
                    rollback_errors.append(type(recovery).__name__)
            if stop_attempted:
                try:
                    run('curl', '--fail', '--silent', '--show-error', '--max-time', '15',
                        '--retry', '4', '--retry-connrefused', '--retry-delay', '2',
                        'http://127.0.0.1:3211/stocks')
                except Exception as recovery:
                    rollback_errors.append(type(recovery).__name__)
            if marker_created:
                pending_marker.unlink(missing_ok=True)
            if rollback_errors:
                raise RuntimeError('Static promotion failed; rollback needs attention: ' + ', '.join(rollback_errors)) from error
            raise RuntimeError('Static promotion failed; prior Nginx/web state restored') from error
        return {'status': 'promoted', 'release': expected_release, 'files': len(manifest['files']),
                'bytes': manifest['bytes'], 'nginx_backup': str(backup), 'web': 'stopped', 'static_root': str(target),
                'previous_static': str(previous_static) if previous_static else None, 'marker': str(marker),
                'public_root': str(public_target), 'previous_public': str(previous_public) if previous_public else None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--expected-release', '--expected', dest='expected_release', required=True)
    parser.add_argument('--archive', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    result = promote(args.expected_release, args.archive, args.sha256, args.dry_run)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
