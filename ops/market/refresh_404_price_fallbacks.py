"""Bounded maintenance using the existing public collector and its rate limit.

Pause the worker while running to avoid concurrent writes. Does not retire any
Toss failure or claim equivalent trading/intraday coverage.
"""
import asyncio
import json
from services.market_dashboard.store import Store
from services.market_dashboard.providers import PublicProvider
from services.market_dashboard.history_worker import refresh_history


async def run():
    store=Store();provider=PublicProvider(store);results=[]
    codes=sorted({key.split(':')[1] for key,item in store.list_prefix('toss-backfill:').items()
                  if item.get('http_status')==404})
    try:
        for code in codes:
            before=store.metadata('prices:'+code,{})
            old_count=before.get('row_count',len(before.get('rows',[])))
            status=await refresh_history(store,provider,code)
            after=store.metadata('prices:'+code,{})
            result={'code':code,'status':status,'before':old_count,
                    'after':after.get('row_count',len(after.get('rows',[]))),
                    'first':after.get('first_time'),'last':after.get('last_time'),
                    'history_complete':after.get('history_complete',False)}
            results.append(result)
            print(json.dumps(result),flush=True)
        print(json.dumps({'summary':{'codes':len(codes),'added_rows':sum(r['after']-r['before'] for r in results),
                         'errors':sum(r['status']=='error' for r in results)}}),flush=True)
    finally:
        await provider.client.aclose()


if __name__=='__main__':
    asyncio.run(run())
