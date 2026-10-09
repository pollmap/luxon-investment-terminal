"""Reviewed 2017/2018 EPS note bridge. Raw XBRL unit errors stay unchanged."""
import argparse
from decimal import Decimal
import httpx
from bs4 import BeautifulSoup
from services.market_dashboard.store import Store, now
from services.market_dashboard.fact_store import FactStore
from services.market_dashboard.financial_dates import publication_date

BASE = 'https://dart.fss.or.kr/report/viewer.do?'
SPECS = [
    ('20180402005019',2017,'rcpNo=20180402005019&dcmNo=6060273&eleId=14&offset=758246&length=591881&dtd=dart3.xsd',
     {'당기순이익 중 보통주 해당분':['36,323,723','19,647,199'],
      '÷ 가중평균유통보통주식수':['121,132','124,375'],
      '기본 보통주 주당이익(단위: 원)':['299,868','157,967']},'2018-04-02'),
    ('20190401004781',2018,'rcpNo=20190401004781&dcmNo=6616741&eleId=14&offset=745724&length=586931&dtd=dart3.xsd',
     {'당기순이익 중 보통주 해당분':['38,573,066','36,323,723'],
      '÷ 가중평균유통보통주식수':['5,970,448','6,056,616'],
      '기본 보통주 주당이익(단위: 원)':['6,461','5,997']},'2018-05-03'),
    ('20170331004518',2016,'rcpNo=20170331004518&dcmNo=5540271&eleId=14&offset=614651&length=522543&dtd=dart3.xsd',
     {'당기순이익 중 보통주 해당분':['19,647,199','16,317,275'],
      '÷ 가중평균유통보통주식수':['124,375','129,190'],
      '기본 보통주 주당이익(단위: 원)':['157,967','126,305']},'2017-03-31')]
ACTION = BASE+'rcpNo=20190401004781&dcmNo=6616741&eleId=7&offset=247247&length=63793&dtd=dart3.xsd'
PREFIX = '{urn:korea-stocks:reviewed-eps-note}'
UNITS = {'shares':'{http://www.xbrl.org/2003/instance}shares','won':'{http://www.xbrl.org/2003/iso4217}KRW'}


def parse_note(content, expected):
    soup = BeautifulSoup(content, 'html.parser')
    text = soup.get_text(' ', strip=True)
    if '(단위: 백만원, 천주)' not in text:
        raise ValueError('Note unit requires renewed review')
    tables = [t for t in soup.find_all('table') if '÷ 가중평균유통보통주식수' in t.get_text(' ',strip=True)]
    if len(tables) != 1:
        raise ValueError('Ordinary-share EPS note ambiguous')
    rows = [[c.get_text(' ',strip=True) for c in tr.find_all(['td','th'])] for tr in tables[0].find_all('tr')]
    result = {}
    for label, values in expected.items():
        matches = [r for r in rows if r and r[0] == label]
        if len(matches) != 1 or matches[0][1:] != values:
            raise ValueError('Reviewed note changed: '+label)
        result[label] = [Decimal(v.replace(',','')) for v in values]
    return result


def run(apply=False):
    store = Store(); prior = store.get('share-basis:005930',{})
    if prior.get('complete_split_history'):
        intervals = prior['unchanged_intervals']
    else:
        intervals = [prior]
    if not any(i.get('verified') and i.get('start')=='2019-01-01' and i.get('end')=='2026-06-30' for i in intervals):
        raise ValueError('Existing bounded post-split history required')
    index = store.get('filing-index:005930',{}).get('rows',[])
    facts, reviews, traces, parsed = [], [], [], []
    for receipt, year, query, expected, unit_date in SPECS:
        publication = next((r['date'] for r in index if r['receipt']==receipt),None)
        published,_ = publication_date({'published_at':publication})
        if not published:
            raise ValueError('Actual filing publication date required')
        url=BASE+query; response=httpx.get(url,timeout=35);response.raise_for_status()
        rows=parse_note(response.content,expected);parsed.append(rows)
        digest=store.raw('dart-reviewed-eps-note',url,response.content)
        trace={'source':'dart_reviewed_note','source_url':url,'sha256':digest,'filing_id':receipt,
            'company_id':'00126380','available_at':published,'mapping_version':'reviewed-eps-note-v1'}
        traces.append(trace)
        for column, period_year in enumerate((year,year-1)):
            start,end=f'{period_year}-01-01',f'{period_year}-12-31'
            reviews.append({'verified':True,'filing_id':receipt,'basis':'CFS','period_start':start,
                'period_end':end,'unit_as_of':unit_date,'source_trace':trace})
            for label,concept,scale,unit,decimals in (
                ('당기순이익 중 보통주 해당분','ProfitLossAttributableToOrdinaryEquityHoldersOfParentEntity',10**6,UNITS['won'],-6),
                ('÷ 가중평균유통보통주식수','WeightedAverageShares',1000,UNITS['shares'],-3),
                ('기본 보통주 주당이익(단위: 원)','BasicEarningsLossPerShare',1,UNITS['won']+'/'+UNITS['shares'],0)):
                facts.append({'company_id':'00126380','filing_id':receipt,'account_id':PREFIX+concept,
                    'basis':'CFS','period_start':start,'period_end':end,'period_verified':True,
                    'value':str(rows[label][column]*scale),'unit':unit,'currency':'KRW',
                    'decimals':str(decimals),'dimensions':{},'published_at':published,
                    'mapping_verified':True,'source_account_label':label,'source_trace':trace})
    # Restated 2017 amounts must match within the two documents' rounding units.
    old,new=parsed[:2]
    if abs(old['기본 보통주 주당이익(단위: 원)'][0]/50-new['기본 보통주 주당이익(단위: 원)'][1]) > Decimal('.51'):
        raise ValueError('Restated EPS does not reconcile')
    if abs(old['÷ 가중평균유통보통주식수'][0]*50-new['÷ 가중평균유통보통주식수'][1]) > Decimal('25.5'):
        raise ValueError('Restated shares do not reconcile')
    response=httpx.get(ACTION,timeout=25);response.raise_for_status()
    text=BeautifulSoup(response.content,'html.parser').get_text(' ',strip=True)
    if not all(t in text for t in ('2018년 5월 3일','5,000원','50주','100원','5,969,782,550','자사주소각')):
        raise ValueError('Share action requires renewed review')
    action_trace={'source':'dart_issuer_report','source_url':ACTION,
        'sha256':store.raw('issuer-share-review',ACTION,response.content),'reviewed_at':now(),
        'linked_notes':traces,'post_split_intervals':[i['source_trace'] for i in intervals]}
    evidence={'security_code':'005930','company_id':'00126380','verified':True,'complete_split_history':True,
        'start':'2016-01-01','end':'2026-06-30','basis_date':'2026-06-30',
        'basis_id':'005930:split-history:201601-202606:v1','source_trace':action_trace,
        'splits':[{'kind':'split','effective_date':'2018-05-03','new_shares':'50','old_shares':'1',
            'verified':True,'source_trace':action_trace}],
        'fact_unit_reviews':reviews,'unchanged_intervals':intervals}
    if apply:
        inserted=FactStore(store).append('00126380',facts)
        store.put('share-basis-review-history:005930:'+prior['basis_id'],prior)
        store.put('share-basis:005930',evidence)
        print('facts_inserted',inserted)
    print('reviewed annual notes',len(facts),'periods',len(reviews),'applied',apply)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');run(p.parse_args().apply)
