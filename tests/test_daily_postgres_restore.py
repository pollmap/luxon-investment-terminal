import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import verify_daily_postgres_restore as restore


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    root=tmp_path/'repo'
    backups=root/'work/backups/daily'
    backups.mkdir(parents=True)
    pg=root/'pg';pg.mkdir()
    for name in ('initdb','pg_ctl','pg_restore'):
        (pg/(name+'.exe')).write_bytes(b'fixture')
    dump=backups/'market-20261009T153042Z.dump'
    dump.write_bytes(b'PGDMPfixture_non_production')
    out=root/'work/restore-daily-20261010-test'
    monkeypatch.setattr(restore,'ROOT',root)
    monkeypatch.setattr(restore,'PG',pg)
    monkeypatch.setattr(restore,'BACKUPS',backups)
    monkeypatch.setattr(restore.shutil,'disk_usage',lambda _:SimpleNamespace(free=60*1024**3))
    digest=hashlib.sha256(dump.read_bytes()).hexdigest()
    return dump,out,digest


def test_preflight_preserves_existing_output_and_rejects_wrong_hash(inputs):
    dump,out,digest=inputs
    with pytest.raises(ValueError,match='SHA256 mismatch'):
        restore.validated_paths(dump,out,'0'*64)
    assert not out.exists()
    out.mkdir();(out/'keep').write_text('original')
    with pytest.raises(ValueError,match='new isolated'):
        restore.validated_paths(dump,out,digest)
    assert (out/'keep').read_text()=='original'


def test_preflight_rejects_unsupported_paths_and_low_space(inputs,monkeypatch):
    dump,out,digest=inputs
    with pytest.raises(ValueError,match='new isolated'):
        restore.validated_paths(dump,out.parent/'unrelated',digest)
    outside=out.parent/'market-20261009T153042Z.dump';outside.write_bytes(dump.read_bytes())
    with pytest.raises(ValueError,match='complete daily backup'):
        restore.validated_paths(outside,out,digest)
    monkeypatch.setattr(restore.shutil,'disk_usage',lambda _:SimpleNamespace(free=44*1024**3))
    with pytest.raises(ValueError,match='45 GiB'):
        restore.validated_paths(dump,out,digest)
    assert not out.exists()


@pytest.mark.parametrize('failure',[None,'restore','inspect','budget'])
def test_new_local_restore_always_stops_and_retains_cluster(inputs,monkeypatch,failure):
    dump,out,digest=inputs
    monkeypatch.setattr(restore,'validated_paths',lambda *a:(dump,out))
    calls=[];state={'running':False}

    def run(args,**kwargs):
        calls.append(args)
        if args[-1]=='start': state['running']=True
        if args[-1]=='stop': state['running']=False
        code=(0 if state['running'] else 3) if args[-1]=='status' else 0
        return SimpleNamespace(returncode=code)

    class Process:
        pid=777
        returncode=None if failure=='budget' else (1 if failure=='restore' else 0)
        def __init__(self,args,**kwargs): calls.append(args)
        def poll(self): return self.returncode
        def terminate(self): self.returncode=1
        def wait(self,timeout): return self.returncode

    class Connection:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def close(self): pass
        def execute(self,query):
            assert 'DROP' not in query
            return SimpleNamespace(fetchone=lambda:{'path':str(out/'cluster-data'),'version':170011,'port':restore.PORT})

    def inspect(_):
        if failure=='inspect': raise RuntimeError('fixture error')
        return {'passed':True}

    monkeypatch.setattr(restore.subprocess,'run',run)
    monkeypatch.setattr(restore.subprocess,'Popen',Process)
    monkeypatch.setattr(restore.psycopg,'connect',lambda **kw:Connection())
    monkeypatch.setattr(restore,'inspect_database',inspect)
    if failure=='budget':
        monkeypatch.setattr(restore.shutil,'disk_usage',lambda _:SimpleNamespace(free=5*1024**3))
    report=restore.verify(dump,out,digest)
    assert report['passed']==(failure is None)
    assert report['local_cluster_stopped'] and not state['running']
    assert report['production_contacted'] is False
    assert out.is_dir() and dump.read_bytes()==b'PGDMPfixture_non_production'
    commands=[c for c in calls if Path(c[0]).name=='pg_restore.exe']
    assert len(commands)==1
    assert '--clean' not in commands[0] and '--create' not in commands[0]
    assert commands[0][commands[0].index('--host')+1]=='127.0.0.1'
    assert any(c[-1]=='stop' for c in calls)
