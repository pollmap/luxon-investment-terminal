"""Deployment contracts without Docker, SSH, credentials, or live databases."""
import importlib.util
import io
import json
import os
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = {'services/market_dashboard/source_support.py': b'# new source',
           'services/market_dashboard/toss_backfill.py': b'# backfill',
           'scripts/replay_dart_period_archives.py': b'# archive reader',
           'ops/market/compact_raw_archives.py': b'# bounded compactor',
           'ops/market/review_samsung_comparative.py': b'# reviewed unit operator'}
OPERATIONS = {'ops/market/audit_toss_coverage.py': b'# audit',
              'ops/market/promote_backend_patch.py': b'# promotion',
              'ops/market/monitor.py': b'# static monitor',
              'ops/market/backup.py': b'# verified retention',
              'ops/market/retire_duplicate_backup.py': b'# exact verified duplicate retirement',
              'ops/market/retain_releases.py': b'# protected releases',
              'ops/market/compact_raw_archives.py': b'# bounded compactor',
              'ops/market/enable_financial_traces.py': b'# guarded financial writer',
              'ops/market/review_samsung_comparative.py': b'# reviewed unit operator'}
FRONTEND = {'web-build/BUILD_ID': b'new-build',
            'web-build/server/app/stocks/page.js': b'new page',
            'web-build/static/new.js': b'new chunk',
            'web-public/icon.svg': b'<svg/>', 'web-server.js': b'new server'}


@pytest.fixture
def modules(monkeypatch):
    # The production host uses fcntl; Windows tests simulate only its lock API.
    monkeypatch.setitem(sys.modules, 'fcntl', SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=lambda *a: None))
    loaded = []
    for name, path in [('promoter', 'ops/market/promote_backend_patch.py'),
                       ('packager', 'scripts/deploy-market-backend-patch.py')]:
        spec = importlib.util.spec_from_file_location(name, ROOT/path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        loaded.append(module)
    return loaded


def write_archive(path, files):
    with tarfile.open(path, 'w:gz') as archive:
        for name, value in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(value)
            archive.addfile(info, io.BytesIO(value))
    return path


def test_persistent_storage_reader_requirement_blocks_incompatible_app(modules, tmp_path, monkeypatch):
    promoter, _ = modules
    monkeypatch.setattr(promoter, 'ROOT', tmp_path)
    candidate = tmp_path/'releases/candidate'
    candidate.mkdir(parents=True)
    promoter.validate_storage_reader(candidate)
    marker = tmp_path/'shared/data/storage-format.json'
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({'version':1,'required_read_formats':['source-trace-sha256-v1']}))
    with pytest.raises(RuntimeError, match='lacks'):
        promoter.validate_storage_reader(candidate)
    capability = candidate/'services/market_dashboard/storage_capabilities.json'
    capability.parent.mkdir(parents=True)
    capability.write_text(json.dumps({'version':1,'read_formats':[]}))
    with pytest.raises(RuntimeError, match='cannot read'):
        promoter.validate_storage_reader(candidate)
    capability.write_text(json.dumps({'version':1,'read_formats':['source-trace-sha256-v1']}))
    promoter.validate_storage_reader(candidate)


def test_declared_storage_reader_matches_implementation():
    from services.market_dashboard.source_trace_store import FINANCIAL_FORMAT, FORMAT
    from services.market_dashboard.trading_projection import FORMAT as TRADING_FORMAT
    contract = json.loads((ROOT/'services/market_dashboard/storage_capabilities.json').read_text())
    assert contract == {'version':1, 'read_formats':[
        FORMAT, TRADING_FORMAT, FINANCIAL_FORMAT, 'raw-dart-gzip-v2']}


def test_financial_reference_contract_blocks_time_series_only_reader(modules, tmp_path, monkeypatch):
    promoter, _ = modules
    monkeypatch.setattr(promoter, 'ROOT', tmp_path)
    marker = tmp_path/'shared/data/storage-format.json'
    marker.parent.mkdir(parents=True)
    formats = ['source-trace-sha256-v1', 'financial-source-trace-sha256-v1']
    marker.write_text(json.dumps({'version': 1, 'required_read_formats': formats}))
    candidate = tmp_path/'releases/previous'
    capability = candidate/'services/market_dashboard/storage_capabilities.json'
    capability.parent.mkdir(parents=True)
    capability.write_text(json.dumps({'version': 1, 'read_formats': formats[:1]}))
    with pytest.raises(RuntimeError, match='cannot read'):
        promoter.validate_storage_reader(candidate)
    capability.write_text(json.dumps({'version': 1, 'read_formats': formats}))
    promoter.validate_storage_reader(candidate)


def test_trading_reference_contract_blocks_inline_only_rollback(modules, tmp_path, monkeypatch):
    promoter, _ = modules
    monkeypatch.setattr(promoter, 'ROOT', tmp_path)
    marker = tmp_path/'shared/data/storage-format.json'
    marker.parent.mkdir(parents=True)
    formats = ['source-trace-sha256-v1', 'toss-trading-reference-v1']
    marker.write_text(json.dumps({'version': 1, 'required_read_formats': formats}))
    candidate = tmp_path/'releases/previous'
    capability = candidate/'services/market_dashboard/storage_capabilities.json'
    capability.parent.mkdir(parents=True)
    capability.write_text(json.dumps({'version': 1, 'read_formats': formats[:1]}))
    with pytest.raises(RuntimeError, match='cannot read'):
        promoter.validate_storage_reader(candidate)
    capability.write_text(json.dumps({'version': 1, 'read_formats': formats}))
    promoter.validate_storage_reader(candidate)


@pytest.mark.parametrize('extra', [
    'ops/market/compose.desktop.yml', 'shared/secrets/release.env',
    'ops/market/enable_shared_traces.py', 'ops/market/monitor.py.bak',
    'ops/market/monitor.py/settings.json', 'ops/market/.env',
    '../data.sqlite', '/absolute-file', 'web-build/BUILD_ID',
    'services\\market_dashboard\\bad.py',
])
def test_archive_cannot_change_config_data_or_unrequested_web(modules, tmp_path, extra):
    promoter, _ = modules
    archive = write_archive(tmp_path/'patch.tar.gz', {**BACKEND, extra: b'forbidden'})
    release = tmp_path/'candidate'
    release.mkdir()
    with pytest.raises(ValueError):
        promoter.safe_extract(archive, release)
    assert list(release.iterdir()) == []


def test_archive_checks_canonical_duplicates_and_links(modules, tmp_path):
    promoter, _ = modules
    duplicate = {'services//market_dashboard/source_support.py': b'alias'}
    archive = write_archive(tmp_path/'duplicate.tar.gz', {**BACKEND, **duplicate})
    with pytest.raises(ValueError, match='duplicate'):
        promoter.safe_extract(archive, tmp_path/'candidate')
    with tarfile.open(tmp_path/'link.tar.gz', 'w:gz') as archive:
        info = tarfile.TarInfo('services/market_dashboard/link.py')
        info.type = tarfile.SYMTYPE
        info.linkname = '/shared/data'
        archive.addfile(info)
    with pytest.raises(ValueError, match='Unsafe'):
        promoter.safe_extract(tmp_path/'link.tar.gz', tmp_path/'candidate')


def test_existing_web_artifacts_cannot_complete_incomplete_patch(modules, tmp_path):
    promoter, _ = modules
    release = tmp_path/'candidate'
    for name, value in FRONTEND.items():
        target = release/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)
    archive = write_archive(tmp_path/'patch.tar.gz', BACKEND)
    with pytest.raises(ValueError, match='frontend is missing from patch'):
        promoter.safe_extract(archive, release, frontend=True)


def build_packaging_tree(root):
    files = {**BACKEND, **OPERATIONS,
             'ops/market/compose.desktop.yml': b'preserved configuration',
             'ops/market/enable_shared_traces.py': b'not part of an app patch',
             'ops/market/.env': b'fixture private settings',
             'shared/secrets/release.env': b'fixture private release',
             'services/market_dashboard/.env': b'not an application file',
             'apps/web/.next/BUILD_ID': b'fixture-build',
             'apps/web/.next/standalone/apps/web/server.js': b'server',
             'apps/web/.next/server/app/stocks/page.js': b'page',
             'apps/web/.next/static/chunks/main.js': b'chunk',
             'apps/web/.next/static/chunks/main.js.map': b'private map',
             'apps/web/.next/cache/large': b'cache',
             'apps/web/.next/dev/temporary.js': b'dev',
             'apps/web/.next/standalone/node_modules/unused.js': b'node_modules',
             'apps/web/public/icon.svg': b'<svg/>'}
    for name, value in files.items():
        path = root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)


@pytest.mark.parametrize('frontend', [False, True])
def test_package_delivers_exact_operational_sources_without_configuration(
        modules, tmp_path, monkeypatch, frontend):
    promoter, packager = modules
    source = tmp_path/'repo'
    build_packaging_tree(source)
    monkeypatch.setattr(packager, 'ROOT', source)
    archive_path = tmp_path/'patch.tar.gz'
    packager.package(archive_path, frontend=frontend)
    with tarfile.open(archive_path) as archive:
        names = set(archive.getnames())
        assert {name for name in names if name.startswith('ops/')} == set(OPERATIONS)
        assert not any(name.startswith('shared/') or name.endswith('/.env') for name in names)
        for name, content in OPERATIONS.items():
            assert archive.extractfile(name).read() == content
    candidate = tmp_path/'candidate'
    config = candidate/'ops/market/compose.desktop.yml'
    config.parent.mkdir(parents=True)
    config.write_bytes(b'existing deployment configuration')
    for name in OPERATIONS:
        (candidate/name).write_bytes(b'previous operations source')
    promoter.safe_extract(archive_path, candidate, frontend=frontend)
    assert config.read_bytes() == b'existing deployment configuration'
    for name, content in OPERATIONS.items():
        assert (candidate/name).read_bytes() == content


@pytest.mark.parametrize('name', ['monitor.py', 'backup.py', 'retain_releases.py'])
@pytest.mark.parametrize('condition', ['missing', 'linked'])
def test_required_operations_source_cannot_be_silently_omitted_or_linked(
        modules, tmp_path, monkeypatch, name, condition):
    _, packager = modules
    source = tmp_path/'repo'
    build_packaging_tree(source)
    monkeypatch.setattr(packager, 'ROOT', source)
    path = source/'ops/market'/name
    if condition == 'missing':
        path.unlink()
    else:
        # Windows does not always grant symlink creation; retain real file I/O
        # and model only the metadata result for this exact source path.
        original = Path.is_symlink
        monkeypatch.setattr(Path, 'is_symlink', lambda value: value == path or original(value))
    archive_path = tmp_path/'patch.tar.gz'
    archive_path.write_bytes(b'previous valid archive')
    with pytest.raises(RuntimeError, match='operations source'):
        packager.package(archive_path)
    assert archive_path.read_bytes() == b'previous valid archive'
    assert not archive_path.with_name(archive_path.name+'.partial').exists()


def test_web_package_matches_remote_contract_and_excludes_build_caches(modules, tmp_path, monkeypatch):
    promoter, packager = modules
    build_packaging_tree(tmp_path/'repo')
    monkeypatch.setattr(packager, 'ROOT', tmp_path/'repo')
    destination = tmp_path/'patch.tar.gz'
    digest = packager.package(destination, frontend=True)
    assert len(digest) == 64
    with tarfile.open(destination) as archive:
        names = archive.getnames()
    assert 'web-server.js' in names
    assert 'web-build/server/app/stocks/page.js' in names
    assert not any('/cache/' in name or '/standalone/' in name or '/dev/' in name
                   or name.endswith('.map') or name.endswith('/.env') for name in names)
    promoter.safe_extract(destination, tmp_path/'candidate', frontend=True)


def test_incomplete_local_build_does_not_publish_archive(modules, tmp_path, monkeypatch):
    _, packager = modules
    build_packaging_tree(tmp_path/'repo')
    (tmp_path/'repo/apps/web/.next/server/app/stocks/page.js').unlink()
    monkeypatch.setattr(packager, 'ROOT', tmp_path/'repo')
    destination = tmp_path/'patch.tar.gz'
    with pytest.raises(RuntimeError, match='build missing'):
        packager.package(destination, frontend=True)
    assert not destination.exists()
    assert not destination.with_name(destination.name+'.partial').exists()


def test_failed_packaging_keeps_existing_archive(modules, tmp_path, monkeypatch):
    _, packager = modules
    build_packaging_tree(tmp_path/'repo')
    monkeypatch.setattr(packager, 'ROOT', tmp_path/'repo')
    def fail(path, *args):
        path.write_bytes(b'incomplete')
        raise OSError('simulated disk full')
    monkeypatch.setattr(packager, '_write_package', fail)
    destination = tmp_path/'patch.tar.gz'
    destination.write_bytes(b'previous valid archive')
    with pytest.raises(OSError):
        packager.package(destination)
    assert destination.read_bytes() == b'previous valid archive'
    assert not destination.with_name(destination.name+'.partial').exists()


def test_build_switch_during_packaging_is_rejected(modules, tmp_path, monkeypatch):
    _, packager = modules
    build_packaging_tree(tmp_path/'repo')
    monkeypatch.setattr(packager, 'ROOT', tmp_path/'repo')
    write = packager._write_package
    def changing_build(destination, files, frontend, build):
        write(destination, files, frontend, build)
        (build/'BUILD_ID').write_text('different-build')
    monkeypatch.setattr(packager, '_write_package', changing_build)
    destination = tmp_path/'patch.tar.gz'
    with pytest.raises(RuntimeError, match='build changed'):
        packager.package(destination, frontend=True)
    assert not destination.exists()
    assert not destination.with_name(destination.name+'.partial').exists()


@pytest.mark.parametrize('service,expected', [('market-worker', True), ('market-api', False)])
def test_stopped_recovery_is_limited_to_owned_worker(modules, monkeypatch, service, expected):
    promoter, _ = modules
    monkeypatch.setattr(promoter.subprocess, 'check_output',
                        lambda *a, **kw: f'id|exited|image|korea-stocks|{service}')
    if expected:
        assert promoter.container_state(service, allow_stopped=True)['state'] == 'exited'
    else:
        with pytest.raises(RuntimeError, match='ownership or state'):
            promoter.container_state(service, allow_stopped=True)


def test_stopped_web_requires_verified_static_activation(modules, tmp_path, monkeypatch):
    promoter, _ = modules
    monkeypatch.setattr(promoter, 'ROOT', tmp_path)
    current = tmp_path / 'current'
    (current / 'static-web').mkdir(parents=True)
    monkeypatch.setattr(promoter.subprocess, 'check_output',
                        lambda *a, **kw: 'id|exited|image|korea-stocks|market-web')
    marker = current / 'static-web-deployment.json'
    manifest = current / 'static-web/build-manifest.json'
    manifest.write_text(json.dumps({'profile': 'static', 'release': 'v94'}))
    with pytest.raises(RuntimeError):
        promoter.container_state('market-web', allow_stopped=True)
    marker.write_text(json.dumps({'state': 'active', 'release': 'wrong'}))
    assert not promoter.static_web_active()
    marker.write_text(json.dumps({'state': 'active', 'release': 'v94'}))
    assert promoter.container_state('market-web', allow_stopped=True)['state'] == 'exited'
    with pytest.raises(RuntimeError):
        promoter.container_state('market-web')


def test_static_smoke_uses_tls_loopback_and_checks_build(modules, tmp_path, monkeypatch):
    promoter, _ = modules
    monkeypatch.setattr(promoter, 'ROOT', tmp_path)
    current = tmp_path / 'current'
    (current / 'static-web').mkdir(parents=True)
    build = {'profile': 'static', 'release': 'v94'}
    (current / 'static-web/build-manifest.json').write_text(json.dumps(build))
    (current / 'static-web-deployment.json').write_text(json.dumps({'state': 'active', 'release': 'v94'}))
    calls = []
    def read(command, **kwargs):
        calls.append(command)
        return json.dumps(build)
    monkeypatch.setattr(promoter.subprocess, 'check_output', read)
    monkeypatch.setattr(promoter, 'run', lambda *args: calls.append(args))
    promoter.smoke_web()
    assert len(calls) == 2
    assert all('--resolve' in call and f'{promoter.PUBLIC_HOST}:443:127.0.0.1' in call for call in calls)
    monkeypatch.setattr(promoter.subprocess, 'check_output', lambda *a, **kw: json.dumps({**build, 'release': 'old'}))
    with pytest.raises(RuntimeError, match='does not match'):
        promoter.smoke_web()


@pytest.fixture
def deployment(modules, tmp_path, monkeypatch):
    promoter, _ = modules
    root = tmp_path/'service'
    previous = root/'releases/previous-v88'
    for name, value in {**BACKEND, **FRONTEND,
                        'services/market_dashboard/removed.py': b'old module',
                        'web-build/static/obsolete.js': b'old asset',
                        'ops/market/compose.desktop.yml': b'unchanged compose'}.items():
        path = previous/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    env = root/'shared/secrets/release.env'
    env.parent.mkdir(parents=True)
    old_env = b'RELEASE=previous-v88\nDATABASE_URL=unchanged-test-database\n'
    env.write_bytes(old_env)
    data = root/'shared/data/retained.db'
    data.parent.mkdir(parents=True)
    data.write_bytes(b'existing database contents')
    (root/'current').write_text(str(previous))
    real_resolve = Path.resolve
    def resolve(path, *args, **kwargs):
        if path == root/'current':
            return real_resolve(Path(path.read_text()))
        return real_resolve(path, *args, **kwargs)
    def symlink(path, target, **kwargs):
        assert path in (root/'current.next', root/'current.rollback')
        path.write_text(str(target))
    # Windows cannot create symlinks without elevated rights. Simulate only the
    # deployment pointer, leaving all extraction and filesystem I/O real.
    monkeypatch.setattr(Path, 'resolve', resolve)
    monkeypatch.setattr(Path, 'symlink_to', symlink)
    monkeypatch.setattr(promoter, 'ROOT', root)
    monkeypatch.setattr(promoter.os, 'umask', lambda *a: None)
    monkeypatch.setattr(promoter.shutil, 'disk_usage', lambda *a: SimpleNamespace(free=1024**3))
    state = {service: {'id': 'old-'+service, 'image': 'korea-stocks-api:previous-v88',
                      'state': 'exited' if service == 'market-worker' else 'running'}
             for service in promoter.BACKEND}
    state.update({'market-web': {'id': 'old-web', 'image': 'korea-stocks-web:web-only-v88', 'state': 'running'},
                  'openalgo': {'id': 'old-openalgo', 'image': 'korea-stocks-openalgo:previous-v88', 'state': 'running'}})
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        if args[:2] == ('docker', 'compose'):
            version = next(line.split('=', 1)[1] for line in env.read_text().splitlines() if line.startswith('RELEASE='))
            services = args[args.index('--no-deps')+1:]
            assert set(services) <= {'market-api', 'market-worker', 'market-web'}
            for service in services:
                image = 'web' if service == 'market-web' else 'api'
                state[service].update(id=version+'-'+service, image=f'korea-stocks-{image}:{version}', state='running')
    monkeypatch.setattr(promoter, 'run', run)
    monkeypatch.setattr(promoter, 'container_state', lambda service, **kwargs: dict(state[service]))
    monkeypatch.setattr(promoter, 'external_container_id', lambda name: 'preserved-'+name)
    monkeypatch.setattr(promoter, 'smoke', lambda version: {'release': version})
    return SimpleNamespace(promoter=promoter, root=root, previous=previous, env=env, old_env=old_env,
                           data=data, state=state, calls=calls)


@pytest.mark.parametrize('frontend', [False, True])
def test_promotion_replaces_removed_sources_and_keeps_database(deployment, tmp_path, frontend):
    d = deployment
    archive = write_archive(tmp_path/'patch.tar.gz', {**BACKEND, **(FRONTEND if frontend else {})})
    d.promoter.promote('candidate-v89', 'previous-v88', archive, frontend=frontend)
    candidate = d.root/'releases/candidate-v89'
    assert (d.root/'current').resolve() == candidate
    assert not (candidate/'services/market_dashboard/removed.py').exists()
    assert (d.previous/'services/market_dashboard/removed.py').exists()
    assert d.state['market-worker']['state'] == 'running'
    assert d.state['openalgo']['id'] == 'old-openalgo'
    assert d.data.read_bytes() == b'existing database contents'
    assert 'DATABASE_URL=unchanged-test-database' in d.env.read_text()
    report = json.loads((candidate/'deployment.json').read_text())
    assert report['prior_worker_state'] == 'exited'
    if frontend:
        assert not (candidate/'web-build/static/obsolete.js').exists()
        dockerfile = (candidate/'Dockerfile.web-patch').read_text()
        assert 'RUN rm -rf /app/apps/web/.next /app/apps/web/public' in dockerfile
        assert dockerfile.endswith('USER node\n')
    else:
        assert d.state['market-web']['id'] == 'old-web'


@pytest.mark.parametrize('frontend', [False, True])
def test_failed_smoke_restores_release_environment_without_database_rollback(deployment, tmp_path, monkeypatch, frontend):
    d = deployment
    archive = write_archive(tmp_path/'patch.tar.gz', {**BACKEND, **(FRONTEND if frontend else {})})
    def fail(version):
        raise RuntimeError('candidate readiness failed')
    monkeypatch.setattr(d.promoter, 'smoke', fail)
    with pytest.raises(RuntimeError, match='candidate readiness failed'):
        d.promoter.promote('candidate-v89', 'previous-v88', archive, frontend=frontend)
    assert d.env.read_bytes() == d.old_env
    assert (d.root/'current').resolve() == d.previous
    assert d.data.read_bytes() == b'existing database contents'
    compose = [args for args in d.calls if args[:2] == ('docker', 'compose')]
    assert len(compose) == 2
    assert str(d.previous/'ops/market/compose.desktop.yml') in compose[-1]
    assert all('--no-deps' in args for args in compose)
    assert ('docker', 'tag', 'korea-stocks-web:web-only-v88', 'korea-stocks-web:previous-v88') in d.calls
    assert all(d.state[service]['image'] == 'korea-stocks-api:previous-v88' for service in d.promoter.BACKEND)


def quote_patch():
    return {**BACKEND,
            'services/market_dashboard/store.py': b'# compatible snapshot reader and writer',
            'services/market_dashboard/quote_store.py': b'# incremental quote contract'}


def test_first_quote_transition_failure_uses_verified_compatibility_image(
        deployment, tmp_path, monkeypatch):
    d = deployment
    archive = write_archive(tmp_path/'patch.tar.gz', quote_patch())
    original_run = d.promoter.run
    def run(*args, **kwargs):
        original_run(*args, **kwargs)
        if args[:2] == ('docker', 'compose'):
            overrides = [Path(args[index+1]) for index, value in enumerate(args[:-1])
                         if value == '-f' and args[index+1].endswith('compose.quote-rollback.json')]
            for override in overrides:
                config = json.loads(override.read_text())
                for service in d.promoter.BACKEND:
                    d.state[service]['image'] = config['services'][service]['image']
    monkeypatch.setattr(d.promoter, 'run', run)
    def fail(version):
        raise RuntimeError('candidate readiness failed')
    monkeypatch.setattr(d.promoter, 'smoke', fail)
    with pytest.raises(RuntimeError, match='candidate readiness failed'):
        d.promoter.promote('candidate-v131', 'previous-v88', archive)
    release = d.root/'releases/candidate-v131'
    image = 'korea-stocks-api:candidate-v131-quote-rollback'
    report = json.loads((release/'quote-rollback.json').read_text())
    assert report['image'] == image
    assert report['state'] == 'activated_after_candidate_failure'
    assert report['requires_override_for_manual_rollback']
    assert d.env.read_bytes() == d.old_env
    assert (d.root/'current').resolve() == d.previous
    assert d.data.read_bytes() == b'existing database contents'
    assert d.state['openalgo']['id'] == 'old-openalgo'
    assert d.state['market-web']['id'] == 'old-web'
    assert all(d.state[service]['image'] == image for service in d.promoter.BACKEND)
    assert not (d.previous/'services/market_dashboard/quote_store.py').exists()
    assert not any(args[:2] == ('docker', 'tag') and args[-1] ==
                   'korea-stocks-api:previous-v88' for args in d.calls)
    check = next(args for args in d.calls if args[:2] == ('docker', 'run'))
    assert '--read-only' in check and '--network' in check and 'none' in check
    assert not any(arg in ('-v', '--volume', '--env-file') for arg in check)
    compile(check[-1], '<rollback canary>', 'exec')
    first_compose = next(index for index, args in enumerate(d.calls)
                         if args[:2] == ('docker', 'compose'))
    assert d.calls.index(check) < first_compose
    dockerfile = (release/'Dockerfile.quote-rollback').read_text()
    assert dockerfile.startswith('FROM korea-stocks-api:previous-v88\n')
    assert dockerfile.count('COPY ') == 2 and 'store.py' in dockerfile


def test_failed_quote_compatibility_canary_does_not_start_candidate(
        deployment, tmp_path, monkeypatch):
    d = deployment
    archive = write_archive(tmp_path/'patch.tar.gz', quote_patch())
    original_run = d.promoter.run
    def fail_canary(*args, **kwargs):
        original_run(*args, **kwargs)
        if args[:2] == ('docker', 'run'):
            raise RuntimeError('compatibility canary failed')
    monkeypatch.setattr(d.promoter, 'run', fail_canary)
    with pytest.raises(RuntimeError, match='compatibility canary failed'):
        d.promoter.promote('candidate-v131', 'previous-v88', archive)
    assert d.env.read_bytes() == d.old_env
    assert (d.root/'current').resolve() == d.previous
    assert not any(args[:2] == ('docker', 'compose') for args in d.calls)
    assert d.data.read_bytes() == b'existing database contents'


def test_already_incremental_previous_release_needs_no_compatibility_layer(
        deployment, tmp_path):
    d = deployment
    (d.previous/'services/market_dashboard/quote_store.py').write_text('# existing reader')
    archive = write_archive(tmp_path/'patch.tar.gz', quote_patch())
    d.promoter.promote('candidate-v131', 'previous-v88', archive)
    assert not any(args[:2] == ('docker', 'run') for args in d.calls)
    assert not (d.root/'releases/candidate-v131/compose.quote-rollback.json').exists()


def test_failure_after_pointer_switch_restores_old_pointer(deployment, tmp_path, monkeypatch):
    d = deployment
    archive = write_archive(tmp_path/'patch.tar.gz', BACKEND)
    original_write = Path.write_text
    def fail_report(path, value, *args, **kwargs):
        if path == d.root/'releases/candidate-v89/deployment.json':
            raise OSError('simulated deployment report write failure')
        return original_write(path, value, *args, **kwargs)
    monkeypatch.setattr(Path, 'write_text', fail_report)
    with pytest.raises(OSError, match='report write failure'):
        d.promoter.promote('candidate-v89', 'previous-v88', archive)
    assert (d.root/'current').resolve() == d.previous
    assert d.env.read_bytes() == d.old_env
    assert d.data.read_bytes() == b'existing database contents'


def test_backend_promotion_does_not_clone_static_rollback_snapshots(deployment, tmp_path):
    d = deployment
    names = [f'static-web.previous-20261009T12000{i}123456Z' for i in range(7)]
    names.append('static-web.failed-20261009T120007123456Z')
    for name in names:
        asset = d.previous/name/'chunk.js'
        asset.parent.mkdir()
        asset.write_bytes(b'old static asset' * 8192)
    keep = {
        'static-web/build-manifest.json': b'{"release":"web-v104","profile":"static"}',
        'static-web/chunk.js': b'active static asset',
        'static-web.previous-manual/readme.txt': b'unrelated manual directory',
        'static-web.failed-20261009T120007123456Z.bak': b'unrelated file',
        'ops/static-web.previous-20261009T120000123456Z/data': b'nested unrelated directory',
        'data/retained.json': b'original data',
    }
    for name, content in keep.items():
        path = d.previous/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    archive = write_archive(tmp_path/'patch.tar.gz', BACKEND)
    d.promoter.promote('candidate-v89', 'previous-v88', archive)
    candidate = d.root/'releases/candidate-v89'
    assert not any((candidate/name).exists() for name in names)
    assert all((d.previous/name/'chunk.js').is_file() for name in names)
    for name, content in keep.items():
        assert (candidate/name).read_bytes() == content
    assert (candidate/'web-build/static/obsolete.js').read_bytes() == b'old asset'
    assert (candidate/'ops/market/compose.desktop.yml').read_bytes() == b'unchanged compose'
    report = json.loads((candidate/'deployment.json').read_text())
    assert report['omitted_static_snapshots'] == sorted(names)


SNAPSHOT = 'static-web.previous-20261009T120000123456Z'


def directory_link(link, target):
    if os.name == 'nt':
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.fixture
def release_copy(modules, tmp_path, monkeypatch):
    promoter, _ = modules
    previous = tmp_path/'service/releases/previous'
    previous.mkdir(parents=True)
    public = tmp_path/'public'
    (public/'releases').mkdir(parents=True)
    monkeypatch.setattr(promoter, 'PUBLIC_ROOT', public)
    old = previous/SNAPSHOT
    old.mkdir()
    (old/'index.html').write_bytes(b'old static content')
    return SimpleNamespace(promoter=promoter, previous=previous, public=public,
                           candidate=previous.with_name('candidate'), snapshot=old)


def activate_public(d, target=None):
    target = target or d.public/'releases/current-static'
    target.mkdir(parents=True, exist_ok=True)
    directory_link(d.public/'current', target)
    marker = d.previous/'static-web-deployment.json'
    marker.write_text(json.dumps({
        'state': 'active', 'release': 'web-v104', 'public_root': str(target)}))
    static = d.previous/'static-web'
    static.mkdir(exist_ok=True)
    (static/'build-manifest.json').write_text(json.dumps({
        'release': 'web-v104', 'profile': 'static'}))
    return target


def test_copy_saves_exact_snapshot_bytes_and_preserves_active_assets(release_copy):
    d = release_copy
    public = activate_public(d)
    (public/'index.html').write_bytes(b'public HTML')
    (d.previous/'static-web/index.html').write_bytes(b'current HTML')
    (d.previous/'web-server.js').write_bytes(b'Node fallback')
    for name in ('data/original.json', 'ops/market/compose.desktop.yml', 'ops/market/script.py'):
        path = d.previous/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'preserved')
    failed = d.previous/'static-web.failed-20261009T120010123456Z'
    failed.mkdir()
    (failed/'chunk.js').write_bytes(b'x' * 100000)
    def contents(root):
        return {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    before = contents(d.previous)
    excluded = d.promoter.copy_release(d.previous, d.candidate)
    after = contents(d.candidate)
    expected = {path: content for path, content in before.items() if path.parts[0] not in excluded}
    assert after == expected
    assert sum(map(len, before.values())) - sum(map(len, after.values())) == 100018
    assert contents(d.previous) == before
    assert (d.public/'current').resolve() == public
    assert (public/'index.html').read_bytes() == b'public HTML'


@pytest.mark.parametrize('name', [
    'static-web.previous-manual', 'static-web.previous-20261009T120000Z',
    'static-web.previous-20261309T120000123456Z', 'static-web.failed-20261009T120000123456Z.bak',
])
def test_snapshot_lookalike_directory_is_preserved(release_copy, name):
    d = release_copy
    (d.previous/name).mkdir()
    (d.previous/name/'readme').write_bytes(b'unrelated')
    d.promoter.copy_release(d.previous, d.candidate)
    assert (d.candidate/name/'readme').read_bytes() == b'unrelated'


def test_exact_snapshot_name_on_regular_file_is_preserved(release_copy):
    d = release_copy
    path = d.previous/'static-web.failed-20261009T120000123456Z'
    path.write_bytes(b'ordinary file, not a static directory')
    d.promoter.copy_release(d.previous, d.candidate)
    assert (d.candidate/path.name).read_bytes() == path.read_bytes()


@pytest.mark.parametrize('inside', [True, False])
def test_snapshot_link_is_rejected_without_following_it(release_copy, inside):
    d = release_copy
    target = d.previous/'ordinary' if inside else d.public/'outside'
    target.mkdir()
    (target/'original').write_bytes(b'do not touch')
    link = d.previous/'static-web.failed-20261009T120000123456Z'
    directory_link(link, target)
    with pytest.raises(RuntimeError, match='must not be a link'):
        d.promoter.copy_release(d.previous, d.candidate)
    assert not d.candidate.exists()
    assert (target/'original').read_bytes() == b'do not touch'


def test_actual_public_root_pointing_to_snapshot_blocks_copy(release_copy):
    d = release_copy
    activate_public(d, d.snapshot)
    with pytest.raises(RuntimeError, match='Active public root references'):
        d.promoter.copy_release(d.previous, d.candidate)
    assert not d.candidate.exists()
    assert (d.public/'current').resolve() == d.snapshot


def test_public_root_escape_blocks_copy(release_copy):
    d = release_copy
    activate_public(d, d.public/'outside-managed-releases')
    with pytest.raises(RuntimeError, match='escapes managed releases'):
        d.promoter.copy_release(d.previous, d.candidate)
    assert not d.candidate.exists()


@pytest.mark.parametrize('reference', ['static-web', 'data/linked-static'])
def test_retained_directory_reference_to_snapshot_blocks_copy(release_copy, reference):
    d = release_copy
    link = d.previous/reference
    link.parent.mkdir(parents=True, exist_ok=True)
    directory_link(link, d.snapshot)
    with pytest.raises(RuntimeError, match='Retained path references'):
        d.promoter.copy_release(d.previous, d.candidate)
    assert not d.candidate.exists()


@pytest.mark.parametrize('public', [True, False])
def test_active_or_retained_file_reference_to_snapshot_blocks_copy(
        release_copy, monkeypatch, public):
    d = release_copy
    # Keep actual file bytes/I/O. Model file-link metadata on Windows without
    # requiring symlink privileges, as the existing deployment tests do.
    root = activate_public(d) if public else d.previous
    link = root/'linked-chunk.js'
    link.write_bytes(b'unchanged')
    is_symlink, resolve = Path.is_symlink, Path.resolve
    monkeypatch.setattr(Path, 'is_symlink', lambda p: p == link or is_symlink(p))
    monkeypatch.setattr(Path, 'resolve', lambda p, *a, **kw:
                        d.snapshot/'index.html' if p == link else resolve(p, *a, **kw))
    with pytest.raises(RuntimeError, match='Retained path references'):
        d.promoter.copy_release(d.previous, d.candidate)
    assert not d.candidate.exists()
    assert link.read_bytes() == b'unchanged'


def test_public_asset_directory_escape_blocks_copy(release_copy):
    d = release_copy
    public = activate_public(d)
    outside = d.public/'private'
    outside.mkdir()
    directory_link(public/'assets', outside)
    with pytest.raises(RuntimeError, match='asset link escapes'):
        d.promoter.copy_release(d.previous, d.candidate)
    assert not d.candidate.exists()


@pytest.mark.parametrize('condition', ['missing', 'plain_directory', 'marker_mismatch'])
def test_ambiguous_active_public_root_blocks_copy(release_copy, condition):
    d = release_copy
    if condition == 'missing':
        (d.previous/'static-web-deployment.json').write_text('{"state":"active"}')
    elif condition == 'plain_directory':
        (d.public/'current').mkdir()
    else:
        activate_public(d)
        other = d.public/'releases/different'
        other.mkdir()
        (d.previous/'static-web-deployment.json').write_text(json.dumps({
            'state': 'active', 'public_root': str(other)}))
    with pytest.raises(RuntimeError, match='public static root'):
        d.promoter.copy_release(d.previous, d.candidate)
    assert not d.candidate.exists()


def test_candidate_path_cannot_write_through_link(release_copy):
    d = release_copy
    target = d.public/'untouched'
    target.mkdir()
    directory_link(d.candidate, target)
    with pytest.raises(RuntimeError, match='exists or is a link'):
        d.promoter.copy_release(d.previous, d.candidate)
    assert list(target.iterdir()) == []


def test_candidate_parent_cannot_escape_release_directory(release_copy):
    d = release_copy
    with pytest.raises(RuntimeError, match='Candidate release escapes'):
        d.promoter.copy_release(d.previous, d.public/'candidate')
    assert not (d.public/'candidate').exists()


def test_archive_extraction_cannot_write_through_retained_directory_link(modules, tmp_path):
    promoter, _ = modules
    release = tmp_path/'release'
    release.mkdir()
    outside = tmp_path/'outside'
    outside.mkdir()
    directory_link(release/'services', outside)
    archive = write_archive(tmp_path/'patch.tar.gz', BACKEND)
    with pytest.raises(ValueError, match='escapes release directory'):
        promoter.safe_extract(archive, release)
    assert list(outside.iterdir()) == []


def test_public_managed_parent_link_escape_blocks_copy(release_copy):
    d = release_copy
    # Move the empty fixture directory; do not remove an existing release.
    external = d.public/'elsewhere'
    (d.public/'releases').rename(external)
    directory_link(d.public/'releases', external)
    activate_public(d)
    with pytest.raises(RuntimeError, match='releases directory must not be a link'):
        d.promoter.copy_release(d.previous, d.candidate)
    assert not d.candidate.exists()


def test_public_asset_file_link_escape_blocks_copy(release_copy, monkeypatch):
    d = release_copy
    public = activate_public(d)
    link, external = public/'chunk.js', d.public/'private-file'
    link.write_bytes(b'public file')
    external.write_bytes(b'private file')
    is_symlink, resolve = Path.is_symlink, Path.resolve
    monkeypatch.setattr(Path, 'is_symlink', lambda p: p == link or is_symlink(p))
    monkeypatch.setattr(Path, 'resolve', lambda p, *a, **kw:
                        external if p == link else resolve(p, *a, **kw))
    with pytest.raises(RuntimeError, match='asset link escapes'):
        d.promoter.copy_release(d.previous, d.candidate)
    assert not d.candidate.exists()
    assert external.read_bytes() == b'private file'
