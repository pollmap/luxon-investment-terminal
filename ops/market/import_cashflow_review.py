"""Reviewed Samsung annual cash-flow bridge, exact filing and periods only."""
import argparse
import httpx
from bs4 import BeautifulSoup
from services.market_dashboard.store import Store,now

URL='https://dart.fss.or.kr/report/viewer.do?rcpNo=20260310002820&dcmNo=11104488&eleId=24&offset=587836&length=47267&dtd=dart4.xsd'
NOTE='https://dart.fss.or.kr/report/viewer.do?rcpNo=20260310002820&dcmNo=11104488&eleId=52&offset=2073383&length=76480&dtd=dart4.xsd'
EXPECTED={
    '영업활동현금흐름':['85,315,148','72,982,621'],
    '이자의 지급':['(469,730)','(675,049)'],
    '유형자산의 취득':['(47,522,179)','(51,406,355)'],
    '무형자산의 취득':['(4,630,970)','(2,335,284)'],
    '단기차입금의 순증가(감소)':['4,655,349','5,871,346'],
    '장기차입금의 차입':['2,807,001','404,954'],
    '사채 및 장기차입금의 상환':['(2,852,971)','(1,364,508)'],
}


def run(apply=False):
    s=Store();r=httpx.get(URL,timeout=25);r.raise_for_status()
    soup=BeautifulSoup(r.content,'html.parser')
    all_text=soup.get_text(' ',strip=True)
    if '2025.01.01' not in all_text or '2024.12.31' not in all_text or '단위 : 백만원' not in all_text:
        raise ValueError('Cash-flow report scope changed')
    rows=[[c.get_text(' ',strip=True) for c in tr.find_all(['td','th'])] for tr in soup.find_all('tr')]
    for label,expected in EXPECTED.items():
        found=[row for row in rows if len(row)>=3 and row[0].split(' (주')[0]==label]
        if len(found)!=1 or found[0][1:3]!=expected:
            raise ValueError('Cash-flow line requires renewed review: '+label)
    note=httpx.get(NOTE,timeout=25);note.raise_for_status()
    text=BeautifulSoup(note.content,'html.parser').get_text(' ',strip=True)
    if not all(t in text for t in ['1,284,792','1,184,788','239,243','223,938','리스부채에 대한 원금 상환','리스부채에 대한 이자비용']):
        raise ValueError('Lease classification evidence changed')
    digest=s.raw('issuer-cashflow-review',URL,r.content)
    note_hash=s.raw('issuer-cashflow-review',NOTE,note.content)
    policy={'company_id':'00126380','filing_id':'20260310002820','basis':'CFS','verified':True,
        'periods':[['2024-01-01','2024-12-31'],['2025-01-01','2025-12-31']],
        'all_interest_in_ocf':True,'lease_principal_in_debt_repaid':True,'complete_debt_cashflows':True,
        'scope':'company_total_per_weighted_ordinary_share',
        'source_trace':{'source':'dart_issuer_report','source_url':URL,'sha256':digest,
            'classification_note':{'source_url':NOTE,'sha256':note_hash},'reviewed_at':now()},
        'note':'Statement debt cash movements include lease principal; note reconciles long debt net cash and separately discloses leases. Interest paid is in OCF; no second lease or interest deduction.'}
    if apply:
        official=s.get('official:005930',{},use_redis=False)
        reviews=[p for p in official.get('cashflow_reviews',[]) if p.get('filing_id')!=policy['filing_id'] or p.get('basis')!='CFS']
        official['cashflow_reviews']=[*reviews,policy]
        s.put('official:005930',official)
    print('005930 FCFE CFS 2024/2025','saved' if apply else 'validated')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');args=p.parse_args();run(args.apply)
