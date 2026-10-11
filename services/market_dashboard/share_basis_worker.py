"""Advance reviewed share units only through complete source-backed disclosure windows."""
import asyncio
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
import hashlib
import json
import logging
import re
import time
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from .raw_archive import read_original
from .share_basis import extend_reviewed_history, valid_evidence
from .share_basis_review_store import load_reviews, review_index_key, review_revision
from .store import now
from .dart_retry import provider_retry_for

log = logging.getLogger('share-basis-review')
VERSION = 'bounded-share-disclosure-window-v2'
URL = 'https://opendart.fss.or.kr/api/list.json'


def review_targets(store):
    """Only existing verified issuer reviews; never assume the universe is reviewed."""
    listed = set(store.listed_codes())
    corporations = store.get('dart-corporations', {}).get('codes', {})
    return sorted(key.split(':', 1)[1] for key, evidence in store.list_prefix('share-basis:').items()
        if key.split(':', 1)[1] in listed
        and evidence.get('security_code') == key.split(':', 1)[1]
        and corporations.get(key.split(':', 1)[1])
        and valid_evidence(evidence, corporations[key.split(':', 1)[1]]))


def latest_raw_price_date(store, code):
    metadata = store.metadata(f'bars:{code}:통합:raw', {})
    if metadata.get('adjustment') != 'raw':
        return None
    stamp = metadata.get('last_time')
    if not stamp:
        stamp = max((r.get('date', '') for r in metadata.get('rows', [])), default='')
    return date.fromisoformat(stamp[:10]).isoformat() if stamp else None


def validate_page(payload, query, digest):
    status = payload.get('status')
    rows = payload.get('list') or []
    if status == '013':
        if rows or int(payload.get('total_count') or 0) or int(query['page_no']) != 1:
            raise ValueError('Conflicting empty disclosure response')
        return {'page': 1, 'total_pages': 1, 'returned_count': 0, 'total_count': 0,
            'status': '013', 'empty_response_verified': True, 'sha256': digest,
            'sha256_valid': True, 'company_id': query['corp_code'], 'disclosure_type': 'I',
            'request': query}, []
    if status != '000' or not isinstance(rows, list) or not rows:
        raise ValueError('Disclosure window is unavailable')
    page, pages, count = (int(payload.get(k, 0)) for k in ('page_no', 'total_page', 'total_count'))
    if page != int(query['page_no']) or not 1 <= page <= pages <= 20 or count < len(rows):
        raise ValueError('Invalid disclosure page totals')
    normalized = []
    for row in rows:
        receipt = str(row.get('rcept_no', ''))
        day = str(row.get('rcept_dt', ''))
        if (row.get('corp_code') != query['corp_code'] or not re.fullmatch(r'\d{14}', receipt)
                or not query['bgn_de'] <= day <= query['end_de'] or not row.get('report_nm')):
            raise ValueError('Disclosure row identity or date mismatch')
        datetime.strptime(day, '%Y%m%d')
        normalized.append({'receipt': receipt, 'date': day, 'title': row['report_nm'],
                           'company_id': query['corp_code']})
    return {'page': page, 'total_pages': pages, 'total_count': count, 'status': status,
        'returned_count': len(rows), 'sha256': digest, 'sha256_valid': True,
        'company_id': query['corp_code'], 'disclosure_type': 'I', 'request': query}, normalized


def verified_window(store, job):
    """Re-read all exact response bytes before promoting a completed window."""
    pages, filings = [], []
    for saved in job['pages']:
        query = saved.get('request', {})
        if (query.get('corp_code') != job['company_id'] or query.get('bgn_de') != job['begin']
                or query.get('end_de') != job['end'].replace('-', '')
                or query.get('pblntf_ty') != 'I' or query.get('last_reprt_at') != 'N'):
            raise ValueError('Disclosure request window changed')
        content = read_original(store.root/'raw/dart-share-window', saved['sha256'])
        page, rows = validate_page(json.loads(content), saved['request'], saved['sha256'])
        if page != saved:
            raise ValueError('Disclosure evidence metadata changed')
        pages.append(page)
        filings.extend(rows)
    pages.sort(key=lambda item: item['page'])
    if not pages or [p['page'] for p in pages] != list(range(1, pages[0]['total_pages']+1)):
        raise ValueError('Disclosure pages are incomplete')
    if len({p['total_count'] for p in pages}) != 1 or len({p['total_pages'] for p in pages}) != 1:
        raise ValueError('Disclosure pagination changed')
    if len(filings) != pages[0]['total_count'] or len({r['receipt'] for r in filings}) != len(filings):
        raise ValueError('Disclosure count or identity overlap')
    return pages, filings


def apply_reviewed_window(store, code, company, evidence, job, revision):
    try:
        pages, filings = verified_window(store, job)
    except (OSError, KeyError) as exc:
        raise ValueError('Saved disclosure window is unavailable') from exc
    reviews = load_reviews(store, security_code=code, company_id=company,
                           receipts=[row['receipt'] for row in filings])
    updated = extend_reviewed_history(evidence, security_code=code, company_id=company,
        target_end=job['end'], disclosure_end=job['end'], disclosure_type='I',
        pages=pages, filings=filings, issuer_trace=evidence['source_trace'], filing_reviews=reviews)
    history = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
    # A final Python equality check leaves a race before the subsequent writes.
    # Compare, preserve history and replace the basis on one locked DB connection.
    if not store.compare_and_put('share-basis:'+code, evidence, updated,
            history_key='share-basis-history:'+code+':'+history[:32],
            revision_guards={review_index_key(code): revision}):
        raise ValueError('Share basis or filing review changed during review')
    result = {**job, 'status': 'ready', 'reviewed_end': job['end'], 'updated_at': now()}
    result.pop('next_at', None)
    result.pop('error', None)
    store.put('share-basis-window:'+code, result)
    return result


async def review_share_basis(source, code):
    store = source.store
    if code not in set(store.listed_codes()):
        return {'status': 'out_of_scope'}
    # A reviewer may mutate a decoded Store.get dictionary before putting it.
    # Keep the expectation independent throughout provider/archive processing.
    evidence = deepcopy(store.get('share-basis:'+code, {}, use_redis=False))
    company = store.get('dart-corporations', {}).get('codes', {}).get(code)
    if not company or not valid_evidence(evidence, company) or evidence.get('security_code') != code:
        return {'status': 'initial_share_unit_review_required'}
    target = latest_raw_price_date(store, code)
    if not target or target <= evidence['end']:
        return {'status': 'current', 'end': evidence['end']}
    local_now = datetime.now(ZoneInfo('Asia/Seoul'))
    today = local_now.date().isoformat()
    if target >= today:
        # A still-open disclosure date cannot establish a complete day. Review
        # it on the following day; never certify today's evolving index.
        return {'status': 'disclosure_day_not_closed', 'target_end': target}
    key = 'share-basis-window:'+code
    old = store.get(key, {})
    revision = review_revision(store, code)
    same_basis = old.get('basis_id') == evidence['basis_id']
    waiting_review = same_basis and old.get('status') == 'review_required'
    review_changed = old.get('review_revision') != revision
    new_day = old.get('review_checked_day') != today
    if waiting_review and not review_changed and not new_day:
        return old
    # New reviewed evidence can unblock an archived complete window immediately,
    # without spending a supplier request or waiting for a network retry time.
    replay = waiting_review and review_changed and not new_day
    if same_basis and not replay and old.get('next_at', '') > now():
        return old
    # Finish a frozen older window before following prices to a newer date.
    continuing = same_basis and old.get('status') in ('partial', 'error', 'review_required')
    window_limit = (date.fromisoformat(evidence['end'])+timedelta(days=31)).isoformat()
    end = old['end'] if continuing else min(target, window_limit)
    begin = (date.fromisoformat(evidence['end'])+timedelta(days=1)).strftime('%Y%m%d')
    job = dict(old) if continuing else {'version': VERSION, 'basis_id': evidence['basis_id'],
          'company_id': company, 'begin': begin, 'end': end, 'pages': []}
    if waiting_review and not replay:
        # Recheck the exact frozen range on a later day to detect new/corrected
        # disclosure responses. Do not keep polling it every hour within a day.
        job['pages'] = []
    if (not waiting_review and job['pages']
            and len(job['pages']) == job['pages'][-1].get('total_pages')):
        # Resume a complete archive after interruption between saving the last
        # page and promoting the review; never request a fictitious next page.
        replay = True
    job.update(version=VERSION, review_revision=revision, review_checked_day=today)
    page_no = len(job['pages'])+1
    query = {'corp_code': company, 'bgn_de': begin, 'end_de': end.replace('-', ''),
             'pblntf_ty': 'I', 'last_reprt_at': 'N', 'page_no': str(page_no),
             'page_count': '100', 'sort': 'date', 'sort_mth': 'asc'}
    try:
        if replay:
            return apply_reviewed_window(store, code, company, evidence, job, revision)
        response = await source._dart_get(URL, params={'crtfc_key': source.dart, **query})
        response.raise_for_status()
        digest = store.raw('dart-share-window', URL+'?'+urlencode(query), response.content)
        page, _ = validate_page(response.json(), query, digest)
        job = {**job, 'pages': job['pages']+[page], 'updated_at': now(), 'status': 'partial'}
        job.pop('next_at', None)
        job.pop('error', None)
        store.put(key, job)
        if page_no < page['total_pages']:
            return job
        return apply_reviewed_window(store, code, company, evidence, job, revision)
    except Exception as exc:
        # No source fact or prior unit evidence is replaced on failure.
        provider = provider_retry_for(store, URL)
        needs_review = isinstance(exc, ValueError)
        tomorrow = (local_now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        result = {**job, 'status': 'review_required' if needs_review else 'error',
            'error': type(exc).__name__, 'updated_at': now(),
            'next_at': tomorrow.astimezone(UTC).isoformat() if needs_review
                else provider.get('next_at') if provider.get('next_at', '') > now()
                else (datetime.now(UTC)+timedelta(hours=1)).isoformat()}
        store.put(key, result)
        return result


async def share_basis_review_loop(source):
    from .storage_budget import collection_capacity
    from .share_basis_bootstrap import bootstrap_target, prepare_bootstrap
    from .share_basis_bootstrap_notes import collect_bootstrap_note
    cursor, targets, refresh_at = 0, [], 0
    while True:
        try:
            if source.dart and not collection_capacity(source.store, 'share-basis')['limited']:
                from .share_basis_promotion import process_one_review
                await asyncio.to_thread(process_one_review, source.store)
                if time.monotonic() >= refresh_at:
                    targets = review_targets(source.store)
                    refresh_at = time.monotonic() + 300
                if targets:
                    await review_share_basis(source, targets[cursor % len(targets)])
                # Existing unit reviews must not be the only issuers ever
                # inspected. One persisted rotation slot keeps work bounded.
                progress = source.store.get('share-basis-bootstrap-cursor', {})
                position = int(progress.get('cursor', 0))
                candidate = bootstrap_target(source.store, set(targets), position)
                if candidate:
                    await collect_bootstrap_note(source, candidate)
                    prepare_bootstrap(source.store, candidate)
                    source.store.put('share-basis-bootstrap-cursor', {
                        'cursor': position + 1, 'last_code': candidate, 'updated_at': now()})
        except Exception as exc:
            log.warning('Share unit window review failed: %s', type(exc).__name__)
        cursor += 1
        await asyncio.sleep(60)
