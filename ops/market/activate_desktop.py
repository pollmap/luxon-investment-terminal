"""Staged desktop release. Reuses the project's existing DB; no other services are changed."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import time
import urllib.request

ROOT=Path('/srv/services/korea-stocks')
OLD='hannun-public-market-market-'

def run(*args,**kwargs):
    return subprocess.run(args,check=True,**kwargs)


def inspect(name):
    return json.loads(subprocess.check_output(['docker','inspect',name]))[0]


def wait(url):
    for _ in range(30):
        try:
            with urllib.request.urlopen(url,timeout=5) as response:
                if response.status==200:return
        except Exception:
            time.sleep(2)
    raise RuntimeError('Release health failed: '+url)


def activate(version,archive):
    if not re.fullmatch(r'[a-zA-Z0-9-]{5,80}',version):raise ValueError('Invalid release')
    os.umask(0o077)
    release=ROOT/'releases'/version
    release.mkdir(parents=True,exist_ok=False)
    with tarfile.open(archive) as source:
        source.extractall(release,filter='data')
    with tarfile.open(Path(archive).parent/'web-runtime.tar.gz') as runtime:
        runtime.extractall(release/'web-runtime',filter='data')
    shutil.copy2(Path(archive).parent/'verify_dart_ttm_inputs.py',release/'scripts/verify_dart_ttm_inputs.py')
    shutil.copy2(Path(__file__),release/'ops/market/activate_desktop.py')
    # No DB copy/restore. Same durable volume, same schema, same data.
    shared=ROOT/'shared'
    for folder in ('data/openalgo','secrets'):(shared/folder).mkdir(parents=True,exist_ok=True)
    for path in (shared/'secrets').iterdir():
        os.chown(path,1000,1000);path.chmod(0o600)
    os.chown(shared/'secrets',1000,1000);(shared/'secrets').chmod(0o700)
    os.chown(shared/'data/openalgo',1000,1000)
    for name,volume in (('postgres','hannun-public-market_market-db'),('sources','hannun-public-market_market-sources')):
        target=Path(json.loads(subprocess.check_output(['docker','volume','inspect',volume]))[0]['Mountpoint'])
        link=shared/'data'/name
        if not link.exists():link.symlink_to(target,target_is_directory=True)
    api=inspect(OLD+'api-1');web=inspect(OLD+'web-1');worker=inspect(OLD+'worker-1')
    env=dict(item.split('=',1) for item in api['Config']['Env'])
    worker_env=dict(item.split('=',1) for item in worker['Config']['Env'])
    private=shared/'secrets/release.env'
    private.write_text('RELEASE='+version+'\nMARKET_DATABASE_URL='+env['MARKET_DATABASE_URL']+'\nDART_API_KEY='+worker_env.get('DART_API_KEY','')+'\n')
    private.chmod(0o600)
    run('docker','tag',api['Image'],f'korea-stocks-base-api:{version}')
    run('docker','tag',web['Image'],f'korea-stocks-base-web:{version}')
    (release/'Dockerfile.api').write_text(f"FROM korea-stocks-base-api:{version}\nCOPY --chown=market:market services/market_dashboard /app/services/market_dashboard\nCOPY --chown=market:market packages/core /app/packages/core\nCOPY --chown=market:market scripts /app/scripts\n")
    (release/'Dockerfile.web').write_text(f"FROM korea-stocks-base-web:{version}\nCOPY --chown=node:node web-build /app/apps/web/.next\nCOPY --chown=node:node web-server.js /app/apps/web/server.js\nCOPY --chown=node:node apps/web/public /app/apps/web/public\nCOPY --chown=node:node web-runtime /tmp/web-runtime\nRUN cp -r /tmp/web-runtime/next-compiled/. /app/apps/web/node_modules/next/dist/compiled/ && cp -r /tmp/web-runtime/react /app/apps/web/node_modules/react && cp -r /tmp/web-runtime/react-dom /app/apps/web/node_modules/react-dom\n")
    (release/'Dockerfile.openalgo').write_text(f"FROM korea-stocks-base-api:{version}\nUSER root\nCOPY wheels /wheels\nCOPY ops/openalgo /integration\nRUN pip install --no-index --find-links=/wheels -r /integration/requirements.txt\nADD openalgo-upstream.tar.gz /opt/\nRUN mkdir -p /opt/openalgo/broker/toss/api && touch /opt/openalgo/broker/toss/__init__.py /opt/openalgo/broker/toss/api/__init__.py\nCOPY ops/openalgo/toss_data.py /opt/openalgo/broker/toss/api/data.py\nUSER market\nCMD [\"python\",\"/integration/server.py\"]\n")
    # Local compiled JS + local downloaded wheels: no frontend compilation on the VPS.
    for kind in ('api','web','openalgo'):
        run('docker','build','-q','-t',f'korea-stocks-{kind}:{version}','-f',str(release/f'Dockerfile.{kind}'),str(release))
    networks=subprocess.check_output(['docker','network','ls','--format','{{.Name}}'],text=True).splitlines()
    if 'korea-stocks-private' not in networks:run('docker','network','create','korea-stocks-private')
    for kind in ('db','redis'):
        name=OLD+kind+'-1'
        if 'korea-stocks-private' not in inspect(name)['NetworkSettings']['Networks']:
            run('docker','network','connect','--alias','market-'+kind,'korea-stocks-private',name)
    run('docker','update','--memory','384m','--memory-swap','768m',OLD+'db-1')
    run('docker','update','--memory','128m','--memory-swap','256m',OLD+'redis-1')
    compose=['docker','compose','--env-file',str(private),'-f',str(release/'ops/market/compose.desktop.yml')]
    (ROOT/'backups').mkdir(exist_ok=True)
    run('python3',str(release/'ops/market/backup.py'),'--root',str(ROOT/'backups'),'--verify-restore')
    # Stop only this project's collector to prevent token races.
    run('docker','stop',OLD+'worker-1')
    switched=False
    try:
        run(*compose,'up','-d','market-api','market-web','openalgo')
        wait('http://127.0.0.1:8211/api/market/v1/health')
        seed=Path(archive).parent/'seed-toss.json'
        if seed.is_file():
            loader="""import json,sys
from services.market_dashboard.store import Store
s=Store(); payload=json.load(sys.stdin)
for key,value in payload.items():
 if key=='quotes':
  previous=s.get('quotes',{});previous.update(value);s.put(key,previous)
 elif key.startswith(('bars:','toss:','toss-chart:','toss-normalized:','depth:','financing:','short-selling:','trading:')):s.put(key,value)
print('Verified Toss snapshots imported:',len(payload))
"""
            with seed.open('rb') as source:
                run('docker','exec','-i','korea-stocks-market-api-1','python','-c',loader,stdin=source)
            raw=seed.with_name('seed-raw.tar.gz')
            if raw.is_file():
                with tarfile.open(raw) as source:source.extractall(shared/'data/sources',filter='data')
                # Extracting as root under umask 077 must not make seeded archives
                # unwritable to the collector's verified runtime UID.
                seeded = shared/'data/sources/raw/toss'
                if seeded.is_dir():
                    for path in (seeded, *seeded.rglob('*')):
                        os.chown(path, 1000, 1000)
        wait('http://127.0.0.1:3211/stocks')
        wait('http://127.0.0.1:3211/api/market/v1/search?q=005930')
        # Only the matching hostname's file is edited, protected by the shared Nginx lock.
        candidates=[p for p in Path('/etc/nginx/sites-enabled').iterdir() if p.is_file() and 'server_name stocks.62-171-141-206.sslip.io' in p.read_text()]
        if len(candidates)!=1:raise RuntimeError('Ambiguous project proxy file')
        config=candidates[0].resolve();old=config.read_text()
        if '127.0.0.1:3210' not in old or '127.0.0.1:8210' not in old:raise RuntimeError('Unexpected proxy ownership')
        previous=release/'nginx.before.conf';previous.write_text(old)
        Path('/srv/platform').mkdir(exist_ok=True)
        with Path('/srv/platform/nginx-edit.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            config.write_text(old.replace('127.0.0.1:3210','127.0.0.1:3211').replace('127.0.0.1:8210','127.0.0.1:8211'))
            try:run('nginx','-t');run('systemctl','reload','nginx')
            except Exception:config.write_text(old);raise
        switched=True
        run(*compose,'up','-d','market-worker')
        wait('https://stocks.62-171-141-206.sslip.io/api/market/v1/health')
        current=ROOT/'current';pending=ROOT/'current.next'
        pending.symlink_to(release,target_is_directory=True);pending.replace(current)
        run('docker','stop',OLD+'api-1',OLD+'web-1')
        timer=Path('/etc/systemd/system/hannun-db-backup.timer')
        shutil.copy2(timer,release/'backup.timer.before')
        timer.write_text('[Unit]\nDescription=Korea stocks daily database backup\n[Timer]\nOnCalendar=*-*-* 04:20:00 Asia/Seoul\nPersistent=true\n[Install]\nWantedBy=timers.target\n')
        service=Path('/etc/systemd/system/hannun-db-backup.service')
        shutil.copy2(service,release/'backup.service.before')
        service.write_text('[Unit]\nDescription=Korea stocks verified database backup\n[Service]\nType=oneshot\nExecStart=/usr/bin/python3 /srv/services/korea-stocks/current/ops/market/backup.py --root /srv/services/korea-stocks/backups\n')
        run('systemctl','daemon-reload');run('systemctl','restart','hannun-db-backup.timer')
        (release/'deployment.json').write_text(json.dumps({'release':version,'state':'active','db':'existing durable volume','previous_api_image':api['Image'],'previous_web_image':web['Image']}))
        print('ACTIVATED',version)
    except Exception:
        run(*compose,'stop')
        run('docker','start',OLD+'api-1',OLD+'web-1',OLD+'worker-1')
        if switched:
            config.write_text(old);run('nginx','-t');run('systemctl','reload','nginx')
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('version');parser.add_argument('archive');args=parser.parse_args()
    ROOT.mkdir(parents=True,exist_ok=True)
    with (ROOT/'.deploy.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        activate(args.version,args.archive)
