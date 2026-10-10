"""Pure fixture_non_production tests: no Docker, native PG or server access."""
import base64
import hashlib
import io
import json
import queue
import subprocess
from copy import deepcopy

import pytest

from ops.market.compact_restore import fenced_backup_host as m

NONCE = 'a' * 32


def config():
    raw = {name: {'body': '', 'sha256': hashlib.sha256(b'').hexdigest()} for name in m.ALLOWED}
    bundle = base64.b64encode(json.dumps(raw).encode()).decode()
    return {'bundle': bundle, 'bundle_sha256': hashlib.sha256(bundle.encode()).hexdigest(),
            'run_id': NONCE, 'max_seconds': 120}


def record():
    return {'format': 'market-backup-fence-v2', 'run_id': NONCE, 'release': m.RELEASE,
            'inner_launch_attempted': False,
            'worker': {'id': '1'*64, 'image': m.IMAGE, 'running': True,
                       'project': 'korea-stocks', 'service': 'market-worker'},
            'api': {'id': '2'*64, 'image': m.IMAGE, 'running': True,
                    'project': 'korea-stocks', 'service': 'market-api'}}


class FakeDocker:
    def __init__(self):
        self.values = record()
        self.commands = []
        self.state = 'gone'
        self.stop_timeout = False
        self.worker_changed = False
        self.identity = {'run_id': NONCE, 'pid': 123, 'starttime': '100'}
        self.hook = lambda: None

    def command(self, args, **kwargs):
        self.commands.append(args)
        if args[0] == 'stop':
            self.values['worker']['running'] = False
            if self.stop_timeout:
                raise subprocess.TimeoutExpired('fixture', 1)
        if args[0] == 'start':
            self.values['worker']['running'] = True
        return b''

    def container(self, name):
        value = deepcopy(self.values['api' if name == m.API else 'worker'])
        if name == m.WORKER and self.worker_changed:
            value['id'] = '3' * 64
        return value

    def probe(self, *args, terminate=False):
        self.hook()
        return {'state': self.state, 'database_sessions': 0 if self.state == 'gone' else 1,
                'identity': self.identity}

    def spawn(self, *args):
        return self.child


def make_fence(tmp_path, monkeypatch):
    docker = FakeDocker()
    history = []
    monkeypatch.setattr(m, 'write_marker', lambda path, value, **kwargs: history.append(
        deepcopy(value)))
    fence = m.Fence(docker, tmp_path/'fixture.json', record())
    return fence, docker, history


def test_bundle_is_stdin_and_never_large_argv(monkeypatch):
    cfg = config()
    calls = []
    child = type('Child', (), {'stdin': io.StringIO()})()
    monkeypatch.setattr(m.subprocess, 'Popen', lambda argv, **kwargs: calls.append(
        (argv, kwargs)) or child)
    m.Docker().spawn('2'*64, cfg, 100)
    assert cfg['bundle'] not in repr(calls[0][0])
    assert max(len(x) for x in calls[0][0]) < 16384
    sent = json.loads(child.stdin.getvalue())
    assert sent['bundle'] == cfg['bundle'] and sent['remaining_seconds'] == 100
    assert calls[0][0][:4] == ['docker', 'exec', '-i', '2'*64]


def test_inner_error_context_omits_sql_and_preserves_fixed_contract_reason():
    import ast

    tree = ast.parse(m.INNER)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == 'failure_details')
    scope = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<pure-failure>', 'exec'), scope)
    describe = scope['failure_details']
    sql_error = type('QueryCanceled', (Exception,), {'sqlstate': '57014'})('secret SQL values')
    assert describe(sql_error) == {'state': 'inner_failed', 'error_type': 'QueryCanceled',
                                   'sqlstate': '57014'}
    from ops.market.compact_restore.copy_stream import Refused

    reason = 'Invalid decoded trace row'
    assert describe(Refused(reason))['contract_reason'] == reason
    assert 'contract_reason' not in describe(ValueError('secret payload'))


def test_bad_bundle_or_path_refused():
    cfg = config()
    assert m.validate_bundle(cfg) is cfg
    cfg['bundle_sha256'] = '0'*64
    with pytest.raises(m.Refused):
        m.validate_bundle(cfg)
    cfg = config()
    raw = json.loads(base64.b64decode(cfg['bundle']))
    raw['../other'] = raw.pop(next(iter(raw)))
    cfg['bundle'] = base64.b64encode(json.dumps(raw).encode()).decode()
    cfg['bundle_sha256'] = hashlib.sha256(cfg['bundle'].encode()).hexdigest()
    with pytest.raises(m.Refused, match='bundle_paths'):
        m.validate_bundle(cfg)


def test_stop_timeout_restores_actual_same_stopped_worker(tmp_path, monkeypatch):
    fence, docker, history = make_fence(tmp_path, monkeypatch)
    docker.stop_timeout = True
    with pytest.raises(subprocess.TimeoutExpired):
        fence.run(config(), queue.Queue())
    assert docker.values['worker']['running']
    assert docker.commands == [['stop', '-t', '30', '1'*64], ['start', '1'*64]]
    assert history[0]['phase'] == 'stop_requested'
    assert history[-1]['phase'] == 'finished'


@pytest.mark.parametrize('state', ['alive', 'unknown', 'database_sessions_remain'])
def test_cli_exit_does_not_allow_restart_without_actual_inner_exit(tmp_path, monkeypatch, state):
    fence, docker, history = make_fence(tmp_path, monkeypatch)
    fence.record['inner_launch_attempted'] = True
    docker.values['worker']['running'] = False
    docker.state = state
    fence.child = type('Child', (), {'poll': lambda self: 0})()
    with pytest.raises(m.Refused, match='actual_inner_exit_not_proven'):
        fence.restore_worker()
    assert not docker.commands and not docker.values['worker']['running']
    assert history[-1]['phase'] == 'recovery_required'


def test_changed_worker_and_changed_inner_never_get_force_actions(tmp_path, monkeypatch):
    fence, docker, _ = make_fence(tmp_path, monkeypatch)
    docker.worker_changed = True
    with pytest.raises(m.Refused, match='worker_identity_changed'):
        fence.restore_worker()
    assert not docker.commands
    docker.worker_changed = False
    fence.record.update(inner_launch_attempted=True,
                        inner_identity={**docker.identity, 'starttime': 'older'})
    with pytest.raises(m.Refused, match='inner_identity_changed'):
        fence.actual_inner_gone()
    assert not docker.commands


def test_release_retains_worker_fence_until_explicit_finish(tmp_path, monkeypatch):
    fence, docker, history = make_fence(tmp_path, monkeypatch)
    identity = docker.identity
    stream = io.StringIO(''.join(json.dumps(v)+'\n' for v in (
        {'state': 'inner_started', 'identity': identity},
        {'state': 'snapshot_ready', 'snapshot': {'fixture_non_production': True}},
        {'state': 'snapshot_released', 'identity': identity})))

    class Child:
        stdout, stdin = stream, io.StringIO()

        def wait(self, **kwargs):
            return 0

    docker.child = Child()
    inputs = queue.Queue()
    inputs.put('{"command":"release"}\n')
    outputs = []

    def emit(value):
        outputs.append(value)
        if value['state'] == 'snapshot_released_fence_held':
            assert not docker.values['worker']['running']
            assert not any(row[0] == 'start' for row in docker.commands)
            inputs.put('{"command":"finish"}\n')

    monkeypatch.setattr(m, 'emit', emit)
    fence.run(config(), inputs)
    assert docker.values['worker']['running']
    assert any(row['phase'] == 'snapshot_released_fence_held' for row in history)
    assert outputs[-1]['continuous_fence_ended'] is False


def test_deadline_and_input_limit_are_bounded():
    lines = queue.Queue()
    with pytest.raises(m.Refused, match='absolute_deadline'):
        m.next_json(lines, m.time.monotonic()-1)
    m.reader(io.StringIO('x'*(m.LINE_MAX+1)), lines)
    with pytest.raises(m.Refused, match='input_limit'):
        m.next_json(lines, m.time.monotonic()+1)


def test_inner_deadline_covers_blocked_stdin_and_query_without_parent():
    # Compile the real fixed programs, but never run Linux /proc, SQL or Docker.
    compile(m.INNER, '<inner-fixture-inspection>', 'exec')
    compile(m.PROBE, '<probe-fixture-inspection>', 'exec')
    assert 'os._exit(124)' in m.INNER
    assert m.INNER.index('target=enforce') < m.INNER.index('sys.stdin.readline')
    assert 'statement_timeout=60000' in m.INNER
    assert "fields[19]==data['starttime']" in m.PROBE
    assert "MARKET_SNAPSHOT_INNER_V2" in m.PROBE
    assert 'pg_stat_activity' in m.PROBE


def test_default_cli_never_touches_host(monkeypatch, capsys):
    monkeypatch.setattr(m, 'Docker', lambda: pytest.fail('Docker instantiated'))
    assert m.main([]) == 0
    assert json.loads(capsys.readouterr().out)['executed'] is False
