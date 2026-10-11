"""Read-only audit of persisted, public financial inputs; never reads credentials."""
import json
from collections import Counter
from services.market_dashboard.store import Store, now

s = Store()
result = {'checked_at': now(), 'universe': len(s.listed_codes()), 'companies': {}}
result['backfill'] = s.get('toss-backfill-progress', {})
for code in ('005930', '000660', '005935', '035420'):
    o = s.get('official:' + code, {})
    facts = o.get('xbrl_facts', [])
    selected = [f for f in facts if any(n in f.get('name','') for n in
                ('Earnings', 'WeightedAverage', 'OrdinaryShares', 'CommonShares'))]
    result['companies'][code] = {
        'xbrl_count': len(facts), 'names': dict(Counter(f['name'] for f in selected)),
        'facts': selected, 'company': o.get('company'),
        'shares': o.get('report_facts', {}).get('shares') if isinstance(o.get('report_facts'),dict) else None,
        'price_metadata': {k:v for k,v in s.get(f'bars:{code}:통합:day',{}).items() if k not in ('rows','source_trace')},
        'per_share': o.get('per_share_facts',[]),
        'events': s.get('events:' + code, {}),
    }
print(json.dumps(result, ensure_ascii=False, default=str))
