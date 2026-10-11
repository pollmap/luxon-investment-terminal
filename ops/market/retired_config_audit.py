"""Inventory project-scoped deprecated configuration names without values."""
import json,os,re,hashlib
from pathlib import Path

paths=[Path('/run/toss/release.env'),Path('/run/toss/toss.env'),
       Path('/app/storage/public-market/runtime.env')]
retired=re.compile(r'^(KIWOOM_|KIS_|MARKET_KIWOOM_|MARKET_KIS_|MARKET_BROKER$)')
results=[]
for path in paths:
    if not path.is_file():
        continue
    content=path.read_bytes()
    names=[line.split('=',1)[0].strip().removeprefix('export ').strip()
        for line in content.decode('utf-8-sig').splitlines() if '=' in line and not line.lstrip().startswith('#')]
    results.append({'path':str(path),'retired_names':[key for key in names if retired.match(key)],
        'sha256':hashlib.sha256(content).hexdigest()})
print(json.dumps({'files':results,'retired_environment_names':[k for k in os.environ if retired.match(k)]}))
