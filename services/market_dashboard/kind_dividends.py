"""Reviewed KIND ex-date notices, kept separate from issuer payment evidence."""
from datetime import date, datetime, timedelta
import hashlib
import re

from bs4 import BeautifulSoup
from .dividend_evidence import enrich_dividends
from .analysis import numeric
from .store import now

VERSION = 'kind-reviewed-exdate-v3'
# Explicitly reviewed document/entitlement binding, not a T+2/calendar estimate.
# This notice names ONLY Hyundai's first preferred series.
NOTICES = ({
    'code': '005385', 'name': '현대차우', 'company': '현대자동차',
    'share_label': '1우선주', 'share_series': 1, 'share_class': 'preferred',
    'ex_date': '2025-11-27', 'record_date': '2025-11-30',
    'url': 'https://kind.krx.co.kr/external/2025/11/26/000470/20251126001158/99335.htm',
}, {
    'code': '005380', 'name': '현대차', 'company': '현대자동차',
    'share_label': '보통주식', 'share_series': None, 'share_class': 'ordinary',
    'ex_date': '2025-05-29', 'record_date': '2025-05-30',
    'url': 'https://kind.krx.co.kr/external/2025/05/28/000385/20250528001030/99335.htm',
}, {
    'code': '005387', 'name': '현대차2우B', 'company': '현대자동차',
    'share_label': '2우선주B', 'share_series': 2, 'share_class': 'preferred',
    'ex_date': '2025-11-27', 'record_date': '2025-11-30',
    'url': 'https://kind.krx.co.kr/external/2025/11/26/000471/20251126001159/99335.htm',
}, {
    'code': '000660', 'name': 'SK하이닉스', 'company': 'SK하이닉스',
    'share_label': '보통주식', 'share_series': None, 'share_class': 'ordinary',
    'ex_date': '2025-05-29', 'record_date': '2025-05-31',
    'entitlement_source': 'dart_declared_dividend', 'filing_id': '20250423800599',
    'url': 'https://kind.krx.co.kr/external/2025/05/28/000380/20250528001029/99335.htm',
}, {
    'code': '005387', 'name': '현대차2우B', 'company': '현대자동차',
    'share_label': '2우선주B', 'share_series': 2, 'share_class': 'preferred',
    'ex_date': '2026-05-28', 'record_date': '2026-05-31',
    'url': 'https://kind.krx.co.kr/external/2026/05/27/000563/20260527001268/99335.htm',
})


def parse_notice(content, binding, evidence):
    soup = BeautifulSoup(content, 'html.parser')
    rows = [re.sub(r'\s+', ' ', row.get_text(' ', strip=True)) for row in soup.find_all('tr')]
    def field(label):
        matches = [row[len(label):].strip() for row in rows if row.startswith(label)]
        if len(matches) != 1:
            raise ValueError('KIND notice schema changed')
        return matches[0]
    if field('1. 회사명') != binding['company'] or '배당락' not in field('3. 사유'):
        raise ValueError('KIND notice company or event mismatch')
    share_rows = [row for row in rows if re.fullmatch(r'(보통주(?:식)?|[1-3]우선주B?) [\d,]+', row)]
    if len(share_rows) != 1 or share_rows[0].split()[0] != binding['share_label']:
        raise ValueError('KIND notice security class mismatch')
    ex_date = date.fromisoformat(field('4. 적용일'))
    if (ex_date.isoformat() != binding['ex_date'] or ex_date > date.fromisoformat(binding['record_date'])
            or ex_date > datetime.fromisoformat(evidence['available_at']).date()):
        raise ValueError('KIND ex date conflicts with reviewed binding')
    return ex_date.isoformat()


def reconcile_notice(binding, ex_date, evidence, observations, events=()):
    if binding.get('entitlement_source') == 'dart_declared_dividend':
        # Ex-date evidence is independent of cash payment confirmation. Match
        # the exact reviewed DART entitlement, not a nearby payment date.
        matches = [row for row in events if row.get('kind') == 'dividend'
                and not row.get('superseded_by') and row.get('filing_id') == binding['filing_id']
                and row.get('security_code') == binding['code']
                and row.get('share_class') == binding['share_class']
                and row.get('record_date') == binding['record_date']
                and row.get('currency') == 'KRW'
                and numeric(row.get('amount')) is not None and numeric(row['amount']) >= 0
                and row.get('source_trace', {}).get('source') == 'opendart'
                and re.fullmatch('[0-9a-f]{64}', row.get('source_trace', {}).get('sha256', ''))]
        if len(matches) != 1:
            raise ValueError('Reviewed DART dividend binding missing or conflicting')
        issuer = matches[0]
        identity = hashlib.sha256((binding['url'] + ':' + issuer['id']).encode()).hexdigest()
        return {'id': identity, 'security_code': binding['code'], 'share_class': binding['share_class'],
            'share_series': binding['share_series'], 'record_date': binding['record_date'],
            'amount': issuer['amount'], 'currency': 'KRW', 'ex_date': ex_date, 'reviewed': True,
            'review_method': VERSION, 'source_trace': {**evidence, 'entitlement_binding': {
                'method': 'reviewed_exact_dart_filing_class_and_record_date',
                'issuer_event_id': issuer['id'], 'issuer_source_trace': issuer['source_trace']}}}
    # Require a separately reviewed actual issuer payment for the exact series
    # and reviewed record date. Never pick a "nearby" dividend automatically.
    matches = [row for row in observations if
        row.get('security_code') == binding['code'] and row.get('share_class') == binding['share_class']
        and row.get('share_series') == binding['share_series']
        and row.get('record_date') == binding['record_date'] and row.get('currency') == 'KRW'
        and row.get('reviewed') is True and row.get('review_method') == 'hyundai-dividend-table-v3'
        and row.get('source_trace', {}).get('source') == 'hyundai_motor_ir'
        and row.get('confirmed_payment_date')]
    identities = {(row.get('amount'), row.get('confirmed_payment_date')) for row in matches}
    if len(identities) != 1:
        raise ValueError('Issuer dividend binding missing or conflicting')
    issuer = matches[0]
    identity = hashlib.sha256((binding['url'] + ':' + issuer['id']).encode()).hexdigest()
    return {'id': identity, 'security_code': binding['code'], 'share_class': binding['share_class'],
        'share_series': binding['share_series'], 'record_date': binding['record_date'],
        'amount': issuer['amount'], 'currency': 'KRW', 'ex_date': ex_date, 'reviewed': True,
        'review_method': VERSION, 'source_trace': {**evidence,
            'entitlement_binding': {'method': 'reviewed_exact_issuer_series_and_record_date',
                'issuer_observation_id': issuer['id'], 'issuer_source_trace': issuer['source_trace']}}}


async def collect_kind_dividends(source, code):
    for binding in NOTICES:
        if code != binding['code']:
            continue
        store = source.store
        # Each document has its own retry/refresh checkpoint. A successful
        # notice must not suppress another entitlement for the same security.
        document_id = hashlib.sha256(binding['url'].encode()).hexdigest()[:16]
        key = 'kind-dividends-job:' + code + ':' + document_id
        job = store.get(key, {})
        if job.get('version') == VERSION and job.get('next_at', '') > now():
            continue
        try:
            listed = {r['code']: r.get('name') for r in store.get('universe', {}).get('rows', [])}
            if listed.get(code) != binding['name']:
                raise ValueError('KIND security listing identity mismatch')
            response = await source.client.get(binding['url'])
            response.raise_for_status()
            digest = store.raw('kind-dividends', binding['url'], response.content)
            evidence = {'source': 'kind_ex_dividend_notice', 'source_url': binding['url'],
                'sha256': digest, 'available_at': now(), 'availability_basis': 'observed_at', 'method': VERSION}
            ex_date = parse_notice(response.content, binding, evidence)
            saved = store.get('dividend-observations:' + code, {})
            events = store.get('events:' + code, {})
            observation = reconcile_notice(binding, ex_date, evidence, saved.get('rows', []), events.get('rows', []))
            merged = {row['id']: row for row in saved.get('rows', [])}
            merged.setdefault(observation['id'], observation)
            reconciled = enrich_dividends(events.get('rows', []), list(merged.values()))
            store.put('dividend-observations:' + code, {**saved, 'rows': list(merged.values()), 'received_at': now()})
            # One verified dividend does not prove complete corporate-action coverage.
            store.put('events:' + code, {**events, 'rows': reconciled, 'received_at': now()})
            store.put(key, {'version': VERSION, 'status': 'ready', 'observation_id': observation['id'],
                'received_at': now(), 'next_at': (datetime.fromisoformat(now()) + timedelta(days=1)).isoformat()})
        except Exception as exc:
            store.put(key, {**job, 'version': VERSION, 'status': 'error', 'error': type(exc).__name__,
                'next_at': (datetime.fromisoformat(now()) + timedelta(hours=1)).isoformat()})
