"""Promote app patches; optionally replace web, preserving shared data services."""
import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import tarfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path, PurePosixPath

ROOT = Path('/srv/services/korea-stocks')
PUBLIC_ROOT = Path('/var/www/korea-stocks')
PROJECT = 'korea-stocks'
BACKEND = ('market-api', 'market-worker')
PUBLIC_HOST = 'stocks.62-171-141-206.sslip.io'
DART_FORMAT = 'raw-dart-gzip-v2'
RUNTIME_SCRIPTS = ('scripts/replay_dart_period_archives.py',)
RUNTIME_OPERATIONS = ('ops/market/compact_raw_archives.py',
                      'ops/market/review_samsung_comparative.py')


def _is_link(path):
    # Junctions only occur in local Windows rehearsals; never traverse them as
    # ordinary directories. Production symbolic links have the same boundary.
    return path.is_symlink() or getattr(path, 'is_junction', lambda: False)()


def _snapshot_name(name):
    match = re.fullmatch(r'static-web\.(?:previous|failed)-([0-9]{8}T[0-9]{12}Z)', name)
    if not match:
        return False
    try:
        datetime.strptime(match[1], '%Y%m%dT%H%M%S%fZ')
    except ValueError:
        return False
    return True


def _replacement_names(relative, frontend):
    if relative == Path('services'):
        return {'market_dashboard'}
    if relative == Path('scripts'):
        return {Path(name).name for name in RUNTIME_SCRIPTS}
    if frontend and relative == Path('.'):
        return {'web-build', 'web-server.js', 'web-public'}
    return set()


def _check_retained_links(root, excluded, *, frontend=False, public=False):
    def failed(error):
        raise error

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=failed):
        relative = Path(directory).relative_to(root)
        ignored = set() if public else _replacement_names(relative, frontend)
        if not public and relative == Path('.'):
            ignored |= {path.name for path in excluded}
        for name in dirs[:] + files:
            path = Path(directory)/name
            if name in ignored:
                if name in dirs:
                    dirs.remove(name)
                continue
            if not _is_link(path):
                continue
            if name in dirs:
                dirs.remove(name)
            target = path.resolve(strict=public)
            if any(target.is_relative_to(snapshot) for snapshot in excluded):
                raise RuntimeError('Retained path references an excluded static snapshot')
            if public and not target.is_relative_to(root):
                raise RuntimeError('Active static asset link escapes its public directory')


def _static_snapshot_exclusions(previous, frontend):
    excluded = []
    for path in previous.iterdir():
        if not _snapshot_name(path.name):
            continue
        if _is_link(path):
            raise RuntimeError('Static snapshot candidate must not be a link')
        # Names alone do not make regular files into deployment snapshots.
        if path.is_dir():
            if path.resolve(strict=True) != path:
                raise RuntimeError('Static snapshot candidate escapes the release')
            excluded.append(path)
    if not excluded:
        return set()

    marker = previous/'static-web-deployment.json'
    active = False
    state = {}
    if marker.exists() or _is_link(marker):
        if _is_link(marker) or not marker.is_file() or marker.stat().st_size > 16384:
            raise RuntimeError('Invalid static deployment marker')
        state = json.loads(marker.read_text())
        active = state.get('state') == 'active'
    current = PUBLIC_ROOT/'current'
    if current.exists() or _is_link(current):
        if not _is_link(current):
            raise RuntimeError('Active public static root must be a link')
        target = current.resolve(strict=True)
        if any(target.is_relative_to(snapshot) for snapshot in excluded):
            raise RuntimeError('Active public root references an excluded static snapshot')
        managed_path = PUBLIC_ROOT/'releases'
        if _is_link(managed_path):
            raise RuntimeError('Public static releases directory must not be a link')
        managed = managed_path.resolve(strict=True)
        if not target.is_dir() or target == managed or not target.is_relative_to(managed):
            raise RuntimeError('Active public static root escapes managed releases')
        recorded = state.get('public_root')
        if active and (not isinstance(recorded, str)
                       or Path(recorded).resolve(strict=True) != target):
            raise RuntimeError('Active public static root does not match the deployment marker')
        _check_retained_links(target, excluded, public=True)
    elif active:
        raise RuntimeError('Active public static root is missing')
    _check_retained_links(previous, excluded, frontend=frontend)
    return {path.name for path in excluded}


def copy_release(previous, release, frontend=False):
    """Copy current artifacts, not old static rollback copies; delete nothing.

    Called under the same deployment lock as the static promoter. Public and
    retained links are checked before mkdir/copy, while shared data links remain
    intact. Existing release directories retain their own rollback snapshots.
    """
    previous = previous.resolve(strict=True)
    if release.exists() or _is_link(release):
        raise RuntimeError('Release already exists or is a link')
    if release.parent.resolve(strict=True) != previous.parent:
        raise RuntimeError('Candidate release escapes the release directory')
    excluded = _static_snapshot_exclusions(previous, frontend)

    def keep_unchanged(directory, names):
        relative = Path(directory).relative_to(previous)
        ignored = _replacement_names(relative, frontend)
        if relative == Path('.'):
            ignored |= excluded
        return ignored & set(names)

    # Backend sources are replaced, not overlaid. Static-web, its manifest,
    # Node fallback and deployment/data files retain their original contracts.
    shutil.copytree(previous, release, symlinks=True, ignore=keep_unchanged)
    return sorted(excluded)


def validate_storage_reader(release):
    """Refuse an app rollback that cannot read already-written shared data."""
    marker = ROOT / 'shared/data/storage-format.json'
    if not marker.exists():
        return
    if marker.is_symlink() or marker.stat().st_size > 16384:
        raise RuntimeError('Invalid persistent storage contract')
    state = json.loads(marker.read_text())
    required = state.get('required_read_formats')
    if (state.get('version') != 1 or not isinstance(required, list)
            or any(not isinstance(item, str) for item in required)):
        raise RuntimeError('Invalid persistent storage contract')
    capability = release / 'services/market_dashboard/storage_capabilities.json'
    if not capability.is_file() or capability.is_symlink() or capability.stat().st_size > 16384:
        raise RuntimeError('Candidate app lacks required storage reader')
    supplied = json.loads(capability.read_text())
    if supplied.get('version') != 1 or not set(required) <= set(supplied.get('read_formats', [])):
        raise RuntimeError('Candidate app cannot read the persistent storage format')
    if DART_FORMAT in required:
        for name in RUNTIME_SCRIPTS:
            path = release / name
            if not path.is_file() or _is_link(path) or _is_link(path.parent):
                raise RuntimeError('Candidate app lacks the deployed DART replay reader')


def static_web_active():
    """Only a successfully activated, matching static deployment replaces Node."""
    current = (ROOT / 'current').resolve()
    marker = current / 'static-web-deployment.json'
    manifest = current / 'static-web/build-manifest.json'
    if not marker.is_file() or not manifest.is_file():
        return False
    state = json.loads(marker.read_text())
    build = json.loads(manifest.read_text())
    return (state.get('state') == 'active' and bool(state.get('release'))
            and state['release'] == build.get('release')
            and build.get('profile') == 'static')


def smoke_web():
    if static_web_active():
        # Pin our host's TLS endpoint to loopback: validate the existing public
        # vhost without depending on public DNS or a prospective edge route.
        base = ['curl', '--fail', '--silent', '--show-error', '--max-time', '12',
                '--resolve', f'{PUBLIC_HOST}:443:127.0.0.1']
        manifest = json.loads(subprocess.check_output(
            [*base, f'https://{PUBLIC_HOST}/build-manifest.json'], text=True))
        saved = json.loads(((ROOT / 'current').resolve() / 'static-web/build-manifest.json').read_text())
        if manifest.get('release') != saved.get('release') or manifest.get('profile') != 'static':
            raise RuntimeError('Static frontend build does not match the active manifest')
        run(*base, '--output', '/dev/null', f'https://{PUBLIC_HOST}/stocks?code=005930')
        return
    with urllib.request.urlopen('http://127.0.0.1:3211/stocks?code=005930', timeout=12) as page:
        if page.status != 200:
            raise RuntimeError('Existing web frontend smoke check failed')


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def container_state(service, allow_stopped=False):
    name = f'{PROJECT}-{service}-1'
    template = (
        '{{.Id}}|{{.State.Status}}|{{.Config.Image}}|'
        '{{index .Config.Labels "com.docker.compose.project"}}|'
        '{{index .Config.Labels "com.docker.compose.service"}}'
    )
    result = subprocess.check_output(['docker', 'inspect', '--format', template, name], text=True)
    identity, state, image, project, label_service = result.strip().split('|', 4)
    can_be_stopped = service == 'market-worker' or service == 'market-web' and static_web_active()
    allowed_states = ('running', 'exited') if allow_stopped and can_be_stopped else ('running',)
    if state not in allowed_states or project != PROJECT or label_service != service:
        raise RuntimeError(f'Unexpected ownership or state for {service}')
    return {'name': name, 'id': identity, 'image': image, 'state': state}


def external_container_id(name):
    return subprocess.check_output(['docker', 'inspect', '--format', '{{.Id}}', name], text=True).strip()


def restore_env(env, previous):
    temporary = env.with_suffix('.restore')
    temporary.write_bytes(previous)
    temporary.chmod(0o600)
    temporary.replace(env)


def safe_extract(archive_path, release, frontend=False):
    release = release.resolve()
    with tarfile.open(archive_path, 'r:gz') as archive:
        members = archive.getmembers()
        total = 0
        names = set()
        for member in members:
            path = PurePosixPath(member.name)
            name = path.as_posix()
            if (not member.isfile() or path.is_absolute() or '..' in path.parts
                    or '\\' in member.name or name in names):
                raise ValueError('Unsafe or duplicate patch member')
            allowed = (path.parts[:2] == ('services', 'market_dashboard') and len(path.parts) > 2
                or name in {'ops/market/audit_toss_coverage.py', 'ops/market/promote_backend_patch.py',
                            'ops/market/monitor.py', 'ops/market/backup.py',
                            'ops/market/retain_releases.py',
                            'ops/market/enable_financial_traces.py', *RUNTIME_OPERATIONS}
                or name in RUNTIME_SCRIPTS
                or frontend and (name == 'web-server.js'
                    or path.parts[0] in {'web-build', 'web-public'} and len(path.parts) > 1))
            if not allowed:
                raise ValueError('Patch may not change deployment configuration or shared data')
            total += member.size
            if total > 256 * 1024**2:
                raise ValueError('Application patch unexpectedly large')
            names.add(name)
        if not {'services/market_dashboard/source_support.py',
                'services/market_dashboard/toss_backfill.py',
                *RUNTIME_SCRIPTS, *RUNTIME_OPERATIONS} <= names:
            raise ValueError('Required backend source is missing')
        if frontend and (not {'web-build/BUILD_ID', 'web-server.js',
                'web-build/server/app/stocks/page.js'} <= names
                or not any(name.startswith('web-build/static/') and name.endswith('.js') for name in names)
                or not any(name.startswith('web-public/') for name in names)):
            raise ValueError('Required compiled frontend is missing from patch')
        for member in members:
            original = release / member.name
            target = original.resolve()
            if not target.is_relative_to(release):
                raise ValueError('Patch path escapes release directory')
            if any(_is_link(path) for path in (original, *original.parents)
                   if path == release or path.is_relative_to(release)):
                raise ValueError('Patch path contains an existing link')
        archive.extractall(release, filter='data')


def read_json(url):
    with urllib.request.urlopen(url, timeout=12) as response:
        if response.status != 200:
            raise RuntimeError('Application smoke check failed')
        return json.load(response)


def smoke(version):
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        try:
            health = read_json('http://127.0.0.1:8211/api/market/v1/health')
            if health.get('release') != version:
                time.sleep(2)
                continue
            search = read_json('http://127.0.0.1:8211/api/market/v1/search?q=005930')
            if not any(row.get('code') == '005930' for row in search.get('rows', [])):
                raise RuntimeError('Known security missing from search')
            detail = read_json('http://127.0.0.1:8211/api/market/v1/securities/005930?view=summary')
            if detail.get('security', {}).get('code') != '005930':
                raise RuntimeError('Security summary contract failed')
            smoke_web()
            for service in BACKEND:
                state = container_state(service)
                if state['image'] != f'korea-stocks-api:{version}':
                    raise RuntimeError(f'{service} did not activate the candidate image')
            return health
        except Exception:
            time.sleep(2)
    raise RuntimeError('Backend readiness deadline exceeded')


def promote(version, expected, archive_path, frontend=False):
    if any(not re.fullmatch(r'[A-Za-z0-9-]{5,80}', name) for name in (version, expected)):
        raise ValueError('Invalid release name')
    os.umask(0o077)
    with (ROOT / '.deploy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = (ROOT / 'current').resolve()
        if previous != ROOT / 'releases' / expected:
            raise RuntimeError('Active release changed')
        if shutil.disk_usage(ROOT).free < 512 * 1024**2:
            raise RuntimeError('Insufficient free disk space')
        before = {service: container_state(service, allow_stopped=True) for service in BACKEND}
        protected = {service: container_state(service, allow_stopped=service == 'market-web')
                     for service in ('market-web', 'openalgo')}
        data_services = {
            'postgres': external_container_id('hannun-public-market-market-db-1'),
            'redis': external_container_id('hannun-public-market-market-redis-1'),
        }
        if any(item['image'] != f'korea-stocks-api:{expected}' for item in before.values()):
            raise RuntimeError('Backend image does not match the active release')
        validate_storage_reader(previous)
        release = ROOT / 'releases' / version
        # Replacement means replacement: removed modules and stale compiled
        # assets must not survive an overlay of the previous source tree.
        omitted_static_snapshots = copy_release(previous, release, frontend)
        safe_extract(archive_path, release, frontend)
        validate_storage_reader(release)
        dockerfile = release / 'Dockerfile.backend-patch'
        dockerfile.write_text(
            f'FROM {before["market-api"]["image"]}\n'
            'USER root\n'
            'RUN rm -rf /app/services/market_dashboard\n'
            'COPY --chown=market:market services/market_dashboard /app/services/market_dashboard\n'
            'COPY --chown=market:market scripts/replay_dart_period_archives.py '
            '/app/scripts/replay_dart_period_archives.py\n'
            'COPY --chown=market:market ops/market/compact_raw_archives.py '
            '/app/ops/market/compact_raw_archives.py\n'
            'COPY --chown=market:market ops/market/review_samsung_comparative.py '
            '/app/ops/market/review_samsung_comparative.py\n'
            'USER market\n', encoding='utf-8')
        run('docker', 'build', '-q', '-t', f'korea-stocks-api:{version}',
            '-f', str(dockerfile), str(release))
        # A prior web-only patch can make the running frontend newer than the
        # backend release pointer. Preserve the images actually serving users;
        # tagging the pointer's older images would make a later full compose
        # reconcile silently roll them back.
        if frontend:
            if not (release/'web-build/BUILD_ID').is_file() or not (release/'web-server.js').is_file():
                raise RuntimeError('Compiled frontend is missing')
            dockerfile_web = release/'Dockerfile.web-patch'
            dockerfile_web.write_text(
                f'FROM {protected["market-web"]["image"]}\n'
                'USER root\n'
                'RUN rm -rf /app/apps/web/.next /app/apps/web/public\n'
                'COPY --chown=node:node web-build /app/apps/web/.next\n'
                'COPY --chown=node:node web-server.js /app/apps/web/server.js\n'
                'COPY --chown=node:node web-public /app/apps/web/public\n'
                'USER node\n', encoding='utf-8')
            run('docker', 'build', '-q', '-t', f'korea-stocks-web:{version}', '-f', str(dockerfile_web), str(release))
        else:
            run('docker', 'tag', protected['market-web']['image'], f'korea-stocks-web:{version}')
        run('docker', 'tag', protected['openalgo']['image'], f'korea-stocks-openalgo:{version}')

        env = ROOT / 'shared/secrets/release.env'
        old_env = env.read_bytes()
        old_text = old_env.decode()
        expected_line = 'RELEASE=' + expected
        if sum(line == expected_line for line in old_text.splitlines()) != 1:
            raise RuntimeError('Release environment does not match active version')
        new_text = '\n'.join('RELEASE=' + version if line == expected_line else line
                              for line in old_text.splitlines())
        if old_text.endswith('\n'):
            new_text += '\n'
        switched_env = False
        switched_pointer = False
        updated_services = (*BACKEND, 'market-web') if frontend else BACKEND
        compose = ['docker', 'compose', '--env-file', str(env), '-f',
                   str(release / 'ops/market/compose.desktop.yml')]
        try:
            temporary = env.with_suffix('.next')
            temporary.write_text(new_text, encoding='utf-8')
            temporary.chmod(0o600)
            temporary.replace(env)
            switched_env = True
            # --no-deps keeps Postgres, Redis, web, OpenAlgo and other host services untouched.
            run(*compose, 'up', '-d', '--no-deps', *updated_services)
            health = smoke(version)
            for service in BACKEND:
                if container_state(service)['image'] != f'korea-stocks-api:{version}':
                    raise RuntimeError(f'Unexpected candidate image for {service}')
            for service, old in protected.items():
                if service == 'market-web' and frontend:
                    if container_state(service)['image'] != f'korea-stocks-web:{version}':
                        raise RuntimeError('Frontend image did not activate')
                    continue
                if container_state(service, allow_stopped=service == 'market-web')['id'] != old['id']:
                    raise RuntimeError(f'Protected {service} container was restarted')
            for name, old_id in data_services.items():
                container = ('hannun-public-market-market-db-1' if name == 'postgres'
                             else 'hannun-public-market-market-redis-1')
                if external_container_id(container) != old_id:
                    raise RuntimeError(f'Persistent {name} container changed unexpectedly')
            pointer = ROOT / 'current'
            pending = ROOT / 'current.next'
            pending.unlink(missing_ok=True)
            pending.symlink_to(release, target_is_directory=True)
            pending.replace(pointer)
            switched_pointer = True
            report = {'release': version, 'previous': expected, 'state': 'active',
                      'updated_services': list(updated_services), 'health': health,
                      'omitted_static_snapshots': omitted_static_snapshots,
                      'prior_worker_state': before['market-worker']['state']}
            (release / 'deployment.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            print(json.dumps({'release': version, 'status': 'activated',
                              'updated_services': list(updated_services),
                              'postgres': 'unchanged', 'redis': 'unchanged',
                              'web': 'updated' if frontend else 'unchanged', 'openalgo': 'unchanged'}))
        except Exception:
            if switched_pointer:
                pending = ROOT / 'current.rollback'
                pending.unlink(missing_ok=True)
                pending.symlink_to(previous, target_is_directory=True)
                pending.replace(ROOT / 'current')
            if switched_env:
                restore_env(env, old_env)
                rollback = ['docker', 'compose', '--env-file', str(env), '-f',
                            str(previous / 'ops/market/compose.desktop.yml')]
                # expected-version tags may lag a separately deployed frontend.
                # Restore the image that was actually running before this change.
                run('docker', 'tag', protected['market-web']['image'], f'korea-stocks-web:{expected}')
                run(*rollback, 'up', '-d', '--no-deps', *updated_services)
            raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', required=True)
    parser.add_argument('--expected', required=True)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--frontend', action='store_true')
    args = parser.parse_args()
    promote(args.version, args.expected, args.archive, args.frontend)
