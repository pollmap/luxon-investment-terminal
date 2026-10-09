"""Read-only implementation/data evidence; never prints environment credentials."""
import json
from pathlib import Path
from services.market_dashboard.store import Store, now
from services.market_dashboard.toss_contract import PUBLIC_GET_PATHS

store=Store()
report={'at':now(),'universe':len(store.listed_codes()),'endpoints':{},'series':{},'jobs':{}}
for path in PUBLIC_GET_PATHS:
    report['endpoints'][path]=store.get('toss-api:'+path,{'status':'not_observed'})
for key in ('provider-toss','provider-toss-stream','toss-backfill-progress','corporate-events-job:005930','corporate-event-index:005930'):
    data=store.get(key,{})
    report['jobs'][key]={k:v for k,v in data.items() if k not in ('rows','documents','source_trace')}
    if 'documents' in data:
        report['jobs'][key]['document_statuses']={}
        for item in data['documents'].values():
            status=item['status']; out=report['jobs'][key]['document_statuses']
            out[status]=out.get(status,0)+1
for key,value in store.list_prefix('toss-market-job:').items():
    report['jobs'][key]=value
for kind in ('bars:005930:통합:day','bars:005930:통합:raw','toss:investor-trading:005930','toss:credit-trades:005930','toss:securities-lending:005930','events:005930'):
    value=store.get(kind,{})
    rows=value.get('rows',[])
    dates=sorted(r.get('date') or r.get('time','')[:10] for r in rows)
    report['series'][kind]={'rows':len(rows),'first':dates[0] if dates else None,'last':dates[-1] if dates else None,
        **{k:value.get(k) for k in ('status','backfill_complete','next_before','next_until','coverage')}}
root=Path('/app/services/market_dashboard')
report['retired_modules_present']=[p.name for p in root.glob('*.py') if p.name.startswith(('kiwoom','kis_'))]
print(json.dumps(report,ensure_ascii=False))
