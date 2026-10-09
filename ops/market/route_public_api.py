"""Keep public JSON on the API upstream, avoiding an unnecessary Next.js hop."""
import fcntl
from pathlib import Path
import subprocess
import urllib.request

root = Path('/srv/services/korea-stocks')
candidates = [p for p in Path('/etc/nginx/sites-enabled').iterdir()
              if p.is_file() and 'server_name stocks.62-171-141-206.sslip.io' in p.read_text()]
if len(candidates) != 1:
    raise RuntimeError('Ambiguous service proxy configuration')
config = candidates[0].resolve()
with Path('/srv/platform/nginx-edit.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    old = config.read_text()
    marker = ' location = /api/market/v1/stream {'
    if marker not in old or '127.0.0.1:8211' not in old:
        raise RuntimeError('Unexpected service proxy state')
    block = ''' location ^~ /api/market/v1/ {
  proxy_pass http://127.0.0.1:8211;
  proxy_http_version 1.1;
  proxy_set_header Connection "";
  proxy_set_header Host $host;
  proxy_set_header X-Forwarded-Proto $scheme;
  proxy_set_header X-Real-IP $remote_addr;
  proxy_read_timeout 30s;
 }
'''
    if 'location ^~ /api/market/v1/' in old:
        raise RuntimeError('Public API route already configured')
    (root/'current/nginx.before-api-routing.conf').write_text(old)
    try:
        config.write_text(old.replace(marker, block + marker))
        subprocess.run(['nginx','-t'],check=True)
        subprocess.run(['systemctl','reload','nginx'],check=True)
        with urllib.request.urlopen('https://stocks.62-171-141-206.sslip.io/api/market/v1/search?q=005930',timeout=15) as response:
            assert response.status == 200
        print('Public API route verified; exact blocked paths and WebSocket route preserved')
    except Exception:
        config.write_text(old)
        subprocess.run(['nginx','-t'],check=True)
        subprocess.run(['systemctl','reload','nginx'],check=True)
        raise
