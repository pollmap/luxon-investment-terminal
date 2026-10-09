"""Public HTTP smoke/size samples, not a load test or Core Web Vitals result."""
import argparse
import gzip
import json
from datetime import datetime,UTC
from pathlib import Path
from time import perf_counter
import httpx

parser=argparse.ArgumentParser()
parser.add_argument('--origin',required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
paths={
    'returns':'/securities/005930/price-window?view=returns',
    'daily':'/securities/005930/price-window?range=252&interval=day',
    'monthly':'/securities/005930/price-window?range=2600&interval=month',
    'summary':'/securities/005930?view=summary',
    'eps':'/securities/005930/financials/per-share?frequency=ttm',
    'events':'/securities/005930/events',
    'calendar_fx':'/market-context?date=2026-09-29&date_time=2026-09-29T00%3A00%3A00Z&base=USD&quote=KRW',
    'reverse_fx':'/market-context?date_time=2026-09-29T00%3A00%3A00Z&base=KRW&quote=USD',
    'preferred':'/listings?market=KOSPI&status=ACTIVE&security_type=STOCK&common_share=false',
    'scheduled':'/listings?market=KOSDAQ&status=SCHEDULED&security_type=STOCK',
    'delisted':'/listings?market=KOSPI&status=DELISTED&security_type=STOCK',
    'institutions':'/indices/KOSPI/investors?interval=1d&limit=30',
}
report={'at':datetime.now(UTC).isoformat(),'origin':args.origin,'conditions':'one serial client; no load-test or p95 claim','checks':{}}
with httpx.Client(base_url=args.origin+'/api/market/v1',timeout=90,headers={'Accept-Encoding':'gzip'}) as client:
    for name,path in paths.items():
        samples=[]
        for _ in range(3 if name in ('returns','daily','monthly','summary') else 1):
            start=perf_counter()
            with client.stream('GET',path) as response:
                raw=b''.join(response.iter_raw())
                elapsed=(perf_counter()-start)*1000
                response.raise_for_status()
                decoded=gzip.decompress(raw) if response.headers.get('content-encoding')=='gzip' else raw
                data=json.loads(decoded)
                samples.append({'ms':round(elapsed,1),'wire_bytes':len(raw),'decoded_bytes':len(decoded),'encoding':response.headers.get('content-encoding')})
                etag=response.headers.get('etag')
        detail={'samples':samples,'status':data.get('status'),'rows':len(data.get('rows',[])),'coverage':data.get('coverage')}
        if name=='returns':
            assert len(data['rows'])<=5 and samples[-1]['wire_bytes']<100000
        if name=='daily':
            assert 150<len(data['rows'])<400 and samples[-1]['wire_bytes']<100000
            if etag:
                unchanged=client.get(path,headers={'If-None-Match':etag})
                detail['revalidation_status']=unchanged.status_code
        if name=='eps':detail.update(ttm_status=data.get('ttm_status'),readiness=data.get('ttm_readiness'))
        if name in ('calendar_fx','reverse_fx'):
            detail.update(exchange=data.get('exchange_rate'),calendars={k:{'status':v.get('status'),'date':v.get('calendar',{}).get('today',{}).get('date')} for k,v in data.get('calendars',{}).items()})
        if name in ('preferred','scheduled','delisted'):detail['sample']=data.get('rows',[])[:3]
        if name=='institutions':detail['latest']=data.get('rows',[])[-1:] if data.get('rows') else []
        report['checks'][name]=detail
args.output.parent.mkdir(parents=True,exist_ok=True)
args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
