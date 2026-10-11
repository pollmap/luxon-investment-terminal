"""Historical regular-report publication dates, separate from the news feed."""
from datetime import date, datetime, timedelta, UTC
from .store import now
import re


def queue_index_reports(store, code):
    """Queue historical filings without guessing fiscal-quarter codes from months."""
    key = 'filing-index:' + code
    revision = store.revisions((key,)).get(key)
    checkpoint = 'xbrl-index-discovered:' + code
    if revision is None or store.get(checkpoint, {}).get('revision') == revision:
        return 0
    index = store.get(key, {})
    added, unresolved = 0, []
    for row in index.get('rows', []):
        receipt, title = str(row.get('receipt', '')), row.get('title', '')
        # Main filings and amended main filings only, not amended attachments.
        title = re.sub(r'^\[(?:기재)?정정\]\s*', '', title).strip()
        kind = re.fullmatch(r'(사업보고서|반기보고서|분기보고서)\s*\(\d{4}\.\d{2}\)', title)
        if not kind or not re.fullmatch(r'\d{14}', receipt) or not row.get('source_trace'):
            continue
        if store.get('xbrl-request:' + receipt):
            continue
        report = {'사업보고서':'11011', '반기보고서':'11012'}.get(kind[1])
        if not report:
            # A March quarter need not be Q1 for a non-December fiscal year.
            # Existing statement responses supply the verified quarterly code.
            unresolved.append(receipt)
            continue
        store.put('xbrl-request:' + receipt, {'code':code, 'filing_id':receipt,
            'report_code':report, 'requested_at':now(), 'discovered_from':key,
            'source_trace':row['source_trace']})
        added += 1
    store.put(checkpoint, {'revision':revision, 'added':added,
        'quarter_identity_pending':unresolved, 'updated_at':now()})
    return added


async def collect_periodic_index(source, code):
    previous = source.store.get('filing-index:' + code, {})
    if (previous.get('retry_after') or '') > now():
        return previous
    if not source.dart:
        return {'status': 'credentials_missing'}
    try:
        return await _collect_periodic_index(source, code)
    except Exception as exc:
        # Preserve the last good rows and pagination cursor for retry.
        state = {**previous, 'last_error': type(exc).__name__,
                 'retry_after': (datetime.now(UTC) + timedelta(hours=1)).isoformat()}
        source.store.put('filing-index:' + code, state)
        return state


async def _collect_periodic_index(source, code):
    store = source.store
    key = 'filing-index:' + code
    previous = store.get(key, {})
    if previous.get('status') in ('ready','no_data') and previous.get('received_at') and previous.get('pagination',{}).get('begin') == '20150101':
        if (datetime.fromisoformat(now())-datetime.fromisoformat(previous['received_at'])).total_seconds() < 7*86400:
            return previous
    corp = (await source.corporations()).get(code)
    if not corp:
        return {'status':'company_identity_required'}
    today = date.today()
    progress = previous.get('pagination', {})
    pending = previous.get('status') == 'partial' and progress.get('begin') == '20150101'
    page = progress.get('next_page',1) if pending else 1
    begin = progress.get('begin') if pending else '20150101'
    end = progress.get('end') if pending else today.strftime('%Y%m%d')
    url = 'https://opendart.fss.or.kr/api/list.json'
    query = {'crtfc_key':source.dart,'corp_code':corp,'bgn_de':begin,'end_de':end,
             'pblntf_ty':'A','last_reprt_at':'N','page_no':str(page),'page_count':'100',
             'sort':'date','sort_mth':'desc'}
    response = await source._dart_get(url,params=query)
    response.raise_for_status()
    payload = response.json()
    if payload.get('status') not in ('000','013'):
        raise ValueError('DART filing index status '+str(payload.get('status')))
    digest = store.raw('dart',f'{url}?corp_code={corp}&pblntf_ty=A&bgn_de={begin}&end_de={end}&page_no={page}',response.content)
    rows = {r['receipt']:r for r in previous.get('rows',[])}
    for r in payload.get('list',[]):
        if r.get('corp_code') != corp:
            raise ValueError('Filing corporation mismatch')
        datetime.strptime(r['rcept_dt'], '%Y%m%d')
        rows[r['rcept_no']] = {'receipt':r['rcept_no'],'date':r['rcept_dt'],'title':r['report_nm'],
            'source_trace':{'source':'opendart','sha256':digest,'available_at':r['rcept_dt']}}
    total_pages = int(payload.get('total_page') or 1)
    result = {'rows':sorted(rows.values(),key=lambda r:r['receipt']),
              'status':('ready' if rows else 'no_data') if page>=total_pages else 'partial',
              'received_at':now(),'pagination':{'begin':begin,'end':end,'next_page':page+1}}
    store.put(key,result)
    queue_index_reports(store, code)
    return result
