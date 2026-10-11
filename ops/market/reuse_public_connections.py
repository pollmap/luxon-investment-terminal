"""Reuse only the stock API's local HTTP connections; preserve cache freshness."""
import re

UPSTREAM='korea_stocks_api_reuse_v1'


def upgraded_config(config):
    if 'upstream '+UPSTREAM+' {' in config:
        return config
    marker=' location ^~ /api/market/v1/ {'
    start=config.index(marker)
    end=config.index('\n }',start)+3
    section=config[start:end]
    match=re.search(r'proxy_pass http://127\.0\.0\.1:(\d+);',section)
    if not match or section.count('proxy_pass ')!=1:
        raise ValueError('Expected one local stock API route')
    port=int(match.group(1))
    if port not in (8210,8211):
        raise ValueError('Unexpected stock API port')
    if 'proxy_http_version 1.1;' not in section or 'proxy_set_header Connection "";' not in section:
        raise ValueError('API route must clear Connection for HTTP/1.1 reuse')
    section=section.replace(match.group(0),'proxy_pass http://'+UPSTREAM+';')
    preamble='upstream '+UPSTREAM+' {\n server 127.0.0.1:'+str(port)+';\n keepalive 64;\n keepalive_requests 1000;\n keepalive_timeout 60s;\n}\n'
    return preamble+config[:start]+section+config[end:]


def main():
    import fcntl
    from pathlib import Path
    import subprocess
    root=Path('/srv/services/korea-stocks')
    candidates=[p for p in Path('/etc/nginx/sites-enabled').iterdir() if p.is_file()
                and 'server_name stocks.62-171-141-206.sslip.io' in p.read_text()]
    if len(candidates)!=1:
        raise RuntimeError('Ambiguous project proxy')
    with Path('/srv/platform/nginx-edit.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        path=candidates[0].resolve(); old=path.read_text(); new=upgraded_config(old)
        if old==new:
            print('Stock API connections already reused');return
        backup=root/'current/nginx.before-api-reuse.conf'
        if backup.exists():
            raise RuntimeError('Existing rollback copy must be reviewed')
        backup.write_text(old)
        try:
            path.write_text(new)
            subprocess.run(['nginx','-t'],check=True)
            subprocess.run(['systemctl','reload','nginx'],check=True)
        except Exception:
            path.write_text(old)
            subprocess.run(['nginx','-t'],check=True)
            subprocess.run(['systemctl','reload','nginx'],check=True)
            raise
    print('Reused stock API local connections; cache and WebSocket unchanged')


if __name__=='__main__':main()
