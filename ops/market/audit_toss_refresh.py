"""Read-only cadence/recovery check. No environment values or tokens are emitted."""
import json,time
from datetime import datetime,UTC
from services.market_dashboard.store import Store,now

s=Store()
rows=[]
for i in range(3):
    q=s.get('quotes',{},use_redis=False).get('005930',{})
    received=q.get('received_at')
    rows.append({'at':now(),'market_time':q.get('as_of'),'received_at':received,
                 'age_seconds':round((datetime.now(UTC)-datetime.fromisoformat(received)).total_seconds(),2) if received else None,
                 'price':q.get('price'),'source':(q.get('quote_trace') or {}).get('source')})
    if i<2:time.sleep(6)
stream=s.get('provider-toss-stream',{})
print(json.dumps({'quotes':rows,'stream':{k:stream.get(k) for k in ('status','epoch','last_event_at')},
    'accepted':len(stream.get('accepted',[])),'rejected':len(stream.get('rejected',[])),
    'recovery':{k:{f:v.get(f) for f in ('status','epoch','completed_at')} for k,v in s.list_prefix('toss-recovery:').items()}},ensure_ascii=False))
