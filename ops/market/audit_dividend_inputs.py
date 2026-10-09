"""Read-only, actionable dividend evidence inventory. No secrets in output."""
import json
from collections import Counter
from services.market_dashboard.store import Store, now


def audit(store):
    totals=Counter(); companies=[]
    for key,snapshot in store.list_prefix('events:').items():
        counts=Counter(); rights=Counter()
        for row in snapshot.get('rows',[]):
            if row.get('kind')!='dividend' or row.get('superseded_by'):
                continue
            counts['active_dividends']+=1
            if not row.get('ex_date'): counts['ex_date_missing']+=1
            if row.get('payment_status')!='confirmed': counts['payment_confirmation_missing']+=1
            if not row.get('payment_date'): counts['payment_date_missing']+=1
            if row.get('verified'): counts['verified_dividends']+=1
            rights[(row.get('share_class'),row.get('record_date'))]+=1
        counts['duplicate_right_groups']=sum(v>1 for v in rights.values())
        totals.update(counts)
        companies.append({'code':key.split(':',1)[1],**counts,
                          'coverage_verified':bool(snapshot.get('coverage',{}).get('verified'))})
    return {'checked_at':now(),'securities_with_event_snapshot':len(companies),
            'totals':dict(totals),'companies':companies}


if __name__=='__main__':
    print(json.dumps(audit(Store()),ensure_ascii=False))
