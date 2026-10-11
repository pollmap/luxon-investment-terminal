"""DART corporate-action documents, with separate declaration/payment/ex dates."""
import asyncio
import hashlib
import io
import json
import re
import zipfile
from datetime import date, datetime, timedelta

from bs4 import BeautifulSoup

from .analysis import numeric
from .store import now
from .dividend_evidence import with_field_availability

PARSER_VERSION = 'dividend-dates-v4'

def is_issuer_action(title):
    # A listed holding company also files decisions about its subsidiaries.
    # Those distributions belong to the subsidiary's shares, not the filer.
    compact=re.sub(r'\s+','',title)
    return not any(word in compact for word in ('자회사','종속회사','타법인'))

def issuer_events(events, items):
    related={receipt for receipt,item in items.items() if not is_issuer_action(item['title'])}
    rows=events.get('rows',[])
    kept=[row for row in rows if row.get('filing_id') not in related]
    quarantined=[row for row in rows if row.get('filing_id') in related]
    return kept,quarantined

def repair_related_events(store):
    repaired=[]
    for key,index in store.list_prefix('corporate-event-index:').items():
        code=key.split(':',1)[1]
        events=store.get('events:'+code,{})
        kept,quarantined=issuer_events(events,{r['receipt']:r for r in index.get('rows',[])})
        if not quarantined:
            continue
        previous=store.get('corporate-event-quarantine:'+code,{'rows':[]})
        archive={r['id']:r for r in previous['rows']+quarantined}
        store.put('corporate-event-quarantine:'+code,{'rows':list(archive.values()),
            'reason':'related_company_not_security','updated_at':now()})
        store.put('events:'+code,{**events,'rows':kept,'coverage':{'verified':False,
            'reason':'ex_dates_and_all_actions_required'},'repaired_at':now()})
        repaired.append({'code':code,'quarantined':len(quarantined)})
    return repaired

def parse_date(value):
    match=re.search(r'(20\d{2})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})',value)
    return date(*map(int,match.groups())).isoformat() if match else None

def document_text(content):
    if content.startswith(b'PK'):
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            files=[i for i in archive.infolist() if i.filename.lower().endswith(('.xml','.html','.htm'))]
            if sum(f.file_size for f in files)>64*1024**2:
                raise ValueError('Oversized corporate action archive')
            content=b'\n'.join(archive.read(f) for f in files)
    if b'<!ENTITY' in content.upper():
        raise ValueError('Unsupported document entity')
    try:
        return content.decode('utf-8')
    except UnicodeDecodeError:
        return content.decode('cp949')

def parse_dividend_document(content,*,code,filing_id,published_at,evidence,share_label='보통주식'):
    soup=BeautifulSoup(document_text(content),'html.parser')
    tables=[]
    for table in soup.find_all('table'):
        rows=[[c.get_text(' ',strip=True) for c in row.find_all(['td','th','tu'],recursive=False)]
            for row in table.find_all('tr') if row.find_parent('table')==table]
        labels=[re.sub(r'\s+','',cells[0]) for cells in rows if cells]
        # Correction comparisons and preferred-stock annexes also contain
        # "per share" headings. Their last column is not the declared dividend.
        if ('3.1주당배당금(원)' in labels and '6.배당기준일' in labels):
            tables.append(rows)
    if not tables:
        return []
    if len(tables)!=1:
        raise ValueError('Ambiguous dividend decision tables')
    rows=tables[0]
    records={}
    amounts={}
    per_share=False
    for cells in rows:
        if not cells:
            continue
        label=' '.join(cells)
        compact=re.sub(r'\s+','',label)
        if '1주당배당금' in compact:
            per_share=True
        elif re.match(r'^\d+\.',compact):
            per_share=False
        if per_share and any(token in compact for token in ('보통주','우선주','종류주')):
            # Preserve the declared class. A generic 종류주식 amount cannot be
            # assigned to several preferred issues merely from a code suffix.
            share=next((re.sub(r'\s+|\([^)]*\)','',c) for c in cells if any(t in c for t in ('보통주','우선주','종류주'))),None)
            share={'보통주':'보통주식','우선주':'우선주식','종류주':'종류주식'}.get(share,share)
            parsed=numeric(cells[-1].replace(',',''))
            if parsed is not None and parsed>=0:
                if share in amounts and amounts[share]!=parsed:
                    raise ValueError('Conflicting share-class dividends')
                amounts[share]=parsed
        for field,labels in {'record_date':('배당기준일',),'payment_date':('배당금지급예정일',),
                             'confirmed_payment_date':('실제지급일','지급완료일'),
                             'declared_payment_date':('배당금지급일',),'ex_date':('배당락일',)}.items():
            if any(token in compact for token in labels):
                observed=parse_date(cells[-1])
                if observed:
                    if records.get(field) not in (None,observed):
                        raise ValueError('Conflicting dividend dates')
                    records[field]=observed
    amount=amounts.get(share_label)
    if amount is None or not records.get('record_date'):
        return []
    confirmed=records.pop('confirmed_payment_date',None)
    declared=records.pop('declared_payment_date',None)
    payment=confirmed or declared or records.get('payment_date')
    planned=records.get('payment_date')
    if payment:
        records['payment_date']=payment
    if planned:
        records['scheduled_payment_date']=planned
    if records.get('payment_date') and records['payment_date']<records['record_date']:
        raise ValueError('Payment precedes dividend record date')
    if records.get('ex_date') and records['ex_date']>records['record_date']:
        raise ValueError('Ex date follows dividend record date')
    from .financial_dates import publication_date
    published,_=publication_date({'published_at':published_at})
    if confirmed and (not published or confirmed>published[:10]):
        raise ValueError('Payment confirmation predates payment')
    share_class='ordinary' if share_label=='보통주식' else 'preferred'
    identity=hashlib.sha256(f'{code}:{filing_id}:{share_class}:dividend'.encode()).hexdigest()
    verified=bool(records.get('ex_date') and confirmed)
    return [with_field_availability({'id':identity,'security_code':code,'share_class':share_class,'share_label':share_label,'kind':'dividend',
        'amount':str(amount),'currency':'KRW',**records,'date':records.get('payment_date') or records['record_date'],
        'date_kind':'payment' if records.get('payment_date') else 'record',
        'payment_status':'confirmed' if confirmed else 'announced',
        'filing_id':filing_id,'published_at':published_at,'verified':verified,
        'status':'verified' if verified else 'ex_date_required' if not records.get('ex_date') else 'payment_confirmation_required',
        'source_trace':evidence,'parser_version':PARSER_VERSION})]

async def collect_events(source,code,*,archive_only=False):
    store=source.store
    from .preferred_dividends import REVIEWED, project_preferred_dividends
    if code in REVIEWED:
        # The corporation index usually lists ordinary shares only. A named
        # annex gives this security's own amount without another index request.
        project_preferred_dividends(store, code)
        return
    from .split_events import project_split_events
    project_split_events(store, code)
    if not archive_only:
        from .issuer_dividends import collect_issuer_dividends
        await collect_issuer_dividends(source, code)
        from .kind_dividends import collect_kind_dividends
        await collect_kind_dividends(source, code)
    key='corporate-events-job:'+code
    job=store.get(key,{})
    if (job.get('retry_after') or '')>now():
        archive_only=True
    if archive_only:
        from .issuer_dividends import replay_issuer_dividends
        replay_issuer_dividends(store, code)
    observations=store.get('dividend-observations:'+code,{}).get('rows',[])
    observation_revision=hashlib.sha256(json.dumps(observations,sort_keys=True).encode()).hexdigest()
    if not archive_only and job.get('checked_at') and job.get('observation_revision')==observation_revision and job.get('parser_version')==PARSER_VERSION and job.get('complete') and (datetime.fromisoformat(now())-datetime.fromisoformat(job['checked_at'])).total_seconds()<86400:
        return
    previous=store.get('corporate-event-index:'+code,{})
    if archive_only:
        candidates=[]
        for item in previous.get('rows',[]):
            prior=job.get('documents',{}).get(item['receipt'],{})
            if not is_issuer_action(item['title']) or (prior.get('parser_version')==PARSER_VERSION
                    and prior.get('status') in ('parsed','review_required')):
                continue
            raw_hash=prior.get('raw_hash','')
            if re.fullmatch(r'[0-9a-f]{64}',str(raw_hash)):
                path=store.root/'raw'/'dart-events'/(raw_hash+'.bin')
                if path.is_file():
                    with path.open('rb') as archive:
                        if archive.read(2)==b'PK':
                            candidates.append(item['receipt'])
        if not candidates:
            # An index with already parsed documents can still acquire new
            # ex-date/payment observations. Reconcile them without re-fetching
            # filings or promoting the stale index/provider state to current.
            if observations and job.get('observation_revision') != observation_revision:
                from .dividend_evidence import enrich_dividends
                events = store.get('events:' + code, {})
                rows = enrich_dividends(events.get('rows', []), observations)
                if rows != events.get('rows', []):
                    store.put('events:' + code, {**events, 'rows': rows, 'reconciled_at': now()})
                store.put(key, {**job, 'observation_revision': observation_revision,
                    'archive_replay_at': now()})
            return
        # Local interpretation does not require a fresh index or authentication.
        # Its completion must never certify that the provider index is current.
        corp=None
    else:
        corp=(await source.corporations()).get(code)
        if not corp:
            return
    pending=previous.get('status')=='partial'
    index_fresh=previous.get('status')=='ready' and (datetime.fromisoformat(now())-datetime.fromisoformat(previous['received_at'])).total_seconds()<86400
    page=previous.get('next_page',1) if pending else 1
    end=previous.get('end') if pending else date.today().strftime('%Y%m%d')
    # Corporate decisions are exchange disclosures (I), distinct from annual reports (A).
    params={'crtfc_key':getattr(source,'dart',None),'corp_code':corp,'bgn_de':'20150101','end_de':end,
        'pblntf_ty':'I','last_reprt_at':'N','page_no':str(page),'page_count':'100','sort':'date','sort_mth':'desc'}
    url='https://opendart.fss.or.kr/api/list.json'
    if index_fresh or archive_only:
        payload={'status':'013'}
    else:
        response=await source._dart_get(url,params=params)
        response.raise_for_status()
        payload=response.json()
    if payload.get('status') not in ('000','013'):
        raise ValueError('Corporate disclosure index unavailable')
    digest=None if index_fresh or archive_only else store.raw('dart-events',f'{url}?corp_code={corp}&page_no={page}&end_de={end}',response.content)
    items={r['receipt']:r for r in previous.get('rows',[])}
    for row in payload.get('list',[]):
        if row.get('corp_code')!=corp:
            raise ValueError('Corporate event identity mismatch')
        title=row['report_nm']
        if any(word in title for word in ('배당결정','배당 결정','분할결정','병합결정','증자결정','합병결정')):
            items[row['rcept_no']]={'receipt':row['rcept_no'],'date':row['rcept_dt'],'title':title,'index_hash':digest}
    complete=(previous.get('status')=='ready') if archive_only else index_fresh or page>=int(payload.get('total_page') or 1)
    if not index_fresh and not archive_only:
        store.put('corporate-event-index:'+code,{'rows':list(items.values()),'next_page':page+1,'end':end,
            'status':'ready' if complete else 'partial','received_at':now()})
    events=store.get('events:'+code,{'rows':[],'venue':'통합'})
    kept,quarantined=issuer_events(events,items)
    if quarantined:
        # Keep the source and the previous interpretation for a reversible repair.
        quarantine=store.get('corporate-event-quarantine:'+code,{'rows':[]})
        archived={row['id']:row for row in quarantine['rows']+quarantined}
        store.put('corporate-event-quarantine:'+code,{'rows':list(archived.values()),
            'reason':'related_company_not_security','updated_at':now()})
    merged={row['id']:row for row in kept}
    documents=job.get('documents',{})
    # One document per turn keeps financial reports and other companies moving.
    for row in sorted(items.values(),key=lambda r:r['receipt'],reverse=True):
        receipt=row['receipt']
        prior=documents.get(receipt,{})
        if not is_issuer_action(row['title']):
            documents[receipt]={**prior,'status':'related_company_not_security','title':row['title']}
            continue
        if (prior.get('parser_version')==PARSER_VERSION and prior.get('status') in ('parsed','review_required')) or (not archive_only and prior.get('retry_after','')>now()):
            continue
        endpoint='https://opendart.fss.or.kr/api/document.xml'
        raw_hash=prior.get('raw_hash')
        archived=store.root/'raw'/'dart-events'/(str(raw_hash)+'.bin')
        content=None
        if raw_hash and re.fullmatch(r'[0-9a-f]{64}',raw_hash) and archived.is_file():
            content=archived.read_bytes()
            if hashlib.sha256(content).hexdigest()!=raw_hash:
                raise ValueError('Corporate action archive hash mismatch')
            # Error XML is immutable evidence of that attempt, not a filing.
            # Once its retry budget expires, request a new document instead
            # of recycling the cached error forever. Valid ZIPs remain reusable.
            if not content.startswith(b'PK'):
                content=None
        if content is None:
            if archive_only:
                continue
            r=await source._dart_get(endpoint,params={'crtfc_key':source.dart,'rcept_no':receipt})
            r.raise_for_status()
            content=r.content
            raw_hash=store.raw('dart-events',endpoint+'?rcept_no='+receipt,content)
        if not content.startswith(b'PK'):
            if archive_only:
                # A cached API error is not an unavailable filing forever.
                continue
            documents[receipt]={'status':'document_unavailable','raw_hash':raw_hash,
                'retry_after':(datetime.fromisoformat(now())+timedelta(days=1)).isoformat()}
            break
        evidence={'source':'opendart','source_url':endpoint+'?rcept_no='+receipt,'sha256':raw_hash,'filing_id':receipt,'available_at':row['date']}
        parsed=parse_dividend_document(content,code=code,filing_id=receipt,published_at=row['date'],evidence=evidence) if '배당' in row['title'] else []
        if not parsed:
            # A stricter parser must not leave its old interpretation public.
            invalid=[event for event in merged.values() if event.get('filing_id')==receipt]
            if invalid:
                old=store.get('corporate-event-quarantine:'+code,{'rows':[]})
                archive={event['id']:event for event in old['rows']+invalid}
                store.put('corporate-event-quarantine:'+code,{'rows':list(archive.values()),
                    'reason':'parser_review_required','updated_at':now()})
                for event in invalid:
                    merged.pop(event['id'])
        for event in parsed:
            event['title']=row['title'].strip()
            event['source_url']='https://dart.fss.or.kr/dsaf001/main.do?rcpNo='+receipt
            # Correction documents supersede the same class/record-date event,
            # while the original remains available in the immutable raw archive.
            if '정정' in row['title']:
                for original in merged.values():
                    if (original.get('record_date')==event['record_date'] and
                            original.get('share_class')==event['share_class'] and
                            original.get('filing_id','')<receipt):
                        original['superseded_by']=event['id']
            corrections=[e for e in merged.values() if e.get('record_date')==event['record_date']
                and e.get('share_class')==event['share_class'] and '정정' in e.get('title','')
                and e.get('filing_id','')>receipt]
            if corrections:
                event['superseded_by']=max(corrections,key=lambda e:e['filing_id'])['id']
            merged[event['id']]=event
        documents[receipt]={'status':'parsed' if parsed else 'review_required','raw_hash':raw_hash,'title':row['title'],'parser_version':PARSER_VERSION}
        break
    # Successful parsing of some dividends is never blanket corporate-action coverage.
    from .dividend_evidence import enrich_dividends
    reconciled=enrich_dividends(list(merged.values()),observations)
    store.put('events:'+code,{**events,'rows':sorted(reconciled,key=lambda r:r.get('date','')),
        'received_at':now(),'status':'partial' if merged else 'collecting',
        'coverage':events.get('coverage') or {'verified':False,'reason':'ex_dates_and_all_actions_required'}})
    updated_job={**job,'documents':documents,'checked_at':job.get('checked_at') if archive_only else now(),
        **({'archive_replay_at':now()} if archive_only else {}),
        'parser_version':PARSER_VERSION,'observation_revision':observation_revision,
        'complete':complete and all(documents.get(r,{}).get('status')=='related_company_not_security' or
            (documents.get(r,{}).get('parser_version')==PARSER_VERSION and documents.get(r,{}).get('status') in ('parsed','review_required')) for r in items)}
    if not archive_only:
        updated_job.pop('error',None)
        updated_job.pop('retry_after',None)
    store.put(key,updated_job)

def next_target(store):
    state=store.get('corporate-events-cursor',{})
    active=sorted(store.active())
    universe=store.listed_codes()
    turn=state.get('turn',0)
    use_active=bool(active) and turn%5!=0

    # Continue one unfinished DART index page every fifth non-universe slot.
    # Keep the existing universe cadence unchanged; the continuation replaces
    # one active-request slot rather than increasing provider request volume.
    use_partial=use_active and turn%5==4
    if use_partial:
        listed=set(universe)
        indexes=store.list_prefix('corporate-event-index:')
        jobs=store.list_prefix('corporate-events-job:')
        retry_now=now()
        partial=[]
        for key,index in indexes.items():
            code=key.removeprefix('corporate-event-index:')
            retry_after=jobs.get('corporate-events-job:'+code,{}).get('retry_after','')
            if (code in listed and index.get('status')=='partial'
                    and not (retry_after and retry_after>retry_now)):
                partial.append(code)
        partial.sort()
        if partial:
            previous=state.get('partial_after','')
            code=next((item for item in partial if item>previous),partial[0])
            store.put('corporate-events-cursor',{**state,'partial_after':code,
                'turn':turn+1,'at':retry_now})
            return code

    codes=active if use_active else universe
    if not codes:
        return None
    field='active_cursor' if use_active else 'universe_cursor'
    cursor=state.get(field,0)
    code=codes[cursor%len(codes)]
    store.put('corporate-events-cursor',{**state,field:cursor+1,'turn':turn+1,'at':now()})
    return code

async def corporate_events_loop(source):
    repair_related_events(source.store)
    while True:
        if source.dart:
            code=next_target(source.store)
            if code:
                try:
                    await collect_events(source,code)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    key='corporate-events-job:'+code
                    store=source.store
                    store.put(key,{**store.get(key,{}),'error':type(exc).__name__,
                        'retry_after':(datetime.fromisoformat(now())+timedelta(hours=1)).isoformat()})
        await asyncio.sleep(15)
