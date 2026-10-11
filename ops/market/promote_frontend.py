"""Promote locally compiled frontend fixes on the existing desktop stack."""
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time
import urllib.request

root=Path('/srv/services/korea-stocks')
os.umask(0o077)


def run(*args):
    subprocess.run(args,check=True)


with (root/'.deploy.lock').open('a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    previous=(root/'current').resolve()
    if previous.name!='20261003-toss-desktop-v5':
        raise RuntimeError('Unexpected release')
    version='20261003-toss-desktop-v6'
    release=root/'releases'/version
    shutil.copytree(previous,release)
    with tarfile.open(root/'incoming/frontend-fix.tar.gz') as archive:
        archive.extractall(release,filter='data')
    shutil.copy2(Path(__file__),release/'ops/market/promote_frontend.py')
    (release/'Dockerfile.frontend').write_text(
        'FROM korea-stocks-web:20261003-toss-desktop-v5\n'
        'COPY --chown=node:node web-build /app/apps/web/.next\n'
        'COPY --chown=node:node web-server.js /app/apps/web/server.js\n')
    run('docker','build','-q','-t','korea-stocks-web:'+version,'-f',str(release/'Dockerfile.frontend'),str(release))
    for kind in ('api','openalgo'):
        run('docker','tag','korea-stocks-'+kind+':'+previous.name,'korea-stocks-'+kind+':'+version)
    env=root/'shared/secrets/release.env'
    old=env.read_text()
    compose=['docker','compose','--env-file',str(env),'-f',str(release/'ops/market/compose.desktop.yml')]
    try:
        env.write_text(old.replace('RELEASE='+previous.name,'RELEASE='+version))
        run(*compose,'up','-d')
        for _ in range(40):
            try:
                with urllib.request.urlopen('http://127.0.0.1:8211/api/market/v1/health',timeout=5) as response:
                    if json.load(response).get('release')==version:
                        with urllib.request.urlopen('http://127.0.0.1:3211/stocks',timeout=15) as page:
                            assert page.status==200
                        break
            except Exception:
                pass
            time.sleep(2)
        else:
            raise RuntimeError('Frontend verification failed')
        pending=root/'current.next'
        pending.symlink_to(release,target_is_directory=True)
        pending.replace(root/'current')
        (release/'deployment.json').write_text(json.dumps({'release':version,'previous':previous.name,
            'state':'active','frontend_build':(release/'web-build/BUILD_ID').read_text().strip()}))
        print('ACTIVATED',version)
    except Exception:
        env.write_text(old)
        run('docker','compose','--env-file',str(env),'-f',str(previous/'ops/market/compose.desktop.yml'),'up','-d')
        raise
