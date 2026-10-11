import os,json
from services.market_dashboard.store import Store
from services.market_dashboard.runtime import load_worker_credentials
from services.market_dashboard.fact_store import FactStore
from services.market_dashboard.eps_calculation import calculate_ttm_eps
load_worker_credentials()
s=Store()
corp=s.get('dart-corporations',{}).get('codes',{}).get('005930')
o=s.get('official:005930',{})
facts=FactStore(s).analysis_facts(corp)
calc=calculate_ttm_eps(facts)
print(json.dumps({'dart_present':bool(os.getenv('DART_API_KEY')),'usage':s.get('dart-usage',{}),
    'corp':corp,'projection':o.get('xbrl_projection_version'),'source_facts':len(o.get('xbrl_facts',[])),
    'database_facts':len(facts),'inputs':len(calc['inputs']),
    'ttm':[{k:r.get(k) for k in ('basis','period_start','period_end','status')} for r in calc['rows']],
    'cursor':s.get('corporate-events-cursor',{}),'corporate_jobs':s.list_prefix('corporate-events-job:'),
    'facts_missing_id':sum(not f.get('fact_id') for f in facts)},ensure_ascii=False))
