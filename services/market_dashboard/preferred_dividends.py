"""Project preferred dividends only from an explicitly named filing annex.

This reuses an issuer's archived document, never its ordinary dividend amount.
"""
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta

from bs4 import BeautifulSoup

from .store import now

VERSION = 'named-preferred-dividend-v1'
# Samsung's disclosed annex names the one listed preferred issue explicitly.
# Other issuers/series require a separately reviewed mapping.
REVIEWED = {'005935': {'issuer': '005930', 'name': '삼성전자우', 'label': '종류주식'}}


def parse_named_preferred(content, *, code, filing_id, published_at, evidence):
    from .corporate_events import document_text, parse_dividend_document
    binding = REVIEWED.get(code)
    if not binding:
        raise ValueError('Preferred security mapping not reviewed')
    soup = BeautifulSoup(document_text(content), 'html.parser')
    amounts = []
    compact = lambda value: re.sub(r'\s+', '', value)
    for table in soup.find_all('table'):
        rows = [[cell.get_text(' ', strip=True) for cell in row.find_all(['td', 'th', 'tu'], recursive=False)]
                for row in table.find_all('tr') if row.find_parent('table') == table]
        for index, cells in enumerate(rows):
            headers = [compact(cell) for cell in cells]
            required = ['종류주식명', '종류주식구분', '1주당배당금(원)']
            if not all(header in headers for header in required):
                continue
            name_at, class_at, amount_at = [headers.index(header) for header in required]
            for values in rows[index + 1:]:
                if len(values) <= max(name_at, class_at, amount_at):
                    continue
                if compact(values[name_at]) != binding['name']:
                    continue
                if compact(values[class_at]) not in {'우선주', '우선주식'}:
                    raise ValueError('Preferred annex security class mismatch')
                try:
                    amount = Decimal(values[amount_at].replace(',', '').strip())
                except InvalidOperation:
                    raise ValueError('Invalid preferred annex amount') from None
                if not amount.is_finite() or amount < 0:
                    raise ValueError('Invalid preferred annex amount')
                amounts.append(amount)
    if not amounts:
        return []
    if len(set(amounts)) != 1:
        raise ValueError('Conflicting named preferred annexes')
    rows = parse_dividend_document(content, code=code, filing_id=filing_id,
        published_at=published_at, evidence=evidence, share_label=binding['label'])
    if len(rows) != 1 or Decimal(rows[0]['amount']) != amounts[0]:
        raise ValueError('Named preferred annex and decision amount disagree')
    row = rows[0]
    row['source_trace'] = {**evidence, 'security_mapping': {
        'method': VERSION, 'issuer_security_code': binding['issuer'],
        'security_code': code, 'annex_security_name': binding['name'],
        'amount_column': '1주당 배당금(원)', 'decision_share_label': binding['label'],
    }}
    row.update(share_label=binding['name'], projection_version=VERSION)
    return rows


def project_preferred_dividends(store, code):
    """Use hash-verified issuer archives without another DART request."""
    from .issuer_documents import company_link
    from .dividend_evidence import enrich_dividends
    binding = REVIEWED.get(code)
    link = company_link(store, code)
    if not binding or not link or link['issuer_security_code'] != binding['issuer']:
        return
    issuer = binding['issuer']
    index = store.get('corporate-event-index:' + issuer, {})
    documents = store.get('corporate-events-job:' + issuer, {}).get('documents', {})
    revision = hashlib.sha256(json.dumps([index, documents], sort_keys=True).encode()).hexdigest()
    key = 'preferred-events-job:' + code
    previous = store.get(key, {})
    observations = store.get('dividend-observations:' + code, {}).get('rows', [])
    observation_revision = hashlib.sha256(json.dumps(observations, sort_keys=True).encode()).hexdigest()
    if previous.get('version') == VERSION and previous.get('revision') == revision and (
            previous.get('status') == 'ready' or previous.get('next_at', '') > now()):
        # Date evidence can arrive after all filing archives are parsed. Refresh
        # only its projection, retaining provider checkpoints and source dates.
        if previous.get('observation_revision') != observation_revision:
            snapshot = store.get('events:' + code, {})
            rows = enrich_dividends(snapshot.get('rows', []), observations)
            if rows != snapshot.get('rows', []):
                store.put('events:' + code, {**snapshot, 'rows': rows, 'reconciled_at': now()})
            store.put(key, {**previous, 'observation_revision': observation_revision,
                'reconciled_at': now()})
        return
    snapshot = store.get('events:' + code, {})
    merged = {row['id']: row for row in snapshot.get('rows', [])}
    results = {}
    from .corporate_events import is_issuer_action
    for item in sorted(index.get('rows', []), key=lambda row: row['receipt']):
        receipt = item['receipt']
        if '배당' not in item.get('title', '') or not is_issuer_action(item['title']):
            continue
        digest = documents.get(receipt, {}).get('raw_hash', '')
        if not re.fullmatch('[0-9a-f]{64}', digest):
            results[receipt] = 'issuer_document_not_collected'
            continue
        archive = store.root / 'raw' / 'dart-events' / (digest + '.bin')
        if not archive.is_file():
            results[receipt] = 'issuer_archive_missing'
            continue
        content = archive.read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            results[receipt] = 'issuer_archive_hash_mismatch'
            continue
        evidence = {'source': 'opendart', 'source_url': 'https://opendart.fss.or.kr/api/document.xml?rcept_no=' + receipt,
            'sha256': digest, 'filing_id': receipt, 'available_at': item['date']}
        try:
            parsed = parse_named_preferred(content, code=code, filing_id=receipt,
                published_at=item['date'], evidence=evidence)
        except ValueError:
            results[receipt] = 'preferred_annex_review_required'
            continue
        results[receipt] = 'parsed' if parsed else 'named_preferred_annex_not_found'
        for event in parsed:
            event.update(title=item['title'], source_url='https://dart.fss.or.kr/dsaf001/main.do?rcpNo=' + receipt)
            if '정정' in item['title']:
                for old in merged.values():
                    if (old.get('record_date') == event['record_date'] and old.get('share_class') == 'preferred'
                            and old.get('filing_id', '') < receipt):
                        old['superseded_by'] = event['id']
            corrections = [old for old in merged.values()
                if old.get('record_date') == event['record_date'] and old.get('share_class') == 'preferred'
                and '정정' in old.get('title', '') and old.get('filing_id', '') > receipt]
            if corrections:
                event['superseded_by'] = max(corrections, key=lambda old: old['filing_id'])['id']
            merged[event['id']] = event
    if merged:
        rows = enrich_dividends(list(merged.values()), observations)
        store.put('events:' + code, {**snapshot, 'code': code, 'rows': rows,
            'status': 'partial', 'received_at': now(),
            'coverage': snapshot.get('coverage') or {'verified': False, 'reason': 'dividend_dates_not_fully_verified'}})
    store.put(key, {'version': VERSION, 'revision': revision, 'documents': results,
        'observation_revision': observation_revision,
        'status': 'ready' if results and all(state == 'parsed' for state in results.values()) else 'partial',
        'issuer_security_code': issuer, 'received_at': now(),
        'next_at': (datetime.fromisoformat(now()) + timedelta(hours=1)).isoformat()})
