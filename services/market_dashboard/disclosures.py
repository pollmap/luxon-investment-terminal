"""Bounded, resumable one-year disclosure pagination with immutable raw archives."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json
from .providers import trace
from .store import now


async def collect_filings(auth, code, corp, pages_per_run=3):
    key = 'filings:' + code
    previous = auth.store.get(key, {})
    today = datetime.now(ZoneInfo('Asia/Seoul')).date()
    progress = previous.get('pagination', {})
    continuing = previous.get('status') == 'partial' and progress.get('next_page')
    end = progress['end'] if continuing else today.strftime('%Y%m%d')
    begin = progress['begin'] if continuing else (today-timedelta(days=365)).strftime('%Y%m%d')
    page = int(progress['next_page']) if continuing else 1
    rows = {row['receipt']:row for row in previous.get('rows', []) if row.get('receipt') and begin<=row.get('date','')<=end}
    url = 'https://opendart.fss.or.kr/api/list.json'
    for _ in range(pages_per_run):
        query = {'crtfc_key':auth.dart,'corp_code':corp,'bgn_de':begin,'end_de':end,'page_no':str(page),
                 'page_count':'100','sort':'date','sort_mth':'desc','last_reprt_at':'N'}
        response = await auth._dart_get(url,params=query)
        response.raise_for_status()
        payload = response.json()
        if payload.get('status') not in ('000','013'):
            raise ValueError('DART filing status: '+str(payload.get('status')))
        total = int(payload.get('total_count',0))
        total_pages = int(payload.get('total_page',1))
        source_url = f'{url}?corp_code={corp}&bgn_de={begin}&end_de={end}&page_no={page}'
        digest = auth.store.raw('dart',source_url,json.dumps(payload).encode())
        for item in payload.get('list',[]):
            receipt = item['rcept_no']
            rows[receipt] = {'title':item['report_nm'],'date':item['rcept_dt'],'receipt':receipt,
                            'url':'https://dart.fss.or.kr/dsaf001/main.do?rcpNo='+receipt,
                            'source_trace':trace('opendart',source_url,digest,item['rcept_dt'],unit='공시',scope='최근 1년 · 정정 포함')}
        complete = page>=total_pages and len(rows)>=total
        next_page = page+1 if page<total_pages else 1
        snapshot = {'rows':sorted(rows.values(),key=lambda row:(row['date'],row['receipt']),reverse=True),
                    'status':('ready' if rows else 'no_data') if complete else 'partial',
                    'received_at':now(),'total':total,'scope':'최근 1년 · 정정 포함',
                    'pagination':{'begin':begin,'end':end,'next_page':next_page,'total_pages':total_pages,'complete':complete}}
        auth.store.put(key,snapshot)
        if complete or page>=total_pages:
            return snapshot
        page=next_page
    return snapshot
