"""Rebuild only source-derived analysis views; preserve facts and source archives."""
import argparse
import hashlib
from bs4 import BeautifulSoup
import httpx
from services.market_dashboard.store import Store, now
from services.market_dashboard.fact_store import FactStore


def samsung_share_evidence(store):
    url='https://www.samsung.com/global/ir/stock-information/listing-Info/'
    response=httpx.get(url,timeout=30,follow_redirects=True)
    response.raise_for_status()
    soup=BeautifulSoup(response.text,'html.parser')
    text=' '.join(soup.stripped_strings)
    table=next((t for t in soup.find_all('table') if 'recent 8 years' in t.get_text(' ',strip=True)),None)
    if table is None or 'KR7005930003' not in text or 'May 4, 2018' not in text or '50:1' not in text:
        raise ValueError('Issuer share history changed; review required')
    rows=[[c.get_text(' ',strip=True) for c in r.find_all(['th','td'])] for r in table.find_all('tr')]
    history={r[0]:r[1].replace(',','') for r in rows if len(r)>=2}
    # This is a reviewed interval, not an evergreen attestation. An unknown
    # future action or a changed issuer table cannot silently extend it.
    expected={**{str(y):'5969782550' for y in range(2019,2025)},
              '2025':'5919637922','Mar 31, 2026':'5919637922','Jun 30, 2026':'5846278608'}
    if any(history.get(k)!=v for k,v in expected.items()):
        raise ValueError('Issuer share history needs renewed review')
    if 'Feb 20, 2025' not in text or 'April 2, 2026' not in text or 'Cancellation' not in text:
        raise ValueError('Treasury cancellation evidence missing')
    digest=store.raw('issuer-share-history',url,response.content)
    return {'security_code':'005930','company_id':'00126380','basis_id':'KR7005930003:post-20180504:review-20260630',
        'verified':True,'no_split_interval':True,'start':'2019-01-01','end':'2026-06-30',
        'review_note':'Issuer eight-year capital history; 2025/2026 changes are treasury cancellation. No extrapolation beyond 2026-06-30; raw prices only.',
        'source_trace':{'source':'samsung_ir','source_url':url,'sha256':digest,'reviewed_at':now()}}


def run(codes, share_evidence=False):
    store=Store(); facts=FactStore(store)
    mapping=store.get('dart-corporations',{}).get('codes',{})
    if share_evidence:
        evidence=samsung_share_evidence(store)
        store.put('share-basis:005930',evidence)
    for code in codes:
        corp=mapping.get(code)
        if not corp:
            print(code,'no_corporation');continue
        # Stop the worker during a bulk reprojection so two read/modify/write
        # operations cannot overwrite each other's newer official snapshot.
        official=store.get('official:'+code,{},use_redis=False)
        if not official:
            print(code,'no_official_source');continue
        official['xbrl_facts']=facts.analysis_facts(corp)
        official['xbrl_projection_version']=4
        evidence=store.get('share-basis:'+code,{})
        if evidence:
            official['share_basis_evidence']=evidence
        store.put('official:'+code,official)
        from services.market_dashboard.official_views import read_view
        result=read_view(store,code,'analysis')
        print(code,{m:len([r for r in result['fundamental_metrics']['rows'] if r['metric']==m]) for m in sorted({r['metric'] for r in result['fundamental_metrics']['rows']})},
              'verified_ttm_eps',len([r for r in result['ttm_eps']['rows'] if r['status']=='ready']))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('codes',nargs='+');p.add_argument('--samsung-share-evidence',action='store_true');args=p.parse_args()
    run(args.codes,args.samsung_share_evidence)
