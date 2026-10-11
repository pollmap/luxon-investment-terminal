"""Import two reviewed issuer histories, bounded to their reporting date.

The capital table alone does not prove a no-split interval. The review includes
the cumulative issuance/reduction table and the stated cancellation reasons.
Changed report text requires another review; no extrapolation to today's date.
"""
import argparse
import re
import httpx
from bs4 import BeautifulSoup
from services.market_dashboard.store import Store, now

REVIEWS = {
    '000660': {'corp':'00164779','name':'SK하이닉스','receipt':'20260317000635',
        'start':'2021-01-01','end':'2025-12-31',
        'capital':['728,002,365','5,000','최근5년간자본금변동사항이없습니다'],
        'shares':['2003.03.31','주식병합(21:1)','728,002,365','4,993,977,844'],
        'note':'Five-year capital-change statement and cumulative reduction table; historical 2003 consolidation is outside this interval.'},
    '005380': {'corp':'00164742','name':'현대자동차','receipt':'20260318001394',
        'start':'2021-12-31','end':'2025-12-31',
        'capital':['2025년말','2021년말','204,757,766','209,416,191','211,531,506','213,668,187','5,000'],
        'shares':['26,838,713','자기주식소각','2023년4월25일','2025년에는','2.2%'],
        'note':'Five year-end ordinary-share/par-value table and cumulative stock reductions identify treasury cancellations. Preferred classes are not authorized by this review.'},
}


def compact(value):
    return re.sub(r'\s+','',value)


def load_review(store, code):
    spec=REVIEWS[code]
    url='https://dart.fss.or.kr/dsaf001/main.do?rcpNo='+spec['receipt']
    root=httpx.get(url,timeout=25,follow_redirects=True);root.raise_for_status()
    if spec['name'] not in root.text:
        raise ValueError('Issuer identity changed')
    root_hash=store.raw('issuer-share-review',url,root.content)
    sections={}
    for block in re.split(r'var node\d+ = \{\};',root.text):
        fields=dict(re.findall(r"node\d+\['(\w+)'\]\s*=\s*\"([^\"]*)\"",block))
        role={'3. 자본금 변동사항':'capital','4. 주식의 총수 등':'shares'}.get(fields.get('text'))
        if not role:
            continue
        if fields.get('rcpNo') != spec['receipt']:
            raise ValueError('Filing changed')
        params={k:fields[k] for k in ('rcpNo','dcmNo','eleId','offset','length','dtd')}
        response=httpx.get('https://dart.fss.or.kr/report/viewer.do',params=params,timeout=25)
        response.raise_for_status()
        text=compact(BeautifulSoup(response.content,'html.parser').get_text(' ',strip=True))
        if not all(compact(token) in text for token in spec[role]):
            raise ValueError('Share history needs renewed review: '+code+':'+role)
        digest=store.raw('issuer-share-review',str(response.url),response.content)
        sections[role]={'source_url':str(response.url),'sha256':digest}
    if set(sections)!={'capital','shares'}:
        raise ValueError('Reviewed sections missing')
    return {'security_code':code,'company_id':spec['corp'],'verified':True,'no_split_interval':True,
        'start':spec['start'],'end':spec['end'],'basis_id':code+':review:'+spec['receipt'],
        'review_note':spec['note'],'source_trace':{'source':'dart_issuer_report',
            'source_url':url,'sha256':root_hash,'filing_id':spec['receipt'],
            'reviewed_at':now(),'sections':sections}}


def run(codes, apply=False):
    store=Store()
    for code in codes:
        evidence=load_review(store,code)
        if apply:
            previous=store.get('share-basis:'+code,{})
            if previous:
                store.put('share-basis-review-history:'+code+':'+previous['basis_id'],previous)
            store.put('share-basis:'+code,evidence)
        print(code,evidence['start'],evidence['end'],'saved' if apply else 'validated')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('codes',nargs='+',choices=list(REVIEWS));p.add_argument('--apply',action='store_true')
    args=p.parse_args();run(args.codes,args.apply)
