"""Date-aware market views and evidence-backed document relationships.

Collectors write append-only observations. Public reads never create tables or
refresh providers. A missing historical day is not filled with today's values.
"""
import asyncio
import gzip
import hashlib
import ipaddress
import json
import logging
import math
import re
from datetime import date, datetime
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from sqlalchemy import Column, Index, MetaData, String, Table, Text, func, inspect, select, text

from .service_scope import included
from .store import next_snapshot_revision, now

VERSION = 'market-context-v1'
KST = ZoneInfo('Asia/Seoul')
META = MetaData()
DOCUMENTS = Table('market_context_documents', META,
    Column('document_id', String(64), primary_key=True),
    Column('revision', String(64), primary_key=True),
    Column('kind', String(20), nullable=False),
    Column('published_day', String(10)),
    Column('published_at', String(40)),
    Column('recorded_at', String(40), nullable=False),
    Column('payload', Text, nullable=False))
Index('market_context_document_day_idx', DOCUMENTS.c.published_day, DOCUMENTS.c.kind)
Index('market_context_document_revision_idx', DOCUMENTS.c.document_id, DOCUMENTS.c.recorded_at)
LINKS = Table('market_context_document_securities', META,
    Column('document_id', String(64), primary_key=True),
    Column('code', String(6), primary_key=True))
Index('market_context_document_security_idx', LINKS.c.code, LINKS.c.document_id)
BRIEFS = Table('market_context_briefs', META,
    Column('day', String(10), primary_key=True),
    Column('revision', String(64), nullable=False),
    Column('recorded_at', String(40), nullable=False),
    Column('payload', Text, nullable=False))
RELATIONS = Table('market_context_relations', META,
    Column('relation_id', String(64), primary_key=True),
    Column('revision', String(64), primary_key=True),
    Column('subject_code', String(6), nullable=False),
    Column('object_code', String(6), nullable=False),
    Column('recorded_at', String(40), nullable=False),
    Column('payload', Text, nullable=False))
Index('market_context_relation_subject_idx', RELATIONS.c.subject_code)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def day_of(value):
    if not value or not isinstance(value, str):
        return None
    try:
        if len(value) == 10:
            return date.fromisoformat(value).isoformat()
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        # Existing Korean source timestamps may explicitly have no UTC offset.
        stamp = stamp.replace(tzinfo=KST) if stamp.tzinfo is None else stamp.astimezone(KST)
        return stamp.date().isoformat()
    except ValueError:
        return None


def public_url(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if parsed.scheme not in ('https', 'http') or not host or parsed.username or parsed.password:
            return None
        if host.lower() == 'localhost' or '.' not in host:
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            pass
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ''))
    except ValueError:
        return None


def securities(store):
    return {r['code']: r for r in store.get('universe', {}).get('rows', [])
            if r.get('market') in ('KOSPI', 'KOSDAQ') and included(r)
            and r.get('security_type', 'stock') not in ('etf', 'etn', 'ETF', 'ETN', 'fund')}


def insert_ignore(store, db, table, rows):
    if not rows:
        return
    if store.engine.dialect.name == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert
    for offset in range(0, len(rows), 100):
        db.execute(insert(table).values(rows[offset:offset + 100]).on_conflict_do_nothing())


def verified_document_codes(payload):
    """A provider's stock tag is not a reviewed document-to-company assertion.

    Explicit per-code evidence is revision-bound in the immutable payload. A
    document's quality review alone must never promote every historical LINKS row.
    """
    bases = ('document_issuer', 'reviewed_document_subject')
    if payload.get('review_status') != 'verified' or payload.get('association_basis') not in bases:
        return set()
    codes = payload.get('verified_security_codes')
    if not isinstance(codes, list):
        return set()
    return {code for code in codes if isinstance(code, str) and re.fullmatch(r'[A-Z0-9]{6}', code)}


def verified_document_filter(payload, code, dialect):
    """Apply the same company-evidence policy before SQL limits and counts."""
    from sqlalchemy import JSON, and_
    bases = ('document_issuer', 'reviewed_document_subject')
    if dialect == 'sqlite':
        values = func.json_each(payload, '$.verified_security_codes').table_valued('value')
        return and_(
            func.json_extract(payload, '$.review_status') == 'verified',
            func.json_extract(payload, '$.association_basis').in_(bases),
            func.json_type(payload, '$.verified_security_codes') == 'array',
            select(1).select_from(values).where(values.c.value == code).exists(),
        )
    from sqlalchemy.dialects.postgresql import JSONB
    document = payload.cast(JSON)
    return and_(
        document['review_status'].as_string() == 'verified',
        document['association_basis'].as_string().in_(bases),
        payload.cast(JSONB)['verified_security_codes'].contains([code]),
    )


def dart_list_evidence(store, source_hash):
    """Bounded local original read; no network or archive mutation in projection."""
    if not isinstance(source_hash, str) or not re.fullmatch(r'[a-f0-9]{64}', source_hash):
        raise ValueError('Filing source hash missing')
    root = store.root.resolve()
    directory = root / 'raw' / 'dart'
    if directory.resolve() != directory or directory.parent.is_symlink():
        raise ValueError('Unsafe filing archive')
    envelope = directory / (source_hash + '.json')
    if envelope.is_symlink() or envelope.stat().st_size > 8 * 1024**2:
        raise ValueError('Filing metadata limit')
    metadata = json.loads(envelope.read_bytes())
    if not isinstance(metadata, dict):
        raise ValueError('Invalid filing metadata')
    url = urlsplit(metadata.get('url', ''))
    if (url.scheme != 'https' or url.hostname != 'opendart.fss.or.kr' or
            url.path != '/api/list.json' or url.username or url.password):
        raise ValueError('Not an official filing list')
    content = None
    maximum = 4 * 1024**2
    for suffix in ('.bin', '.bin.gz'):
        path = directory / (source_hash + suffix)
        if path.is_symlink():
            raise ValueError('Unsafe filing archive')
        if path.is_file():
            opener = gzip.open if suffix.endswith('.gz') else open
            with opener(path, 'rb') as stream:
                content = stream.read(maximum + 1)
            break
    if content is None and isinstance(metadata.get('payload'), str):
        content = metadata['payload'].encode('utf-8')
    if (content is None or len(content) > maximum or
            hashlib.sha256(content).hexdigest() != source_hash):
        raise ValueError('Filing source integrity failure')
    page = json.loads(content)
    if (not isinstance(page, dict) or page.get('status') != '000' or
            not isinstance(page.get('list'), list)):
        raise ValueError('Filing list unavailable')
    return page['list']


class ContextRepository:
    def __init__(self, store):
        self.store = store

    def initialize(self):
        """Only called by a collector; schema changes are additive."""
        META.create_all(self.store.engine)

    def available(self):
        return inspect(self.store.engine).has_table(RELATIONS.name)

    def ingest_news(self, code, snapshot):
        if code not in securities(self.store):
            return 0
        documents, links = [], []
        for row in snapshot.get('rows', []):
            url = public_url(row.get('url'))
            title = row.get('title')
            if not url or not isinstance(title, str) or not title.strip():
                continue
            document_id = digest(url)
            published_at = row.get('published_at') if day_of(row.get('published_at')) else None
            payload = {'document_id': document_id, 'title': title.strip(), 'url': url,
                'publisher': row.get('publisher'), 'published_at': published_at, 'kind': 'news',
                'source': snapshot.get('source'), 'source_document_id': snapshot.get('source_digest'),
                'source_received_at': snapshot.get('received_at'),
                'association_basis': 'provider_stock_news', 'review_status': 'source_observed'}
            # A repeated fetch is not a correction. Keep the receipt/source of
            # the first observation of each actual public content revision.
            revision = digest({k: payload[k] for k in ('title', 'url', 'publisher', 'published_at', 'kind')})
            payload['revision'] = revision
            documents.append({'document_id': document_id, 'revision': revision, 'kind': 'news',
                'published_day': day_of(published_at), 'published_at': published_at,
                'recorded_at': now(), 'payload': encoded(payload)})
            links.append({'document_id': document_id, 'code': code})
        return self._append_documents(documents, links)

    def ingest_filings(self, code, snapshot, *, offset=0, limit=20):
        """Confirm issuer identities from stored DART bytes, not title mentions."""
        if not 1 <= limit <= 50 or offset < 0:
            raise ValueError('Filing projection bound')
        from .issuer_documents import document_issuer
        identities = securities(self.store)
        corporations = self.store.get('dart-corporations', {}).get('codes', {})
        issuer = document_issuer(self.store, code, corporations)
        corporation = corporations.get(issuer)
        if code not in identities or not corporation:
            return {'processed': 0, 'verified': 0, 'rejected': 0, 'has_more': False}
        verified_codes = sorted(c for c in identities
                                if corporations.get(c) == corporation or
                                document_issuer(self.store, c, corporations) == issuer)
        all_rows = snapshot.get('rows') or []
        rows = all_rows[offset:offset + limit]
        documents, links, pages, rejected = [], [], {}, 0
        for row in rows:
            try:
                trace = row.get('source_trace') or {}
                source_hash = trace.get('source_document_id') or trace.get('sha256')
                if trace.get('source') != 'opendart':
                    raise ValueError('Unofficial filing')
                if source_hash not in pages:
                    pages[source_hash] = dart_list_evidence(self.store, source_hash)
                receipt = str(row.get('receipt') or '')
                if not re.fullmatch(r'\d{14}', receipt):
                    raise ValueError('Invalid filing identity')
                matches = [r for r in pages[source_hash]
                           if isinstance(r, dict) and r.get('rcept_no') == receipt]
                if len(matches) != 1:
                    raise ValueError('Missing or conflicting filing')
                original = matches[0]
                if (not isinstance(row.get('title'), str) or not 0 < len(row['title']) <= 1000 or
                        original.get('corp_code') != corporation or
                        original.get('stock_code') not in (None, '', issuer) or
                        original.get('report_nm') != row.get('title') or
                        original.get('rcept_dt') != row.get('date')):
                    raise ValueError('Filing issuer or content mismatch')
                day = datetime.strptime(original['rcept_dt'], '%Y%m%d').date().isoformat()
                url = 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=' + receipt
                identifier = digest(url)
                payload = {'document_id': identifier, 'title': row['title'], 'url': url,
                    'kind': 'filing', 'published_at': day, 'issuer': identities[issuer]['name'],
                    'source': 'opendart', 'source_hash': source_hash,
                    'association_basis': 'document_issuer', 'review_status': 'verified',
                    'verified_security_codes': verified_codes,
                    'issuer_binding': {'corp_code': corporation, 'issuer_security_code': issuer,
                                       'receipt': receipt,
                                       'method': 'dart_original_list_identity_v1'}}
                revision = digest({k: v for k, v in payload.items() if k != 'source_hash'})
                payload['revision'] = revision
                documents.append({'document_id': identifier, 'revision': revision, 'kind': 'filing',
                    'published_day': day, 'published_at': day,
                    'recorded_at': now(), 'payload': encoded(payload)})
                links.extend({'document_id': identifier, 'code': c} for c in verified_codes)
            except (ValueError, TypeError, KeyError, OSError, UnicodeError, EOFError):
                rejected += 1
        self._append_documents(documents, links)
        return {'processed': len(rows), 'verified': len(documents), 'rejected': rejected,
                'has_more': offset + len(rows) < len(all_rows), 'next_offset': offset + len(rows)}

    def _append_documents(self, documents, links):
        if not documents:
            return 0
        with self.store.engine.begin() as db:
            # Receipt clocks can tie on fast corrections. Order actual revisions
            # by a monotonic internal receipt, without changing publication dates
            # or rewriting original observations. Serialize this writer on both
            # supported databases; stable document order avoids lock inversion.
            if self.store.engine.dialect.name == 'sqlite':
                db.exec_driver_sql('BEGIN IMMEDIATE')
            for document in sorted(documents, key=lambda item: item['document_id']):
                identifier = document['document_id']
                if self.store.engine.dialect.name == 'postgresql':
                    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),
                               {'key': int(identifier[:16], 16) % (2**63)})
                exists = db.execute(select(DOCUMENTS.c.revision).where(
                    DOCUMENTS.c.document_id == identifier,
                    DOCUMENTS.c.revision == document['revision'])).scalar_one_or_none()
                if exists is not None:
                    continue
                prior = db.execute(select(func.max(DOCUMENTS.c.recorded_at)).where(
                    DOCUMENTS.c.document_id == identifier)).scalar_one_or_none()
                document['recorded_at'] = (
                    next_snapshot_revision(prior, document['recorded_at']) if prior else
                    datetime.fromisoformat(document['recorded_at']).isoformat(timespec='microseconds'))
                insert_ignore(self.store, db, DOCUMENTS, [document])
            insert_ignore(self.store, db, LINKS, links)
        return len(documents)

    def news(self, *, day=None, code=None, limit=30, offset=0):
        identities = securities(self.store)
        if not self.available():
            return {'rows': [], 'status': 'not_collected', 'has_more': False, 'date': day}
        # Preserve every correction but serve the latest recorded revision once.
        ranked = select(DOCUMENTS,
            func.row_number().over(partition_by=DOCUMENTS.c.document_id,
                order_by=(DOCUMENTS.c.recorded_at.desc(), DOCUMENTS.c.revision.desc())).label('n')).subquery()
        linked = select(LINKS.c.document_id).where(LINKS.c.code.in_([code] if code else list(identities)))
        query = select(ranked.c.payload).where(ranked.c.n == 1, ranked.c.kind == 'news',
                                              ranked.c.document_id.in_(linked))
        if code:
            query = query.where(verified_document_filter(
                ranked.c.payload, code, self.store.engine.dialect.name))
        if day:
            query = query.where(ranked.c.published_day == day)
        query = query.order_by(ranked.c.published_at.desc(), ranked.c.document_id).offset(offset).limit(limit + 1)
        with self.store.read_connection() as db:
            rows = [json.loads(p) for p in db.execute(query).scalars().all()]
            ids = [r['document_id'] for r in rows[:limit]]
            associations = db.execute(select(LINKS).where(LINKS.c.document_id.in_(ids))).mappings().all() if ids else []
        by_id = {}
        for link in associations:
            if link['code'] in identities:
                stock = identities[link['code']]
                by_id.setdefault(link['document_id'], []).append({'code': stock['code'], 'name': stock['name']})
        public_rows = []
        for row in rows[:limit]:
            verified = verified_document_codes(row)
            associations = by_id.get(row['document_id'], [])
            confirmed = [r for r in associations if r['code'] in verified]
            candidates = [{**r, 'review_status': 'candidate',
                           'association_basis': 'provider_stock_news'}
                          for r in associations if r['code'] not in verified]
            public_rows.append({**row, 'securities': confirmed, 'candidate_securities': candidates,
                                'association_status': 'verified' if confirmed else 'unverified'})
        return {'rows': public_rows,
            'status': 'ready' if rows else 'no_observations', 'has_more': len(rows) > limit,
            'date': day, 'offset': offset, 'history_mode': 'latest_recorded_revision',
            'coverage': 'collected_stock_headlines', 'revision': digest([r['revision'] for r in rows[:limit]])}

    def capture_market(self):
        identities = securities(self.store)
        observed_days = [day_of(r.get('as_of')) for r in identities.values()]
        today = datetime.now(KST).date().isoformat()
        days = [d for d in observed_days if d and d <= today]
        if not days:
            return None
        selected_day = max(days)
        groups = {}
        def finite(value):
            return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        for r in identities.values():
            if day_of(r.get('as_of')) != selected_day:
                continue
            venue = r.get('venue') or 'unspecified'
            group = groups.setdefault(venue, {'venue': venue, 'rows': [], 'covered': 0,
                'breadth': {'up': 0, 'down': 0, 'flat': 0, 'unknown': 0}, 'value': 0, 'value_covered': 0})
            row = {k: r.get(k) for k in ('code', 'name', 'market', 'price', 'change', 'value', 'volume', 'cap', 'as_of')}
            row['source_document_id'] = (r.get('source_trace') or {}).get('source_document_id')
            group['rows'].append(row)
            group['covered'] += 1
            change = r.get('change')
            group['breadth']['unknown' if not finite(change) else 'up' if change > 0 else 'down' if change < 0 else 'flat'] += 1
            if finite(r.get('value')):
                group['value'] += r['value']
                group['value_covered'] += 1
        for group in groups.values():
            group['rows'].sort(key=lambda r: (-(r['value'] if finite(r.get('value')) else -1), r['code']))
            # Daily market navigation is not a second full tick-history store.
            group['rows'] = group['rows'][:50]
            group['ranking_limit'] = 50
            if not group['value_covered']:
                group['value'] = None
        indices = [{k: r.get(k) for k in ('code', 'name', 'price', 'change', 'as_of')}
            for r in self.store.get('indices', {}).get('rows', [])
            if r.get('code') in ('KOSPI', 'KOSDAQ') and day_of(r.get('as_of')) == selected_day]
        payload = {'date': selected_day, 'groups': list(groups.values()), 'indices': indices,
            'scope': 'observed_domestic_shares', 'universe_total_at_capture': len(identities),
            'excluded_other_dates': len(identities) - sum(g['covered'] for g in groups.values()),
            'source_as_of': max(str(r.get('as_of')) for r in identities.values() if day_of(r.get('as_of')) == selected_day),
            'version': VERSION, 'history_mode': 'latest_observation_for_day'}
        revision = digest(payload)
        if self.store.engine.dialect.name == 'sqlite':
            from sqlalchemy.dialects.sqlite import insert
        else:
            from sqlalchemy.dialects.postgresql import insert
        with self.store.engine.begin() as db:
            statement = insert(BRIEFS).values(day=selected_day, revision=revision,
                recorded_at=now(), payload=encoded(payload))
            db.execute(statement.on_conflict_do_update(index_elements=[BRIEFS.c.day],
                set_={'revision': statement.excluded.revision, 'recorded_at': statement.excluded.recorded_at,
                      'payload': statement.excluded.payload}, where=BRIEFS.c.revision != statement.excluded.revision))
        return selected_day

    def brief(self, day=None):
        if not self.available():
            return {'date': day, 'status': 'not_collected', 'groups': [], 'indices': [], 'available_dates': []}
        with self.store.read_connection() as db:
            dates = db.execute(select(BRIEFS.c.day).distinct().order_by(BRIEFS.c.day.desc()).limit(366)).scalars().all()
            target = day or (dates[0] if dates else None)
            found = db.execute(select(BRIEFS).where(BRIEFS.c.day == target)
                .order_by(BRIEFS.c.recorded_at.desc(), BRIEFS.c.revision.desc()).limit(1)).mappings().first()
        news = self.news(day=target, limit=12) if target else {'rows': [], 'status': 'not_collected'}
        if not found:
            return {'date': target, 'status': 'not_collected', 'groups': [], 'indices': [],
                'news': news, 'available_dates': dates}
        return {**json.loads(found['payload']), 'status': 'ready', 'revision': found['revision'],
            'recorded_at': found['recorded_at'], 'news': news, 'available_dates': dates}

    def save_relation(self, relation):
        """Only verified evidence enters business relations; AI candidates remain candidates."""
        identities = securities(self.store)
        subject, target = relation.get('subject_code'), relation.get('object_code')
        if subject not in identities or target not in identities or subject == target:
            raise ValueError('Relation requires two different in-scope securities')
        kind = relation.get('kind')
        if kind not in ('supplier', 'customer', 'subsidiary', 'shareholder', 'business_partner'):
            raise ValueError('Unsupported relationship')
        status = relation.get('review_status')
        if status not in ('reviewed', 'candidate'):
            raise ValueError('Business relation requires review status')
        evidence = relation.get('evidence') or {}
        if not (public_url(evidence.get('url')) and evidence.get('document_id')
                and isinstance(evidence.get('excerpt'), str) and evidence['excerpt'].strip()
                and day_of(evidence.get('published_at'))):
            raise ValueError('Relation evidence requires document, excerpt and publication date')
        valid_from, valid_until = relation.get('valid_from'), relation.get('valid_until')
        if day_of(valid_from) != valid_from or not valid_from or (
                valid_until and (day_of(valid_until) != valid_until or valid_until < valid_from)):
            raise ValueError('Invalid relationship validity')
        if day_of(evidence['published_at']) > day_of(valid_from):
            raise ValueError('Do not apply relationship before evidence was public')
        relation_id = digest([subject, target, kind, evidence['document_id']])
        payload = {k: relation.get(k) for k in ('subject_code', 'object_code', 'kind', 'review_status', 'valid_from', 'valid_until')}
        payload.update({'evidence': {k: evidence[k] for k in ('url', 'document_id', 'excerpt', 'published_at')},
            'relation_id': relation_id})
        revision = digest(payload)
        with self.store.engine.begin() as db:
            if self.store.engine.dialect.name == 'sqlite':
                db.exec_driver_sql('BEGIN IMMEDIATE')
            elif self.store.engine.dialect.name == 'postgresql':
                db.execute(text('SELECT pg_advisory_xact_lock(:key)'),
                           {'key': int(relation_id[:16], 16) % (2**63)})
            prior = db.execute(select(func.max(RELATIONS.c.recorded_at)).where(
                RELATIONS.c.relation_id == relation_id)).scalar_one_or_none()
            receipt = next_snapshot_revision(prior, now()) if prior else now()
            insert_ignore(self.store, db, RELATIONS, [{'relation_id': relation_id, 'revision': revision,
                'subject_code': subject, 'object_code': target, 'recorded_at': receipt, 'payload': encoded(payload)}])
        return relation_id

    def relations(self, code, *, day=None):
        identities = securities(self.store)
        if not self.available():
            return {'rows': [], 'status': 'not_collected', 'date': day}
        target_day = day or datetime.now(KST).date().isoformat()
        ranked = select(RELATIONS,
            func.row_number().over(partition_by=RELATIONS.c.relation_id,
                order_by=(RELATIONS.c.recorded_at.desc(), RELATIONS.c.revision.desc())).label('n')).subquery()
        with self.store.read_connection() as db:
            records = db.execute(select(ranked.c.payload).where(ranked.c.n == 1, ranked.c.subject_code == code)).scalars().all()
        result = []
        for encoded_row in records:
            row = json.loads(encoded_row)
            if row['object_code'] not in identities or row['review_status'] != 'reviewed':
                continue
            if row['valid_from'] > target_day or (row['valid_until'] and row['valid_until'] < target_day):
                continue
            result.append({**row, 'name': identities[row['object_code']]['name']})
        # Even confirmed co-mentions remain document-navigation candidates, never
        # supplier/partner relations. Unreviewed provider tags cannot enter here.
        candidates = []
        for article in self.news(day=day, code=code, limit=50)['rows']:
            if not article.get('published_at') or day_of(article['published_at']) > target_day:
                continue
            for stock in article['securities']:
                if stock['code'] == code:
                    continue
                candidates.append({
                    'relation_id': digest([code, stock['code'], article['document_id']]),
                    'subject_code': code, 'object_code': stock['code'], 'name': stock['name'],
                    'kind': 'shared_news', 'label': '같은 관련 기사', 'review_status': 'source_observed',
                    'valid_from': day_of(article['published_at']), 'valid_until': None,
                    'evidence': {'document_id': article['document_id'], 'url': article['url'],
                        'excerpt': article['title'], 'excerpt_kind': 'headline',
                        'published_at': article['published_at']}})
        return {'rows': result, 'candidate_relations': candidates,
            'status': 'ready' if result else 'no_observations', 'date': day,
            'history_mode': 'latest_recorded_revision', 'revision': digest([result, candidates])}


def project_filings_batch(store, repository, *, securities_per_run=4, rows_per_security=20):
    """Collector-only, capacity-gated projection with persistent per-stock cursors."""
    from .storage_budget import collection_capacity
    if not 1 <= securities_per_run <= 4 or not 1 <= rows_per_security <= 50:
        raise ValueError('Filing projection budget')
    capacity = collection_capacity(store, 'research-filings')
    if capacity['limited'] or capacity.get('used_percent', 0) >= 80:
        return {'status': 'capacity_limited', 'processed': 0}
    codes = sorted(securities(store))
    cursor = store.get('research-filings-projection', {}).get('after_code', '')
    chosen = [c for c in codes if c > cursor][:securities_per_run]
    if not chosen:
        chosen = codes[:securities_per_run]
    versions = store.revisions(['filings:' + c for c in chosen])
    processed = 0
    for code in chosen:
        source_revision = versions.get('filings:' + code)
        if not source_revision:
            continue
        key = 'research-filings:' + code
        previous = store.get(key, {})
        same = previous.get('source_revision') == source_revision
        if same and previous.get('has_more') is False:
            continue
        offset = previous.get('next_offset', 0) if same else 0
        try:
            result = repository.ingest_filings(code, store.get('filings:' + code, {}),
                                               offset=offset, limit=rows_per_security)
            store.put(key, {**result, 'source_revision': source_revision, 'recorded_at': now()})
            processed += result['processed']
        except (ValueError, KeyError, TypeError, OSError):
            # Keep source and last successful cursor; another stock still gets a turn.
            store.put(key, {**previous, 'last_error': 'projection_failed', 'recorded_at': now()})
    if chosen:
        store.put('research-filings-projection', {'after_code': chosen[-1], 'recorded_at': now()})
    return {'status': 'ready', 'processed': processed}


async def market_context_loop(store, *, once=False):
    """Incremental, bounded local projection; performs no extra provider calls."""
    repository = ContextRepository(store)
    initialized = False
    revisions = {}
    while True:
        try:
            if not initialized:
                repository.initialize()
                initialized = True
            for key, snapshot in store.list_prefix('news:').items():
                try:
                    revision = digest(snapshot)
                    if revisions.get(key) != revision:
                        repository.ingest_news(key.split(':', 1)[1], snapshot)
                        revisions[key] = revision
                except (ValueError, KeyError, TypeError) as exc:
                    # An invalid document must not stop projection of unrelated stocks.
                    logging.getLogger(__name__).warning('News projection rejected: %s', type(exc).__name__)
            repository.capture_market()
            project_filings_batch(store, repository)
        except Exception as exc:
            logging.getLogger(__name__).warning('Market context projection: %s', type(exc).__name__)
        if once:
            return
        await asyncio.sleep(60)


def attach_routes(app, base, get_store):
    from fastapi import HTTPException, Query

    def parsed_day(value):
        if value is None:
            return None
        if day_of(value) != value:
            raise HTTPException(422, 'Expected YYYY-MM-DD')
        if value > datetime.now(KST).date().isoformat():
            raise HTTPException(422, 'Future dates are not supported')
        return value

    def check_code(store, code):
        if code not in securities(store):
            raise HTTPException(404, 'Security not found')

    @app.get(base + '/market-brief')
    def market_brief(date: str | None = None):
        return ContextRepository(get_store()).brief(parsed_day(date))

    @app.get(base + '/news')
    def market_news(date: str | None = None, code: str | None = None,
                    limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0, le=10000)):
        store = get_store()
        if code:
            check_code(store, code)
        return ContextRepository(store).news(day=parsed_day(date), code=code, limit=limit, offset=offset)

    @app.get(base + '/securities/{code}/relations')
    def security_relations(code: str, date: str | None = None):
        store = get_store()
        check_code(store, code)
        return ContextRepository(store).relations(code, day=parsed_day(date))
