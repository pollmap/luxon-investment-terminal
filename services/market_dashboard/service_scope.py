"""Public product scope; raw observations outside this scope stay preserved."""
VERSION=2

# KIND classifies these as listed trust units, not ordinary/preferred company
# shares. Keep their historical observations and source listings untouched.
REVIEWED_NON_SHARE_INSTRUMENTS={
    '0106J0': {
        'name':'대신 KOSPI200인덱스 X클래스',
        'kind':'listed_fund_unit',
        'source_url':'https://kind.krx.co.kr/external/2025/10/24/000676/20251024001580/69804.htm',
    },
    '0120X0': {
        'name':'유진 챔피언중단기크레딧 X클래스',
        'kind':'listed_fund_unit',
        'source_url':'https://kind.krx.co.kr/external/2025/10/24/000675/20251024001579/69804.htm',
    },
}

def included(row):
    return row.get('market')!='KONEX' and row.get('code') not in REVIEWED_NON_SHARE_INSTRUMENTS

def universe_view(snapshot):
    rows=snapshot.get('rows',[])
    excluded_by_market={}
    for row in rows:
        if row.get('market')!='KONEX' and row.get('code') in REVIEWED_NON_SHARE_INSTRUMENTS:
            market=row['market']
            excluded_by_market[market]=excluded_by_market.get(market,0)+1
    coverage=[]
    for item in snapshot.get('coverage',[]):
        if item.get('market')=='KONEX':
            continue
        excluded=excluded_by_market.get(item.get('market'),0)
        coverage.append({**item,'eligible_rows':max(0,int(item.get('received',0))-excluded),
            'excluded_non_share_instruments':excluded})
    return {**snapshot,'rows':[r for r in rows if included(r)],
        'coverage':coverage,
        'unavailable_markets':[m for m in snapshot.get('unavailable_markets',[]) if m!='KONEX'],
        'excluded_markets':['KONEX'],'excluded_instruments':REVIEWED_NON_SHARE_INSTRUMENTS,
        'scope_version':VERSION}
