"""Read-only production sample; credentials remain in the existing container."""
import json
from pathlib import Path
import subprocess
from services.market_dashboard.fundamental_metrics import ACCOUNTS, calculate_fundamental_metrics, _valid
from services.market_dashboard.eps_calculation import EPS_ACCOUNTS

names=sorted(set(ACCOUNTS)|EPS_ACCOUNTS|{'DilutedEarningsLossPerShare'})
remote='''import json
from services.market_dashboard.store import Store
from services.market_dashboard.fact_store import FactStore
s=Store(); f=FactStore(s)
result={}
for code,corp in [('005930','00126380'),('000660','00164779'),('005380','00164742')]:
 rows=f.read(corp,account_names=NAMES)
 meta=s.metadata('bars:'+code+':통합:raw',{})
 result[code]={'facts':rows, 'prices':{**meta,'rows':s.time_series.rows(meta['_series_ref'],start='2019-01-01') if meta.get('_series_ref') else []}}
print(json.dumps(result,ensure_ascii=False))
'''.replace('NAMES',repr(names))
p=subprocess.run(['ssh','-o','BatchMode=yes','chanhee-vps','docker exec -i korea-stocks-market-api-1 python -'],input=remote,text=True,encoding='utf-8',capture_output=True,timeout=120)
if p.returncode:
    raise RuntimeError('Source audit failed; no state was changed')
data=json.loads(p.stdout)
for code,row in data.items():
    row['facts']=[r for r in row['facts'] if _valid(r) or (r.get('account_id','').rsplit('}',1)[-1] in EPS_ACCOUNTS|{'DilutedEarningsLossPerShare'} and not any('ConsolidatedAndSeparate' not in k and 'ClassesOfShareCapital' not in k for k in (r.get('dimensions') or {})))]
folder=Path('work/multi-metrics');folder.mkdir(parents=True,exist_ok=True)
(folder/'actual-inputs.json').write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
for code,row in data.items():
    result=calculate_fundamental_metrics(row['facts'])
    print(code,len(row['facts']),{m:len([r for r in result['rows'] if r['metric']==m]) for m in sorted({r['metric'] for r in result['rows']})})
