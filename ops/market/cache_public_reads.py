"""One-second shared cache for public search and summary only; no stale-on-error."""
import fcntl
import os
from pathlib import Path
import pwd
import subprocess

root=Path('/srv/services/korea-stocks')
candidates=[p for p in Path('/etc/nginx/sites-enabled').iterdir() if p.is_file()
            and 'server_name stocks.62-171-141-206.sslip.io' in p.read_text()]
if len(candidates)!=1:
    raise RuntimeError('Ambiguous project proxy')
cache=Path('/var/cache/nginx/korea-stocks')
cache.mkdir(parents=True,exist_ok=True,mode=0o755)
owner=pwd.getpwnam('www-data')
os.chown(cache,owner.pw_uid,owner.pw_gid)
config=candidates[0].resolve()
with Path('/srv/platform/nginx-edit.lock').open('a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX)
    old=config.read_text()
    if 'keys_zone=korea_stocks_read' in old:
        raise RuntimeError('Read cache already installed')
    marker=' location ^~ /api/market/v1/ {\n'
    if marker not in old:
        raise RuntimeError('Expected direct public API route')
    preamble='''proxy_cache_path /var/cache/nginx/korea-stocks levels=1:2 keys_zone=korea_stocks_read_v2:10m max_size=64m inactive=10m use_temp_path=off;
map "$uri:$arg_view" $korea_stocks_skip_cache {
 default 1;
 "/api/market/v1/search:" 0;
 "~^/api/market/v1/securities/[A-Z0-9]{6}:summary$" 0;
}
'''
    settings='''  proxy_cache korea_stocks_read_v2;
  proxy_cache_methods GET HEAD;
  proxy_cache_key "$scheme$proxy_host$request_uri";
  proxy_cache_valid 200 1s;
  proxy_cache_lock on;
  proxy_cache_lock_timeout 1s;
  proxy_cache_bypass $korea_stocks_skip_cache $http_authorization $http_cookie;
  proxy_no_cache $korea_stocks_skip_cache $http_authorization $http_cookie $upstream_http_set_cookie;
  proxy_ignore_headers Cache-Control Expires;
  add_header X-Market-Cache $upstream_cache_status always;
'''
    (root/'current/nginx.before-read-cache.conf').write_text(old)
    try:
        config.write_text(preamble+old.replace(marker,marker+settings))
        subprocess.run(['nginx','-t'],check=True)
        subprocess.run(['systemctl','reload','nginx'],check=True)
    except Exception:
        config.write_text(old)
        subprocess.run(['nginx','-t'],check=True)
        subprocess.run(['systemctl','reload','nginx'],check=True)
        raise
print('Public search and summary cache: <= 1 second; authenticated/cookie requests bypass')
