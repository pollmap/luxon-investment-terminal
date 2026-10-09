"""Read-only trading history with explicit market scope and missing observations."""
from datetime import date
from .toss import numeric

INVESTORS = [('individual','개인'),('foreigner','외국인'),('institution','기관'),('otherCorporation','기타법인')]
INSTITUTIONS = [('financialInvestment','금융투자'),('insurance','보험'),('trust','투신'),
                ('privateEquityFund','사모펀드'),('bank','은행'),('otherFinancialInstitution','기타금융'),('pensionFund','연기금')]


def read_history(store, key, start=None, end=None):
    snapshot = store.metadata(key, {})
    if snapshot.get('_series_ref'):
        return {**snapshot, 'rows':store.time_series.rows(snapshot['_series_ref'],start=start,end=end)}
    return snapshot


def trading_history(snapshot, kind, metric='netBuyVolume', start=None, end=None):
    if kind not in ('investor-trading','program-trades') or metric not in ('netBuyVolume','buyVolume','sellVolume'):
        raise ValueError('Unsupported history selection')
    for bound in (start,end):
        if bound: date.fromisoformat(bound)
    if start and end and start > end:
        raise ValueError('Start must precede end')
    groups = INVESTORS + INSTITUTIONS if kind=='investor-trading' else [('total','전체'),('arbitrage','차익'),('nonArbitrage','비차익')]
    rows=[]
    for record in snapshot.get('rows',[]):
        day=record['date']
        if (start and day < start) or (end and day > end): continue
        row={'date':day}
        for key,_ in groups:
            if kind=='investor-trading' and key in dict(INSTITUTIONS):
                obj=((record.get('institution') or {}).get('breakdown') or {}).get(key) or {}
            else: obj=record.get(key) or {}
            row[key]=numeric(obj.get(metric))
        if kind=='program-trades':
            row['total']=row['arbitrage']+row['nonArbitrage'] if row['arbitrage'] is not None and row['nonArbitrage'] is not None else None
        else:
            holding=numeric((record.get('foreignerHolding') or {}).get('holdingRate'))
            row['holding']=holding*100 if holding is not None else None
        rows.append(row)
    rows.sort(key=lambda r:r['date'])
    return {'rows':rows,'columns':[{'key':key,'label':label,'unit':'주'} for key,label in groups],
            'metric':metric,'venue':'통합' if kind=='investor-trading' else 'KRX',
            'status':snapshot.get('status','pending'), 'history_complete':bool(snapshot.get('backfill_complete')),
            'available_start':snapshot.get('first_time') or min((r['date'] for r in snapshot.get('rows',[])),default=None),
            'available_end':snapshot.get('last_time') or max((r['date'] for r in snapshot.get('rows',[])),default=None)}
