"""Candidate only: stdin bundle, fixed stock-service writer fence, no table writes.

Run on the reviewed Linux host only. No operation happens on import. release
ends the DB snapshot, NOT the deployment/worker fence. finish/abort ends that
fence. Recover is explicit after an outer SIGKILL/host failure; no unattended
recovery service is installed. stdout contains safe control receipts only.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path('/srv/services/korea-stocks')
API = 'korea-stocks-market-api-1'
WORKER = 'korea-stocks-market-worker-1'
IMAGE = 'sha256:7e822ca9712df263deb64e7317a119bf74a2e1edd87b185a8afe1fe00532c773'
RELEASE = '20261010-history-contract-v116'
LIMIT = 21600
LINE_MAX = 4 * 1024**2
ALLOWED = {'ops/__init__.py', 'ops/market/__init__.py',
           *{'ops/market/compact_restore/' + name for name in (
               '__init__.py', 'copy_stream.py', 'ordered_verify.py', 'source_snapshot.py',
               'checkpoint.py', 'restore_supervisor.py')}}


class Refused(RuntimeError):
    pass


def emit(value):
    print(json.dumps(value), flush=True)


def validate_bundle(config):
    if set(config) != {'bundle', 'bundle_sha256', 'run_id', 'max_seconds'}:
        raise Refused('config_keys')
    if (not re.fullmatch('[0-9a-f]{32}', config['run_id'])
            or type(config['max_seconds']) is not int
            or not 30 <= config['max_seconds'] <= LIMIT
            or not isinstance(config['bundle'], str) or len(config['bundle']) > LINE_MAX - 1024
            or hashlib.sha256(config['bundle'].encode()).hexdigest() != config['bundle_sha256']):
        raise Refused('config_binding')
    bundle = json.loads(base64.b64decode(config['bundle'], validate=True))
    if set(bundle) != ALLOWED:
        raise Refused('bundle_paths')
    for item in bundle.values():
        body = base64.b64decode(item['body'], validate=True)
        if hashlib.sha256(body).hexdigest() != item['sha256']:
            raise Refused('bundle_hash')
    return config


def reader(stream, target):
    try:
        while True:
            line = stream.readline(LINE_MAX + 1)
            if not line:
                break
            if len(line) > LINE_MAX or not line.endswith('\n'):
                target.put(Refused('input_limit'))
                return
            target.put(line)
    except Exception:
        target.put(Refused('input_read_failed'))
    finally:
        target.put(None)


def next_json(lines, deadline):
    try:
        item = lines.get(timeout=max(0, deadline - time.monotonic()))
    except queue.Empty:
        raise Refused('absolute_deadline') from None
    if item is None:
        raise Refused('control_eof')
    if isinstance(item, Exception):
        raise item
    value = json.loads(item)
    if not isinstance(value, dict):
        raise Refused('control_object_required')
    return value


def proc_start(path):
    return path.read_text().rsplit(')', 1)[1].split()[19]


def write_marker(path, value, *, first=False):
    """Durable recovery identity precedes stopping the worker. Never delete it."""
    if first and path.exists() or path.is_symlink():
        raise Refused('marker_exists_or_linked')
    pending = path.with_name(path.name + '.pending-' + os.urandom(6).hex())
    with pending.open('x', encoding='utf8') as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(pending, 0o600)
    os.replace(pending, path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


# argv contains only this small fixed program and the nonce, never the bundle.
# The watchdog exits THIS process even while blocked on stdin or a native query.
INNER = r'''
import base64,hashlib,json,os,pathlib,queue,signal,sys,tempfile,threading,time
MARKER='MARKET_SNAPSHOT_INNER_V2'
nonce=sys.argv[1]
start=time.monotonic()
deadline=start+20
done=threading.Event()
def enforce():
    while not done.wait(.1):
        if time.monotonic()>=deadline:os._exit(124)
threading.Thread(target=enforce,daemon=True).start()
signal.signal(signal.SIGTERM,lambda *_:os._exit(143))
identity={'run_id':nonce,'pid':os.getpid(),
          'starttime':pathlib.Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19]}
receipt=pathlib.Path('/tmp/market-snapshot-v2-'+nonce+'.json')
with receipt.open('x') as out:
    os.chmod(receipt,0o600);json.dump(identity,out);out.flush();os.fsync(out.fileno())
config=json.loads(sys.stdin.readline(4*1024**2+1))
if config['run_id']!=nonce or not 1<=config['remaining_seconds']<=21600:
    raise RuntimeError('Inner contract')
deadline=start+config['remaining_seconds']
if hashlib.sha256(config['bundle'].encode()).hexdigest()!=config['bundle_sha256']:
    raise RuntimeError('Bundle digest')
root=pathlib.Path(tempfile.mkdtemp(prefix='market_snapshot_'+nonce+'_'))
os.chmod(root,0o700)
allowed={'ops/__init__.py','ops/market/__init__.py',
 *{'ops/market/compact_restore/'+n for n in ('__init__.py','copy_stream.py',
 'ordered_verify.py','source_snapshot.py','checkpoint.py','restore_supervisor.py')}}
bundle=json.loads(base64.b64decode(config['bundle'],validate=True))
if set(bundle)!=allowed:raise RuntimeError('Bundle paths')
for name,item in bundle.items():
    body=base64.b64decode(item['body'],validate=True)
    if hashlib.sha256(body).hexdigest()!=item['sha256']:raise RuntimeError('Bundle hash')
    path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(body)
sys.path.insert(0,str(root))
from sqlalchemy import create_engine
from ops.market.compact_restore.source_snapshot import hold_snapshot,verify_exported
engine=create_engine(os.environ['MARKET_DATABASE_URL'],connect_args={
 'connect_timeout':5,'application_name':'market_snapshot_v2_'+nonce,
 'options':'-c statement_timeout=60000 -c lock_timeout=3000'})
handle=None
def check():
    if time.monotonic()>=deadline:raise RuntimeError('Inner absolute deadline')
def say(value):print(json.dumps(value),flush=True)
def failure_details(error):
    # SQL exceptions can include statements/values. Keep their class and SQLSTATE
    # only; our own Refused messages are fixed contract strings, never payloads.
    value={'state':'inner_failed','error_type':type(error).__name__}
    code=getattr(error,'sqlstate',None)
    if isinstance(code,str) and len(code)==5 and code.isalnum():value['sqlstate']=code
    if (type(error).__name__=='Refused'
            and type(error).__module__.startswith('ops.market.compact_restore.')):
        value['contract_reason']=str(error)[:200]
    return value
say({'state':'inner_started','identity':identity})
try:
    handle=engine.raw_connection();db=handle.driver_connection;db.autocommit=True
    with hold_snapshot(db,check=check,expected_database='market',expected_user='market') as snap:
        say({'state':'snapshot_ready','snapshot':snap})
        while True:
            check()
            line=sys.stdin.readline(1025)
            if not line or len(line)>1024:raise RuntimeError('Inner control ended')
            command=json.loads(line)
            if command=={'command':'verify'}:
                other=engine.raw_connection();r=other.driver_connection;r.autocommit=True
                try:result=verify_exported(r,snap,check=check)
                finally:other.close()
                say({'state':'source_verified','report':result})
            elif command in ({'command':'release'},{'command':'abort'}):break
            else:raise RuntimeError('Unknown command')
    handle.close();handle=None;engine.dispose()
    say({'state':'snapshot_released','identity':identity})
except Exception as error:
    say(failure_details(error))
    raise
finally:
    if handle is not None:handle.close()
    engine.dispose();done.set()
'''

# Read process identity and exact application_name DB sessions independently of
# the docker CLI. Terminate acts only on nonce+PID+starttime+cmdline agreement.
PROBE = r'''
import json,os,pathlib,signal,sys
nonce,action=sys.argv[1:]
p=pathlib.Path('/tmp/market-snapshot-v2-'+nonce+'.json')
if p.is_symlink() or not p.is_file():
    print(json.dumps({'state':'unknown'}));raise SystemExit(0)
data=json.loads(p.read_text())
if data.get('run_id')!=nonce or type(data.get('pid')) is not int or data['pid']<=1:
    raise RuntimeError('Identity receipt')
process=pathlib.Path('/proc')/str(data['pid'])
alive=False
try:
    fields=(process/'stat').read_text().rsplit(')',1)[1].split()
    cmd=(process/'cmdline').read_bytes()
    alive=fields[19]==data['starttime'] and fields[0]!='Z'
    if alive and (b'MARKET_SNAPSHOT_INNER_V2' not in cmd or nonce.encode() not in cmd):
        raise RuntimeError('Process identity differs')
except FileNotFoundError:pass
if alive and action=='terminate':
    # pidfd pins the checked process; PID reuse cannot redirect the signal.
    fd=os.pidfd_open(data['pid'])
    try:
        fields=(process/'stat').read_text().rsplit(')',1)[1].split()
        if fields[19]!=data['starttime']:raise RuntimeError('Process changed before signal')
        signal.pidfd_send_signal(fd,signal.SIGTERM)
    finally:os.close(fd)
if alive:
    print(json.dumps({'state':'alive','identity':data}));raise SystemExit(0)
from sqlalchemy import create_engine
engine=create_engine(os.environ['MARKET_DATABASE_URL'],connect_args={'connect_timeout':3,
 'options':'-c statement_timeout=3000','application_name':'market_snapshot_v2_probe'})
handle=engine.raw_connection();db=handle.driver_connection;db.autocommit=True
try:
    count=db.execute('SELECT count(*) FROM pg_stat_activity WHERE application_name=%s',
                    ('market_snapshot_v2_'+nonce,)).fetchone()[0]
finally:handle.close();engine.dispose()
print(json.dumps({'state':'gone' if count==0 else 'database_sessions_remain',
                  'identity':data,'database_sessions':count}))
'''


class Docker:
    def command(self, args, *, timeout=10):
        return subprocess.check_output(['docker', *args], timeout=timeout,
                                       stderr=subprocess.DEVNULL)

    def container(self, name):
        value = json.loads(self.command(['inspect', name]))[0]
        labels = value['Config'].get('Labels', {})
        return {'id': value['Id'], 'image': value['Image'], 'running': value['State']['Running'],
                'project': labels.get('com.docker.compose.project'),
                'service': labels.get('com.docker.compose.service')}

    def spawn(self, api_id, config, remaining):
        child = subprocess.Popen(['docker', 'exec', '-i', api_id, 'python', '-u', '-c',
                                  INNER, config['run_id']], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        # Only the authenticated stdin contract transports large bundle bytes.
        try:
            child.stdin.write(json.dumps({**config, 'remaining_seconds': int(remaining)}) + '\n')
            child.stdin.flush()
        except BaseException:
            child.terminate()  # Only this Popen child, never a container/process-name kill.
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3)
            raise
        return child

    def probe(self, api_id, nonce, *, terminate=False):
        return json.loads(self.command(['exec', api_id, 'python', '-c', PROBE, nonce,
                                        'terminate' if terminate else 'inspect'], timeout=10))


def same_service(actual, expected):
    return all(actual[key] == expected[key] for key in ('id', 'image', 'project', 'service'))


class Fence:
    def __init__(self, docker, marker, record, *, clock=time.monotonic, sleep=time.sleep):
        self.docker, self.marker, self.record = docker, marker, record
        self.clock, self.sleep = clock, sleep
        self.child = None

    def phase(self, name, **extra):
        self.record.update(phase=name, **extra)
        write_marker(self.marker, self.record)

    def stop_worker(self):
        # Stop-attempt is recorded BEFORE docker stop; timeout may mean it stopped.
        self.phase('stop_requested', stop_attempted=True)
        try:
            self.docker.command(['stop', '-t', '30', self.record['worker']['id']], timeout=45)
        except Exception:
            self.phase('stop_unconfirmed')
            raise
        current = self.docker.container(WORKER)
        if not same_service(current, self.record['worker']) or current['running']:
            raise Refused('worker_stop_not_confirmed')
        self.phase('worker_stopped')

    def actual_inner_gone(self, *, terminate=False):
        if not self.record.get('inner_launch_attempted'):
            return True
        current = self.docker.container(API)
        if not same_service(current, self.record['api']) or not current['running']:
            return False
        result = self.docker.probe(current['id'], self.record['run_id'], terminate=terminate)
        if result.get('identity'):
            if self.record.get('inner_identity') not in (None, result['identity']):
                raise Refused('inner_identity_changed')
            self.record['inner_identity'] = result['identity']
        return result.get('state') == 'gone' and result.get('database_sessions') == 0

    def end_inner(self, *, abort=False):
        if self.actual_inner_gone():
            return True
        if abort:
            self.actual_inner_gone(terminate=True)
        until = self.clock() + 15
        while self.clock() < until:
            if self.actual_inner_gone():
                return True
            self.sleep(.25)
        return False

    def restore_worker(self):
        if not self.actual_inner_gone():
            self.phase('recovery_required', continuous_fence_ended=False)
            raise Refused('actual_inner_exit_not_proven')
        current = self.docker.container(WORKER)
        if not same_service(current, self.record['worker']):
            raise Refused('worker_identity_changed')
        if not current['running']:
            try:
                self.docker.command(['start', current['id']], timeout=45)
            except Exception:
                # A CLI timeout is not the resulting container state.
                current = self.docker.container(WORKER)
                if not same_service(current, self.record['worker']) or not current['running']:
                    raise Refused('worker_restart_unconfirmed') from None
        current = self.docker.container(WORKER)
        if not same_service(current, self.record['worker']) or not current['running']:
            raise Refused('worker_restart_not_running')
        self.phase('finished', continuous_fence_ended=True, worker_restored=True)

    def cleanup_cli(self):
        # This only reaps our Popen child; it does NOT prove the inner exit.
        if self.child is None:
            return
        try:
            self.child.stdin.close()
        except Exception:
            pass
        try:
            self.child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.child.terminate()
            try:
                self.child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=3)

    def run(self, config, inputs):
        deadline = self.clock() + config['max_seconds']
        try:
            self.stop_worker()
            self.phase('inner_launch_requested', inner_launch_attempted=True)
            self.child = self.docker.spawn(self.record['api']['id'], config, deadline-self.clock())
            outputs = queue.Queue(maxsize=4)
            threading.Thread(target=reader, args=(self.child.stdout, outputs), daemon=True).start()
            hello = next_json(outputs, min(deadline, self.clock()+30))
            identity = hello.get('identity', {})
            if hello.get('state') != 'inner_started' or identity.get('run_id') != config['run_id']:
                raise Refused('inner_start_not_confirmed')
            self.phase('snapshot_starting', inner_identity=identity)
            ready = next_json(outputs, min(deadline, self.clock()+90))
            if ready.get('state') != 'snapshot_ready':
                if ready.get('state') == 'inner_failed':
                    self.phase('inner_failed', inner_failure=ready)
                raise Refused('snapshot_not_ready')
            self.phase('snapshot_ready')
            emit(ready)
            released = False
            while True:
                command = next_json(inputs, deadline)
                current = self.docker.container(WORKER)
                if not same_service(current, self.record['worker']) or current['running']:
                    raise Refused('external_worker_fence_changed')
                action = command.get('command')
                if command != {'command': action} or action not in (
                    'verify', 'release', 'finish', 'abort', 'status'
                ):
                    raise Refused('unsupported_command')
                if action == 'status':
                    emit({'state': self.record['phase'], 'continuous_fence_ended': False})
                elif action == 'abort':
                    if not self.end_inner(abort=True):
                        raise Refused('inner_abort_not_confirmed')
                    self.restore_worker()
                    return
                elif action == 'finish':
                    if not released:
                        raise Refused('release_required_before_finish')
                    self.restore_worker()
                    return
                elif released:
                    raise Refused('snapshot_already_released')
                else:
                    self.child.stdin.write(json.dumps(command)+'\n')
                    self.child.stdin.flush()
                    result = next_json(outputs, deadline)
                    if result.get('state') != (
                        'source_verified' if action == 'verify' else 'snapshot_released'
                    ):
                        if result.get('state') == 'inner_failed':
                            self.phase('inner_failed', inner_failure=result)
                        raise Refused('inner_reply_mismatch')
                    if action == 'release':
                        if not self.end_inner():
                            raise Refused('actual_snapshot_exit_not_proven')
                        released = True
                        self.phase('snapshot_released_fence_held')
                        emit({'state': 'snapshot_released_fence_held',
                              'continuous_fence_ended': False})
                    else:
                        emit(result)
        except BaseException:
            # Failure aborts the backup lease. Proven exit is required even when
            # the docker CLI already returned. Unknown exit keeps worker stopped.
            try:
                if self.end_inner(abort=True):
                    self.restore_worker()
                else:
                    self.phase('recovery_required', continuous_fence_ended=False)
            except Exception:
                self.phase('recovery_required', continuous_fence_ended=False)
            raise
        finally:
            self.cleanup_cli()


def check_record(record):
    if (record.get('format') != 'market-backup-fence-v2' or record.get('release') != RELEASE
            or not re.fullmatch('[0-9a-f]{32}', record.get('run_id', ''))):
        raise Refused('recovery_record_invalid')
    for key, service in (('api', 'market-api'), ('worker', 'market-worker')):
        value = record.get(key, {})
        if (not re.fullmatch('[0-9a-f]{64}', value.get('id', '')) or value.get('image') != IMAGE
                or value.get('project') != 'korea-stocks' or value.get('service') != service):
            raise Refused('recovery_service_invalid')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--recover', metavar='RUN_NONCE')
    args = parser.parse_args(argv)
    if not args.run:
        emit({'executed': False, 'production_ready': False})
        return 0
    import fcntl

    lockpath = ROOT / '.deploy.lock'
    if (lockpath.is_symlink() or ROOT.is_symlink()
            or (ROOT/'current').resolve() != ROOT/'releases'/RELEASE):
        raise Refused('deployment_identity_changed')
    with lockpath.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        directory = ROOT / 'shared/data/backup-fences'
        marker_parents = (directory, directory.parent, directory.parent.parent)
        if any(path.is_symlink() for path in marker_parents):
            raise Refused('linked_marker_directory')
        directory.mkdir(mode=0o700, exist_ok=True)
        docker = Docker()
        if args.recover:
            if not re.fullmatch('[0-9a-f]{32}', args.recover):
                raise Refused('recovery_nonce_invalid')
            path = directory / (args.recover + '.json')
            if path.is_symlink() or path.stat().st_size > 16384:
                raise Refused('recovery_marker_invalid')
            record = json.loads(path.read_text())
            check_record(record)
            fence = Fence(docker, path, record)
            if not fence.end_inner(abort=True):
                raise Refused('recovery_inner_not_gone')
            fence.restore_worker()
            emit({'state': 'worker_restored', 'continuous_fence_ended': True})
            return 0
        inputs = queue.Queue(maxsize=4)
        threading.Thread(target=reader, args=(sys.stdin, inputs), daemon=True).start()
        config = validate_bundle(next_json(inputs, time.monotonic()+30))
        api, worker = docker.container(API), docker.container(WORKER)
        record = {'format': 'market-backup-fence-v2', 'run_id': config['run_id'],
                  'release': RELEASE, 'api': api, 'worker': worker,
                  'guardian_pid': os.getpid(),
                  'guardian_starttime': proc_start(Path('/proc/self/stat')),
                  'started_at': time.time(), 'phase': 'before_stop',
                  'inner_launch_attempted': False,
                  'continuous_fence_ended': False, 'production_table_writes': False}
        check_record(record)
        if not api['running'] or not worker['running']:
            raise Refused('running_original_services_required')
        marker = directory / (config['run_id']+'.json')
        write_marker(marker, record, first=True)
        emit({'state': 'fence_started', 'recovery_marker': str(marker), 'identity': record})
        fence = Fence(docker, marker, record)
        # Linux alarm also interrupts blocked stdin writes / docker spawn paths.
        # Cleanup remains independently bounded and may exceed this work limit.
        previous = signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(
            Refused('host_absolute_deadline')))
        signal.setitimer(signal.ITIMER_REAL, config['max_seconds'])
        try:
            fence.run(config, inputs)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
        emit({'state': 'worker_restored', 'continuous_fence_ended': True})
    return 0


if __name__ == '__main__':
    def interrupted(*_):
        raise Refused('host_interrupted')
    for name in ('SIGTERM', 'SIGINT', 'SIGHUP'):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), interrupted)
    try:
        raise SystemExit(main())
    except Exception as error:
        emit({'state': 'failed', 'error_type': type(error).__name__, 'production_migration': False})
        raise SystemExit(1) from None
