"""Route the stock app's named public assets without opening other root paths."""
from datetime import datetime, timezone
from pathlib import Path
import subprocess


def upgraded_config(config):
    old = r"location ~ ^/(stocks(?:/|$)|_next/|api/market(?:/|-)|icon\.svg$) {"
    new = r"location ~ ^/(stocks(?:/|$)|_next/|api/market(?:/|-)|brand/jusikdam-symbol\.png$|icon\.(?:svg|png)$) {"
    if new in config:
        return config
    if config.count(old) != 1:
        raise ValueError('Expected one stock web route')
    return config.replace(old, new)


def main():
    import fcntl
    roots = [p.resolve() for p in Path('/etc/nginx/sites-enabled').iterdir()
             if p.is_file() and 'server_name stocks.62-171-141-206.sslip.io;' in p.read_text()]
    if len(roots) != 1:
        raise RuntimeError('Ambiguous stock proxy')
    with Path('/srv/platform/nginx-edit.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = roots[0]
        old = path.read_text()
        new = upgraded_config(old)
        if new == old:
            print('Stock assets already routed')
            return
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        backup = Path('/srv/services/korea-stocks/current') / f'nginx.before-assets-{stamp}.conf'
        with backup.open('x') as handle:
            handle.write(old)
        try:
            path.write_text(new)
            subprocess.run(['nginx', '-t'], check=True)
            subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
        except Exception:
            path.write_text(old)
            subprocess.run(['nginx', '-t'], check=True)
            subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
            raise
        print(f'Stock assets routed; rollback={backup}')


if __name__ == '__main__':
    main()
