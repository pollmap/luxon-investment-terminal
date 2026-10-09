"""Bounded job-metadata audit, without decoding decades of price snapshots."""
import json
from collections import Counter
from services.market_dashboard.store import Store, now
from services.market_dashboard.toss_backfill import KINDS

store=Store()
codes=set(store.listed_codes())
jobs=store.list_prefix('toss-backfill:')
report={'at':now(),'universe':len(codes),'basis':'resumable_collection_job_metadata','kinds':{}}
for kind in KINDS:
    states={key.split(':')[1]:value for key,value in jobs.items()
            if key.endswith(':'+kind) and key.split(':')[1] in codes}
    counts=Counter()
    for state in states.values():
        if state.get('status')=='error':
            outcome='authentication_error' if state.get('http_status') in (401,403) else 'provider_404_unresolved' if state.get('http_status')==404 else 'request_or_format_error'
        elif state.get('status')=='not_in_provider_catalogue':
            outcome='provider_catalogue_omission'
        elif state.get('status')=='no_data':
            outcome='source_empty'
        elif state.get('backfill_complete') or (kind in ('history','raw-history') and state.get('status')=='ready' and not state.get('next_before')):
            outcome='last_page_received'
        else:
            outcome='older_pages_pending'
        counts[outcome]+=1
    counts['no_collection_job']=len(codes)-len(states)
    report['kinds'][kind]={'securities':dict(counts),'reported_rows':sum(r.get('rows',0) for r in states.values()),
                         'last_page_fraction':counts['last_page_received']/len(codes) if codes else 0,
                         'provider_catalogue_omission_fraction':counts['provider_catalogue_omission']/len(codes) if codes else 0,
                         'error_types':dict(Counter(f"{v.get('error')}:{v.get('http_status')}" for v in states.values() if v.get('status')=='error')),
                         'error_samples':[{k:v.get(k) for k in ('error','http_status','failures','attempted_at')}|{'code':c} for c,v in states.items() if v.get('status')=='error'][:5]}
report['related_company_repair']={key.split(':',1)[1]:{'rows':len(value.get('rows',[])),'reason':value.get('reason')}
    for key,value in store.list_prefix('corporate-event-quarantine:').items()}
report['public_events_402340']=len(store.get('events:402340',{}).get('rows',[]))
report['progress']=store.get('toss-backfill-progress',{})
print(json.dumps(report,ensure_ascii=False))
