"""Offline, issuer-bound JYP EPS/share-unit review. No Store, network or writes.

Output is a review candidate, never an operational approval/import command.
The bindings were inspected against the actual public DART documents.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

CODE, COMPANY = '035900', '00258689'
MANIFEST = Path(__file__).with_name('reviews') / 'jyp_2026h1_v1.json'
MANIFEST_SHA = '576bf7d262824ac04953aaa47cab844b86cebf42b17d82d0bfcd7298e2b8e401'
VERSION = 'jyp-bounded-eps-review-v1'
MAX_DOCUMENT = 200_000


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load_manifest():
    raw = MANIFEST.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == MANIFEST_SHA, 'Reviewed binding changed')
    return json.loads(raw)


def read_document(root, document):
    name = document['file']
    require(re.fullmatch(r'\d{14}-(?:main|\d+)\.html', name), 'Document path invalid')
    path = root / name
    require(not path.is_symlink() and path.resolve().parent == root.resolve(),
            'Document escaped root')
    require(path.is_file() and path.stat().st_size <= MAX_DOCUMENT, 'Document missing or oversized')
    raw = path.read_bytes()
    require(len(raw) == document['bytes'] and hashlib.sha256(raw).hexdigest() == document['sha256'],
            'Original document hash changed')
    url = urlsplit(document['url'])
    require(url.scheme == 'https' and url.netloc == 'dart.fss.or.kr'
            and not url.username and not url.fragment
            and url.path in ('/dsaf001/main.do', '/report/viewer.do')
            and parse_qs(url.query).get('rcpNo') == [name[:14]], 'Document identity changed')
    return BeautifulSoup(raw, 'html.parser')


def table_rows(soup, index):
    tables = soup.find_all('table')
    require(0 <= index < len(tables), 'Reviewed table missing')
    return [[c.get_text(' ', strip=True) for c in tr.find_all(['td', 'th'], recursive=False)]
            for tr in tables[index].find_all('tr')]


def parse_eps_rows(rows, expected, periods):
    require(rows == expected, 'Reviewed EPS headers, scope or values changed')
    headers = rows[:-3]
    require(headers == [['', '보통주']] or headers in (
        [['', '주식'], ['', '보통주'], ['', '3개월', '누적']],
        [['', '주식의 종류'], ['', '보통주'], ['', '3개월', '누적']]),
        'Ordinary share columns required')
    require(rows[-2][0] == '가중평균유통보통주식수(단위 : 주)'
            and rows[-1][0] == '기본주당이익(손실)'
            and '보통주에 귀속되는' in rows[-3][0], 'EPS account scope changed')
    require(all(len(row) == len(periods) + 1 for row in rows[-3:]), 'EPS column mismatch')
    output = []
    for col, (start, end) in enumerate(periods, 1):
        require(70 <= (date.fromisoformat(end) - date.fromisoformat(start)).days + 1 <= 380,
                'Accounting period invalid')
        values = [row[col] for row in rows[-3:]]
        require(all(re.fullmatch(r'\(?-?[\d,]+(?:\.\d+)?\)?', v) for v in values),
                'EPS numeric input missing')
        profit, shares, eps = [Decimal(v.replace(',', '').replace('(', '-').replace(')', ''))
                               for v in values]
        require(shares > 0 and abs(profit / shares - eps) <= Decimal('.5') + Decimal('.5') / shares,
                'EPS rounding reconciliation failed')
        output.append((start, end, profit, shares, eps))
    return output


def prepare(source_dir, metadata):
    from services.market_dashboard.eps_calculation import calculate_ttm_eps, eps_inputs
    from services.market_dashboard.fact_store import fact_identity
    from services.market_dashboard.reviewed_eps_tables import CONCEPTS, KRW, SHARES
    from services.market_dashboard.share_basis import aligned_facts, valid_evidence

    binding = load_manifest()
    require(metadata.get('code') == CODE and metadata.get('readonly') is True,
            'Readonly issuer metadata required')
    snapshots = metadata['snapshots']
    require(snapshots['dart-corporations']['value']['company_id'] == COMPANY,
            'Security and company mapping changed')
    require(snapshots.get('share-basis:' + CODE, {}).get('missing') is True,
            'Existing share approval requires separate reconciliation')
    documents = {d['file']: d for d in binding['documents']}
    soups = {name: read_document(source_dir, doc) for name, doc in documents.items()}
    text = {name: soup.get_text(' ', strip=True) for name, soup in soups.items()}
    facts, reviews = [], []
    for note in binding['notes']:
        receipt, published = note['receipt'], note['published_at']
        require('JYP Ent./' in text[receipt + '-main.html']
                and published.replace('-', '.') in text[receipt + '-main.html'],
                'Main issuer or publication changed')
        require('(주)제이와이피엔터테인먼트' in text[receipt + '-1.html'],
                'Filing cover issuer changed')
        require('연결 포괄손익계산서' in text[receipt + '-21.html']
                and note['period_label'] in text[receipt + '-21.html']
                and '30. 주당손익 (연결)' in text[receipt + '-54.html'],
                'Accounting period or consolidated scope changed')
        eps_doc, period_doc = documents[receipt + '-54.html'], documents[receipt + '-21.html']
        for spec in note['tables']:
            require('(단위 : 원)' in soups[eps_doc['file']].find_all('table')[spec['index'] - 1]
                    .get_text(' ', strip=True), 'EPS monetary unit changed')
            rows = table_rows(soups[eps_doc['file']], spec['index'])
            for col, (start, end, profit, shares, eps) in enumerate(
                    parse_eps_rows(rows, spec['rows'], spec['periods']), 1):
                require(end <= published and binding['start'] <= start <= end <= binding['end'],
                        'Publication or review period invalid')
                trace = {'source': 'dart_reviewed_note', 'company_id': COMPANY,
                         'security_code': CODE, 'filing_id': receipt, 'available_at': published,
                         'source_url': eps_doc['url'], 'sha256': eps_doc['sha256'],
                         'period_source_url': period_doc['url'],
                         'period_source_sha256': period_doc['sha256'],
                         'mapping_version': VERSION, 'table': spec['index'], 'column': col}
                ids = []
                for i, (role, value, unit) in enumerate((('profit', profit, KRW),
                        ('shares', shares, SHARES), ('eps', eps, KRW + '/' + SHARES))):
                    fact = {'account_id': '{urn:korea-stocks:reviewed-eps-note}' + CONCEPTS[role],
                            'company_id': COMPANY, 'filing_id': receipt, 'basis': 'CFS',
                            'share_class': 'ordinary', 'kind': 'duration', 'unit': unit,
                            'currency': 'KRW' if role != 'shares' else None, 'decimals': 0,
                            'value': str(value), 'period_start': start, 'period_end': end,
                            'period_verified': True, 'published_at': published,
                            'mapping_verified': True, 'source_account_label': rows[-3 + i][0],
                            'dimensions': {}, 'source_trace': {**trace, 'unit': unit}}
                    identity = fact_identity(fact)
                    facts.append({**fact, 'fact_id': identity})
                    ids.append(identity)
                reviews.append({'basis': 'CFS', 'verified': True, 'filing_id': receipt,
                    'company_id': COMPANY, 'security_code': CODE, 'period_start': start,
                    'period_end': end, 'unit_as_of': binding['end'],
                    'source_trace': {**trace, 'input_fact_ids': ids}})

    # All inspected current/prior capital columns, issued/treasury movement,
    # and weighted-share day counts must agree with the reviewed original.
    for check in binding['share_tables']:
        require(table_rows(soups[check['file']], check['table']) == check['rows'],
                'Capital, share class or share movement changed')
    for receipt in ('20260318001519', '20260814003204'):
        require('당기 보고기간 중에 발생한 변동 수량은 없습니다.' in text[receipt + '-7.html'],
                'Treasury movement review changed')
    inputs = eps_inputs(facts)['rows']
    stored = snapshots['official-view:' + CODE + ':analysis']['value']['ttm_eps']['inputs']
    matched = []
    for row in inputs:
        if row['filing_id'] != '20260814003204':
            continue
        found = [x for x in stored if all(x.get(k) == row[k] for k in
                 ('company_id', 'filing_id', 'basis', 'period_start', 'period_end'))]
        require(len(found) == 1 and all(Decimal(found[0][k]) == Decimal(row[k]) for k in
                ('profit', 'weighted_shares', 'reported_eps')),
                'Stored H1 EPS differs from original')
        require(found[0].get('share_class_verified') is True
                and found[0].get('period_verified') is True
                and found[0].get('share_class') == 'ordinary' and found[0].get('currency') == 'KRW'
                and found[0].get('published_at') == row['published_at']
                and found[0].get('input_fact_ids'), 'Stored H1 evidence incomplete')
        matched.append({'period_start': row['period_start'], 'period_end': row['period_end'],
                        'input_fact_ids': found[0]['input_fact_ids']})
        # Existing H1 facts are not replaced by this independent HTML replay.
        # The reviewer decision identifies their original immutable ledger IDs.
        unit_review = next(r for r in reviews if r['filing_id'] == row['filing_id']
                           and r['period_start'] == row['period_start']
                           and r['period_end'] == row['period_end'])
        unit_review['source_trace']['input_fact_ids'] = list(found[0]['input_fact_ids'])
    require(len(matched) == 4, 'All four CFS H1 input periods are required')
    evidence = {'security_code': CODE, 'company_id': COMPANY, 'share_class': 'ordinary',
        'start': binding['start'], 'end': binding['end'], 'basis_date': binding['end'],
        'basis_id': CODE + ':review:20260630:jyp-v1', 'verified': True, 'no_split_interval': True,
        'fact_unit_reviews': reviews,
        'review_note': '2025 and 2026H1 capital, issued/treasury movement and weighted-share '
            'days reconcile at ordinary KRW500 units; historical 2010 consolidation and '
            '2018 cancellation remain outside this window. Not a current-price approval.',
        'source_trace': {'source': 'dart_reviewed_share_units', 'company_id': COMPANY,
            'source_url': documents['20260814003204-43.html']['url'],
            'sha256': documents['20260814003204-43.html']['sha256'],
            'available_at': '2026-08-14', 'filing_id': '20260814003204',
            'input_documents': list(documents.values()), 'mapping_version': VERSION}}
    require(valid_evidence(evidence, COMPANY), 'Existing share evidence contract rejected review')
    before = calculate_ttm_eps(facts)
    after = calculate_ttm_eps(aligned_facts(facts, evidence))
    return {'status': 'review_candidate_not_applied', 'version': VERSION,
            'security_code': CODE, 'company_id': COMPANY,
            'network_requests': 0, 'database_writes': 0,
            'metadata_observed_at': metadata.get('at'), 'metadata_release': metadata.get('release'),
            'documents': list(documents.values()), 'proposed_evidence': evidence,
            'candidate_new_facts': [{k: v for k, v in f.items() if k != 'fact_id'}
                                   for f in facts if f['filing_id'] != '20260814003204'],
            'halfyear_original_replay_facts': [f for f in facts
                                             if f['filing_id'] == '20260814003204'],
            'stored_halfyear_matches': matched, 'before': before, 'after': after,
            'limits': ['No operational import or approval; parent review required.',
                'Existing H1 raw fact ledger identity remains an apply-time gate.',
                '2026-07-01 through current price share-unit window is not reviewed.',
                'Stored filing-index is unavailable; issuer cover/main dates are preserved.',
                'Only CFS ordinary basic EPS; no OFS, preferred, diluted or OCF approval.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, required=True)
    parser.add_argument('--metadata', type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.source_dir, json.loads(args.metadata.read_text(encoding='utf-8-sig')))
    result['metadata_sha256'] = hashlib.sha256(args.metadata.read_bytes()).hexdigest()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

