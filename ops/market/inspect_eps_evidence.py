"""Read-only accounting evidence inspection, never reads credentials."""
import json
from collections import defaultdict
from services.market_dashboard.store import Store
from services.market_dashboard.fact_store import FactStore
from services.market_dashboard.eps_calculation import calculate_ttm_eps

store=Store()
corp=store.get('dart-corporations',{}).get('codes',{}).get('005930')
facts=FactStore(store).analysis_facts(corp)
calculated=calculate_ttm_eps(facts)
groups=defaultdict(list)
for fact in facts:
    if fact.get('account_id','').rsplit('}',1)[-1] in ('WeightedAverageShares','BasicEarningsLossPerShare','ProfitLossAttributableToOwnersOfParent'):
        if fact.get('period_end','')>='2023-01-01':
            key=(fact.get('basis'),fact.get('period_start'),fact.get('period_end'),fact.get('filing_id'))
            groups[str(key)].append({k:fact.get(k) for k in ('account_id','value','unit','decimals','dimensions','published_at','source_trace')})
print(json.dumps({'inputs':calculated['inputs'],'ttm':calculated['rows'],'rejected':calculated['rejected'],'groups':dict(groups)},ensure_ascii=False))
