from contextlib import nullcontext
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile

import pytest

SPEC = importlib.util.spec_from_file_location('promote_static_web', Path(__file__).parents[1] / 'ops/market/promote_static_web.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)

CONFIG = '''upstream api { server 127.0.0.1:8211; }
server {
 listen 80;
 server_name stocks.62-171-141-206.sslip.io;
 location / { return 301 https://stocks.62-171-141-206.sslip.io$request_uri; }
}
server {
 listen 443 ssl http2;
 server_name stocks.62-171-141-206.sslip.io;
 ssl_certificate /etc/letsencrypt/live/stocks-dashboard/fullchain.pem;
 location = / { return 302 /stocks; }
 location ^~ /api/market/v1/ { proxy_cache read_cache; proxy_pass http://api; }
 location = /api/market/v1/stream { proxy_pass http://127.0.0.1:8211; proxy_set_header Upgrade $http_upgrade; }
 location ~ ^/(stocks(?:/|$)|_next/|api/market(?:/|-)|brand/jusikdam-symbol\\.png$|icon\\.(?:svg|png)$) {
  proxy_pass http://127.0.0.1:3211;
  proxy_read_timeout 45s;
 }
 location / { return 404; }
}
server { listen 8080; location / { return 200 unrelated; } }
'''


def archive(path, release='v94-fixture', extra=None):
    files = {name: b'fixture_non_production' for name in ['stocks.html', '404.html', 'legacy.html',
        'legacy-entry.mjs', 'legacy-routes.mjs', 'stocks/sw.js', 'stocks/manifest.webmanifest', '_next/static/test.js']}
    manifest = {'profile': 'static', 'release': release, 'files': [{'path': name, 'bytes': len(data),
        'sha256': hashlib.sha256(data).hexdigest()} for name, data in files.items()],
        'bytes': sum(len(data) for data in files.values())}
    files['build-manifest.json'] = json.dumps(manifest).encode()
    if extra:
        files.update(extra)
    with tarfile.open(path, 'w:gz') as bundle:
        for name, data in files.items():
            member = tarfile.TarInfo('./' + name)
            member.size = len(data)
            bundle.addfile(member, io.BytesIO(data))
    return m.sha256(path)


def test_transform_preserves_api_ws_tls_and_other_servers():
    transformed = m.rewrite_nginx(CONFIG, Path('/srv/services/korea-stocks/current/static-web'))
    for line in CONFIG.splitlines():
        if any(token in line for token in ['proxy_cache read_cache', '/api/market/v1/stream', 'ssl_certificate', 'unrelated', 'listen 80;', 'return 301']):
            assert line in transformed
    assert 'proxy_pass http://127.0.0.1:3211;' not in transformed
    assert m.MARKER in transformed
    assert '/company/' in transformed and 'legacy.html' in transformed
    assert m.rewrite_nginx(transformed, Path('/srv/services/korea-stocks/current/static-web')) == transformed


def test_existing_static_config_gets_version_cache_header_only():
    root = Path('/var/www/korea-stocks/current')
    expected = m.rewrite_nginx(CONFIG, root)
    line = (
        ' location = /stocks/version.json { try_files $uri =404; '
        'add_header Cache-Control no-store always; }\n'
    )
    legacy = expected.replace(line, '')
    assert legacy != expected
    updated = m.rewrite_nginx(legacy, root)
    assert updated.count('/stocks/version.json') == 1
    assert updated.replace(line, '') == legacy
    assert m.rewrite_nginx(updated, root) == updated
    assert m.rewrite_nginx(expected, root) == expected
    with pytest.raises(ValueError, match='Unexpected release version'):
        m.rewrite_nginx(expected.replace('no-store always', 'max-age=3600'), root)


def test_existing_static_markers_must_be_unique_and_ordered():
    root = Path('/var/www/korea-stocks/current')
    expected = m.rewrite_nginx(CONFIG, root)
    with pytest.raises(ValueError, match='Ambiguous'):
        m.rewrite_nginx(expected + '\n' + m.MARKER, root)


@pytest.mark.parametrize('name', ['../x', '/etc/passwd', 'a/../../x', 'a\\..\\x', 'C:/x', '.env', './.git/config'])
def test_rejects_unsafe_member_names(name):
    with pytest.raises(ValueError):
        m.member_name(name)


def test_archive_checksum_manifest_and_extra_members(tmp_path):
    bundle = tmp_path / 'static.tar.gz'
    digest = archive(bundle)
    assert len(m.inspect_archive(bundle, 'v94-fixture', digest)['files']) == 8
    with pytest.raises(ValueError, match='checksum'):
        m.inspect_archive(bundle, 'v94-fixture', '0' * 64)
    with pytest.raises(ValueError, match='active release'):
        m.inspect_archive(bundle, 'another-release', digest)
    digest = archive(bundle, extra={'unexpected.txt': b'extra'})
    with pytest.raises(ValueError, match='file list'):
        m.inspect_archive(bundle, 'v94-fixture', digest)


def test_archive_rejects_symlinks_before_writing(tmp_path):
    bundle = tmp_path / 'symlink.tar.gz'
    with tarfile.open(bundle, 'w:gz') as target:
        item = tarfile.TarInfo('escape')
        item.type = tarfile.SYMTYPE
        item.linkname = '/etc'
        target.addfile(item)
    with pytest.raises(ValueError, match='Link'):
        m.inspect_archive(bundle, 'v94-fixture', m.sha256(bundle))


@pytest.mark.parametrize('change', ['3211', 'location = /', 'server_name stocks.62-171-141-206.sslip.io;'])
def test_unexpected_config_is_not_rewritten(change):
    with pytest.raises(ValueError):
        m.rewrite_nginx(CONFIG.replace(change, 'unexpected'), Path('/srv/services/korea-stocks/current/static-web'))


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    root = tmp_path / 'service'
    release = root / 'releases/v94-fixture'
    release.mkdir(parents=True)
    if os.name == 'nt':
        import _winapi
        _winapi.CreateJunction(str(release), str(root / 'current'))
    else:
        (root / 'current').symlink_to(release, target_is_directory=True)
    (root / 'incoming').mkdir()
    bundle = root / 'incoming/static.tar.gz'
    digest = archive(bundle)
    config = tmp_path / 'hannun'
    config.write_text(CONFIG)
    calls = []
    monkeypatch.setattr(m, 'ROOT', root)
    monkeypatch.setattr(m, 'PUBLIC_ROOT', tmp_path / 'public')
    monkeypatch.setattr(m, 'NGINX', config)
    monkeypatch.setattr(m, 'deployment_lock', nullcontext)
    monkeypatch.setattr(m, 'web_state', lambda: {'id': 'owned-fixture', 'state': 'running'})
    monkeypatch.setattr(m, 'run', lambda *args: calls.append(args))
    monkeypatch.setattr(m, 'smoke', lambda manifest: None)
    # Production uses atomic POSIX symlinks. Windows fixtures use a pointer file
    # while archive/config/container rollback stays exercised unchanged.
    def pointer(target, token):
        m.PUBLIC_ROOT.mkdir(exist_ok=True)
        (m.PUBLIC_ROOT / 'current').write_text(str(target))
    if os.name == 'nt':
        monkeypatch.setattr(m, 'switch_public', pointer)
    return root, release, bundle, digest, config, calls


def test_dry_run_does_not_write_or_stop(deployment):
    root, release, bundle, digest, config, calls = deployment
    result = m.promote('v94-fixture', bundle, digest, dry_run=True)
    assert result['status'] == 'dry-run'
    assert config.read_text() == CONFIG
    assert not list(release.iterdir()) and not calls


def test_success_stops_only_owned_web_and_writes_marker(deployment):
    root, release, bundle, digest, config, calls = deployment
    result = m.promote('v94-fixture', bundle, digest)
    assert result['status'] == 'promoted'
    assert json.loads((release / 'static-web-deployment.json').read_text())['state'] == 'active'
    assert ('docker', 'stop', '--time', '20', m.WEB) in calls
    assert not any('rm' in call or 'restart' in call for call in calls)
    assert Path(result['nginx_backup']).read_text() == CONFIG


def test_nginx_failure_restores_config_without_stopping_web(deployment, monkeypatch):
    root, release, bundle, digest, config, calls = deployment
    failures = [True]
    def command(*args):
        calls.append(args)
        if args == ('nginx', '-t') and failures:
            failures.pop()
            raise RuntimeError('fixture failure')
    monkeypatch.setattr(m, 'run', command)
    with pytest.raises(RuntimeError, match='prior Nginx/web state restored'):
        m.promote('v94-fixture', bundle, digest)
    assert config.read_text() == CONFIG
    assert not (release / 'static-web-deployment.json').exists()
    assert not any(call[:2] == ('docker', 'stop') for call in calls)


def test_post_stop_failure_restarts_web_and_restores_prior_static(deployment, monkeypatch):
    root, release, bundle, digest, config, calls = deployment
    old = release / 'static-web'
    old.mkdir()
    (old / 'old.html').write_text('prior static')
    marker = release / 'static-web-deployment.json'
    marker.write_text('{"state":"active","release":"previous"}')
    checks = []
    def smoke(manifest):
        checks.append(True)
        if len(checks) == 2:
            raise RuntimeError('fixture after stop')
    monkeypatch.setattr(m, 'smoke', smoke)
    with pytest.raises(RuntimeError, match='prior Nginx/web state restored'):
        m.promote('v94-fixture', bundle, digest)
    assert ('docker', 'start', m.WEB) in calls
    assert config.read_text() == CONFIG
    assert (old / 'old.html').read_text() == 'prior static'
    assert json.loads(marker.read_text())['release'] == 'previous'


def test_wrong_release_rejected_before_configuration_change(deployment):
    root, release, bundle, digest, config, calls = deployment
    with pytest.raises(RuntimeError, match='Active release'):
        m.promote('v95-different', bundle, digest)
    assert config.read_text() == CONFIG and not calls


def readiness_fixture(monkeypatch):
    bodies = {'stocks?code=005930': b'approved-html', '_next/static/test.js': b'approved-js'}
    manifest = {'release': 'fixture-ready', 'files': [
        {'path': 'stocks.html' if route.startswith('stocks?') else route, 'bytes': len(body),
         'sha256': hashlib.sha256(body).hexdigest()} for route, body in bodies.items()]}
    bodies['build-manifest.json'] = json.dumps(manifest).encode()
    clock = [0.0]
    monkeypatch.setattr(m.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(m.time, 'sleep', lambda duration: clock.__setitem__(0, clock[0] + duration))
    def probe(route, loopback, remaining, body=None):
        data = bodies[route] if body is None else body
        return data, {'route': route, 'via': 'loopback' if loopback else 'public', 'status': 200,
                      'actual_bytes': len(data), 'actual_sha256': hashlib.sha256(data).hexdigest()}
    return manifest, probe, clock


def test_readiness_retries_old_nginx_worker_until_all_bytes_match(monkeypatch):
    manifest, ready, clock = readiness_fixture(monkeypatch)
    calls = []
    def probe(route, loopback, remaining):
        calls.append((route, loopback, remaining))
        return ready(route, loopback, remaining, body=b'old-worker-html' if len(calls) == 1 else None)
    monkeypatch.setattr(m, 'static_probe', probe)
    result = m.smoke(manifest, timeout=2, interval=.25)
    assert result['attempts'] == 2 and len(result['checks']) == 6
    assert result['initial_mismatch']['reason'] == 'content_mismatch'
    assert result['initial_mismatch']['expected_bytes'] == len(b'approved-html')
    assert clock[0] == .25
    assert {info['via'] for info in result['checks']} == {'loopback', 'public'}


def test_readiness_persistent_mismatch_is_bounded_and_reports_hash_not_body(monkeypatch):
    manifest, ready, clock = readiness_fixture(monkeypatch)
    monkeypatch.setattr(m, 'static_probe', lambda route, local, remaining: ready(route, local, remaining, body=b'sensitive_fixture_content'))
    with pytest.raises(RuntimeError, match='readiness deadline exceeded') as failure:
        m.smoke(manifest, timeout=1, interval=.25)
    assert clock[0] == 1
    message = str(failure.value)
    assert 'sensitive_fixture_content' not in message
    report = json.loads(message.split(': ', 1)[1])
    assert report['attempts'] == 4
    assert report['last_mismatch']['route'] == 'stocks?code=005930'
    assert report['last_mismatch']['via'] == 'loopback'
    assert report['last_mismatch']['actual_bytes'] == len(b'sensitive_fixture_content')
    assert report['last_mismatch']['expected_sha256'] == manifest['files'][0]['sha256']


def test_readiness_requires_public_path_and_matching_manifest(monkeypatch):
    manifest, ready, clock = readiness_fixture(monkeypatch)
    def probe(route, local, remaining):
        return ready(route, local, remaining, body=b'{"release":"old","files":[]}' if route == 'build-manifest.json' and not local else None)
    monkeypatch.setattr(m, 'static_probe', probe)
    with pytest.raises(RuntimeError) as failure:
        m.smoke(manifest, timeout=1, interval=.25)
    report = json.loads(str(failure.value).split(': ', 1)[1])
    assert report['last_mismatch']['via'] == 'public'
    assert report['last_mismatch']['reason'] == 'manifest_mismatch'
    assert clock[0] == 1


def test_probe_parses_compressed_response_metadata_without_logging_body(monkeypatch):
    calls = []
    def command(*args):
        calls.append(args)
        return b'HTTP/1.1 200 Connection established\r\n\r\nHTTP/2 200\r\ncontent-type: text/html\r\ncontent-encoding: gzip\r\nage: 2\r\netag: "fixture"\r\n\r\nhtml-body\n__STATIC_SMOKE__200\n'
    monkeypatch.setattr(m, 'run', command)
    body, info = m.static_probe('stocks?code=005930', True, .75)
    assert body == b'html-body'
    assert info['status'] == 200 and info['actual_bytes'] == 9
    assert info['age'] == '2' and info['content-encoding'] == 'gzip'
    assert calls[0][calls[0].index('--max-time') + 1] == '0.75'
    assert '--resolve' in calls[0]
