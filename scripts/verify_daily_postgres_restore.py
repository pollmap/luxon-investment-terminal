"""Restore a hash-verified local daily dump into a new, isolated PG17 cluster.

No production connection, deletion, existing-cluster access or archive rewrite.
The completed/failed cluster is retained and always stopped. Full row counts and
index checks prove restore usability, not equality with a changing live database.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import time

import psycopg
from psycopg.rows import dict_row

from scripts.verify_postgres_restore import hidden_options, inspect_database

ROOT = Path(__file__).resolve().parents[1]
PG = ROOT / 'work/tools/postgresql-restore/pgsql/bin'
BACKUPS = ROOT / 'work/backups/daily'
HOST, PORT, USER, DATABASE = '127.0.0.1', 54420, 'daily_restore', 'stocks_restore'
MIN_FREE = 45 * 1024**3
STOP_FREE = 6 * 1024**3


def validated_paths(dump, output, expected):
    dump, output = Path(dump).absolute(), Path(output).absolute()
    if (dump.is_symlink() or not dump.is_file() or
            not dump.resolve().is_relative_to(BACKUPS.resolve()) or
            not re.fullmatch(r'market-20[0-9]{6}T[0-9]{6}Z\.dump', dump.name)):
        raise ValueError('Expected a complete daily backup in the reserved directory')
    if (output.exists() or output.is_symlink() or
            output.parent.resolve() != (ROOT/'work').resolve() or
            not re.fullmatch(r'restore-daily-[0-9]{8}-[a-z0-9-]{1,24}', output.name)):
        raise ValueError('Expected a new isolated restore directory')
    if not re.fullmatch(r'[a-f0-9]{64}', expected):
        raise ValueError('Expected a verified SHA256')
    if shutil.disk_usage(output.parent).free < MIN_FREE:
        raise ValueError('Less than 45 GiB free before restoration')
    for executable in ('initdb', 'pg_ctl', 'pg_restore'):
        if not (PG/(executable+'.exe')).is_file():
            raise ValueError('Existing PostgreSQL17 tools missing')
    with dump.open('rb') as stream:
        if stream.read(5) != b'PGDMP':
            raise ValueError('Expected custom format dump')
        stream.seek(0)
        if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
            raise ValueError('Backup SHA256 mismatch')
    with socket.socket() as probe:
        probe.bind((HOST, PORT))
    return dump.resolve(), output


def verify(dump, output, expected, timeout=7200):
    dump, output = validated_paths(dump, output, expected)
    output.mkdir()
    # Do this before writing any local password or restored provider settings.
    # Use the current user's SID rather than a locale-dependent account name.
    acl_script = r'''$dir=$args[0]; $sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User;
    $acl=Get-Acl -LiteralPath $dir; $acl.SetAccessRuleProtection($true,$false);
    foreach($who in @($sid,[System.Security.Principal.SecurityIdentifier]'S-1-5-18')) {
      $rule=[System.Security.AccessControl.FileSystemAccessRule]::new($who,'FullControl','ContainerInherit,ObjectInherit','None','Allow');
      $acl.AddAccessRule($rule)
    }; Set-Acl -LiteralPath $dir -AclObject $acl'''
    subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',
                    '& { '+acl_script+' }',str(output)],check=True,capture_output=True,
                   timeout=30,**hidden_options())
    data=output/'cluster-data'
    pwfile=output/'local-password.txt'
    password=secrets.token_urlsafe(36)
    pwfile.write_text(password+'\n',encoding='utf-8')
    env={k:v for k,v in os.environ.items() if not k.upper().startswith('PG')}
    env.update(PGPASSWORD=password,PGCONNECT_TIMEOUT='10',PGCLIENTENCODING='UTF8')
    report={'started_at':datetime.now(timezone.utc).isoformat(),'status':'running',
            'stage':'init','passed':False,'dump':str(dump),'sha256':expected,
            'production_contacted':False,'existing_cluster_modified':False,
            'source_content_equality_verified':False,'port':PORT,
            'free_bytes_before':shutil.disk_usage(output).free}
    started=time.monotonic()
    ctl=[str(PG/'pg_ctl.exe'),'-D',str(data)]
    restore_process=None
    server_attempted=False

    def save(stage=None):
        if stage: report['stage']=stage
        report['elapsed_seconds']=round(time.monotonic()-started,2)
        report['free_bytes_now']=shutil.disk_usage(output).free
        temporary=output/'verification.json.next'
        temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(output/'verification.json')

    def command(args, name, limit=60, allowed=(0,)):
        with (output/name).open('ab') as log:
            result=subprocess.run(args,stdin=subprocess.DEVNULL,stdout=log,
                                  stderr=subprocess.STDOUT,env=env,timeout=limit,**hidden_options())
        if result.returncode not in allowed:
            raise RuntimeError('Local command failed: '+name)
        return result.returncode

    def connect(db=DATABASE):
        connection=psycopg.connect(host=HOST,port=PORT,user=USER,password=password,
            dbname=db,autocommit=True,row_factory=dict_row,connect_timeout=10,
            options='-c statement_timeout=900000 -c lock_timeout=5000')
        identity=connection.execute("SELECT current_setting('data_directory') AS path, "
            "current_setting('server_version_num')::int AS version, inet_server_port() AS port").fetchone()
        if (Path(identity['path']).resolve()!=data.resolve() or identity['port']!=PORT
                or not 170000<=identity['version']<180000):
            connection.close()
            raise RuntimeError('Local cluster identity mismatch')
        return connection

    try:
        save('init')
        command([str(PG/'initdb.exe'),'-D',str(data),'-U',USER,'--auth=scram-sha-256',
                 '--pwfile='+str(pwfile),'--encoding=UTF8','--locale=C'],'init.log',120)
        server_attempted=True
        options=f'-h {HOST} -p {PORT} -c shared_buffers=128MB -c max_connections=8 -c maintenance_work_mem=128MB'
        command(ctl+['-l',str(output/'postgres.log'),'-w','-t','60','-o',options,'start'],'commands.log')
        save('creating_database')
        with connect('postgres') as db:
            db.execute("CREATE DATABASE stocks_restore TEMPLATE template0 ENCODING 'UTF8'")
        save('restoring')
        with (output/'restore.log').open('ab') as log:
            args=[str(PG/'pg_restore.exe'),'--exit-on-error','--no-owner','--no-acl',
                  '--no-tablespaces','--jobs=2','--no-password','--host',HOST,
                  '--port',str(PORT),'--username',USER,'--dbname',DATABASE,str(dump)]
            restore_process=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=log,
                stderr=subprocess.STDOUT,env=env,**hidden_options())
            report['restore_pid']=restore_process.pid
            save()
            while restore_process.poll() is None:
                if time.monotonic()-started>timeout or shutil.disk_usage(output).free<STOP_FREE:
                    raise RuntimeError('Restore time or local space budget exceeded')
                time.sleep(5)
                save()
            if restore_process.returncode:
                raise RuntimeError('pg_restore failed; original dump preserved')
        report['pg_restore_succeeded']=True
        save('checking_database')
        with connect() as db:
            report['restored']=inspect_database(db)
        if not report['restored']['passed']:
            raise RuntimeError('Restored table/index checks failed')
        with dump.open('rb') as stream:
            report['dump_unchanged']=hashlib.file_digest(stream,'sha256').hexdigest()==expected
        report['passed']=report['dump_unchanged']
        report['status']='verified' if report['passed'] else 'failed'
    except (Exception,KeyboardInterrupt) as exc:
        report.update(status='failed',passed=False,failure_type=type(exc).__name__,
                      failed_stage=report['stage'])
    finally:
        if restore_process is not None and restore_process.poll() is None:
            restore_process.terminate()
            try: restore_process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                restore_process.kill();restore_process.wait(timeout=10)
        if server_attempted:
            try:
                if command(ctl+['status'],'commands.log',allowed=(0,3))==0:
                    command(ctl+['-m','fast','-w','-t','60','stop'],'commands.log')
                report['local_cluster_stopped']=command(ctl+['status'],'commands.log',allowed=(0,3))==3
            except Exception:
                report.update(status='failed',passed=False,local_cluster_stopped=False)
        env.pop('PGPASSWORD',None)
        save('finished')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dump',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--sha256',required=True)
    parser.add_argument('--timeout',type=int,default=7200)
    args=parser.parse_args()
    if not 60<=args.timeout<=14400: parser.error('timeout must be 60..14400 seconds')
    report=verify(args.dump,args.out,args.sha256,args.timeout)
    print(json.dumps(report,ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
