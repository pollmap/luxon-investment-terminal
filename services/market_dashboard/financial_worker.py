"""Full statement collection is independent of the daily disclosure feed TTL."""
import asyncio
import logging
from bisect import bisect_left
from .store import now


async def step(source):
    store=source.store
    codes=sorted(store.listed_codes())
    if not codes or not source.dart:
        return {'status':'waiting'}
    progress=store.get('dart-full-report-progress',{})
    next_code=progress.get('next_code')
    cursor=(bisect_left(codes,next_code) % len(codes) if next_code
            else int(progress.get('cursor',0)) % len(codes))
    active=sorted(set(store.active()) & set(codes))
    # Background work receives every visit; active work receives one extra slot
    # in five. A repeatedly viewed company must not consume half the daily quota.
    visit=int(progress.get('visits',0))
    background=codes[cursor]
    priority=active[int(progress.get('active_cursor',0))%len(active)] if active and visit%5==4 else None
    targets=list(dict.fromkeys(([priority] if priority else [])+[background]))
    results={}
    for code in targets:
        results[code]=await source.refresh_financial_data(code,max_tasks=1)
        if results[code].get('status')=='rate_limited':
            state={**progress,'cursor':cursor,'next_code':background,
                   'status':'rate_limited','updated_at':now(),'last_results':results}
            store.put('dart-full-report-progress',state)
            return state
    state={'cursor':(cursor+1)%len(codes),'next_code':codes[(cursor+1)%len(codes)],
           'visits':visit+1,'schedule_version':2,
           'active_cursor':int(progress.get('active_cursor',0))+(1 if priority else 0),
           'status':'collecting','updated_at':now(),'last_results':results}
    store.put('dart-full-report-progress',state)
    return state


async def financial_reports_loop(source):
    from .storage_budget import collection_capacity
    from .authenticated_sources import DartDailyBudgetExceeded
    while True:
        delay=5
        try:
            if collection_capacity(source.store, 'financial-reports')['limited']:
                delay=60
            else:
                result=await step(source)
                if result['status']=='waiting': delay=60
                elif result['status']=='rate_limited': delay=300
        except DartDailyBudgetExceeded:
            delay=300
        except Exception as exc:
            logging.getLogger('market-worker').warning('Financial queue: %s',type(exc).__name__)
            delay=60
        await asyncio.sleep(delay)
