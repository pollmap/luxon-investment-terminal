"""Small backend update of the active desktop release; preserve the live database."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request
import fcntl

ROOT = Path('/srv/services/korea-stocks')


def run(*args):
    subprocess.run(args, check=True)


def main():
    os.umask(0o077)
    with (ROOT/'.deploy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = (ROOT/'current').resolve()
        if previous.name != '20261003-toss-desktop-v4':
            raise RuntimeError('Unexpected active release')
        release = ROOT/'releases/20261003-toss-desktop-v5'
        shutil.copytree(previous, release)
        for name in ('app.py','fact_store.py','xbrl_facts.py','openalgo_bridge.py'):
            shutil.copy2(ROOT/'incoming'/name, release/'services/market_dashboard'/name)
        shutil.copy2(Path(__file__), release/'ops/market/promote_backend.py')
        (release/'Dockerfile.patch').write_text(
            'FROM korea-stocks-api:20261003-toss-desktop-v4\n'
            'COPY --chown=market:market services/market_dashboard /app/services/market_dashboard\n')
        version = release.name
        run('docker','build','-q','-t','korea-stocks-api:'+version,'-f',str(release/'Dockerfile.patch'),str(release))
        for kind in ('web','openalgo'):
            run('docker','tag','korea-stocks-'+kind+':'+previous.name,'korea-stocks-'+kind+':'+version)
        env = ROOT/'shared/secrets/release.env'
        old_env = env.read_text()
        new_env = old_env.replace('RELEASE='+previous.name,'RELEASE='+version)
        compose = ['docker','compose','--env-file',str(env),'-f',str(release/'ops/market/compose.desktop.yml')]
        try:
            env.write_text(new_env)
            run(*compose,'up','-d','market-api','market-worker')
            for _ in range(40):
                try:
                    with urllib.request.urlopen('http://127.0.0.1:8211/api/market/v1/health',timeout=5) as response:
                        if json.load(response).get('release') == version:
                            break
                except Exception:
                    pass
                time.sleep(2)
            else:
                raise RuntimeError('Backend release health failed')
            with urllib.request.urlopen('http://127.0.0.1:3211/api/market/v1/securities/005930?view=summary',timeout=15) as response:
                if json.load(response).get('security',{}).get('code') != '005930':
                    raise RuntimeError('Public summary failed')
            pending = ROOT/'current.next'
            pending.symlink_to(release,target_is_directory=True)
            pending.replace(ROOT/'current')
            (release/'deployment.json').write_text(json.dumps({'release':version,'previous':previous.name,'state':'active','frontend_build_unchanged':True}))
            print('ACTIVATED',version)
        except Exception:
            env.write_text(old_env)
            run('docker','compose','--env-file',str(env),'-f',str(previous/'ops/market/compose.desktop.yml'),'up','-d','market-api','market-worker')
            raise


if __name__ == '__main__':
    main()
