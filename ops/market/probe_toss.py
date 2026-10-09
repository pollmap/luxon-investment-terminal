"""Read-only market probe. Credentials and tokens never appear in diagnostics."""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path('/srv/services/korea-stocks/shared/secrets')


def main():
    os.umask(0o077)
    env = dict(line.strip().split('=', 1) for line in (ROOT / 'toss.env').read_text().splitlines() if '=' in line)
    token_file = ROOT / 'toss-token.json'
    token = json.loads(token_file.read_text()) if token_file.exists() else {}
    try:
        if token.get('expires_at', 0) < time.time() + 120:
            payload = urllib.parse.urlencode({'grant_type': 'client_credentials', 'client_id': env['TOSS_CLIENT_ID'], 'client_secret': env['TOSS_CLIENT_SECRET']}).encode()
            req = urllib.request.Request('https://openapi.tossinvest.com/oauth2/token', payload, {'Content-Type': 'application/x-www-form-urlencoded'})
            with urllib.request.urlopen(req, timeout=25) as response:
                token = json.load(response)
            token['expires_at'] = time.time() + int(token['expires_in'])
            temporary = token_file.with_suffix('.partial')
            temporary.write_text(json.dumps(token))
            temporary.chmod(0o600)
            temporary.replace(token_file)
        print(json.dumps({'authentication': 'success'}), flush=True)
        for path in ('/api/v1/prices?symbols=005930,000660', '/api/v1/orderbook?symbol=005930', '/api/v1/candles?symbol=005930&interval=1d&count=5'):
            request = urllib.request.Request('https://openapi.tossinvest.com' + path, headers={'Authorization': 'Bearer ' + token['access_token']})
            with urllib.request.urlopen(request, timeout=25) as response:
                body = json.load(response)
            data = body.get('result')
            print(json.dumps({'endpoint': path.split('?')[0], 'status': 'success', 'result_count': len(data) if isinstance(data, list) else len((data or {}).get('candles', [])) if 'candles' in path else len((data or {}).get('asks', []))}), flush=True)
    except urllib.error.HTTPError as error:
        try:
            payload = json.loads(error.read())
            code = payload.get('error') or payload.get('code')
            if not isinstance(code, str) or not code.replace('-', '').replace('_', '').isalnum():
                code = 'provider_rejected'
        except Exception:
            code = 'provider_rejected'
        print(json.dumps({'status': error.code, 'error_code': code}), flush=True)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
