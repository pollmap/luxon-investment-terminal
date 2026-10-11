"""Explicitly reviewed share-count disclosures, never an automatic approval queue.

Call record_review only after reviewing the exact source document. The legacy
v83 review object is preserved verbatim; the envelope adds security scope and
an immutable version. Saving or loading it never creates share-basis evidence.
"""
import hashlib
import json
import re
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

from .raw_archive import read_original

VERSION = 'source-backed-share-count-review-v1'


def review_index_key(code):
    if not isinstance(code, str) or not re.fullmatch(r'[0-9A-Z]{6}', code):
        raise ValueError('Invalid review security')
    return 'share-unit-reviews:' + code


def review_revision(store, code):
    key = review_index_key(code)
    return store.revisions((key,)).get(key)


def _scope(store, code, company):
    review_index_key(code)
    if not re.fullmatch(r'\d{8}', company):
        raise ValueError('Invalid review company')
    if store.get('dart-corporations', {}, use_redis=False).get('codes', {}).get(code) != company:
        raise ValueError('Review security and company do not match')


def _document_receipt(url):
    if not isinstance(url, str):
        raise ValueError('Review source URL is missing')
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Review source URL is not an official document')
    query = parse_qs(parsed.query)
    if parsed.netloc == 'opendart.fss.or.kr' and parsed.path == '/api/document.xml':
        name, allowed = 'rcept_no', {'rcept_no'}
    elif parsed.netloc == 'dart.fss.or.kr' and parsed.path in (
            '/report/viewer.do', '/dsaf001/main.do'):
        name = 'rcpNo'
        allowed = {'rcpNo', 'dcmNo', 'eleId', 'offset', 'length', 'dtd'}
    else:
        raise ValueError('Review source URL is not an official document')
    if set(query) - allowed or len(query.get(name, [])) != 1:
        raise ValueError('Review document receipt is ambiguous')
    return query[name][0]


def _validate(store, code, company, review, raw_source):
    """Check an existing reviewer decision and replay its exact archived bytes."""
    if not isinstance(review, dict):
        raise ValueError('Review decision is missing')
    trace = review.get('source_trace') or {}
    if not isinstance(trace, dict):
        raise ValueError('Review source trace is missing')
    receipt = review.get('receipt', '')
    digest = trace.get('sha256', '')
    if (not isinstance(receipt, str) or not re.fullmatch(r'\d{14}', receipt)
            or review.get('company_id') != company
            or review.get('security_code', code) != code
            or trace.get('company_id') != company or trace.get('receipt') != receipt
            or trace.get('security_code', code) != code
            or trace.get('filing_id', receipt) != receipt
            or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)
            or review.get('effect') != 'share_count_change_same_unit'
            or not isinstance(review.get('reason'), str) or not review['reason'].strip()
            or ('verified' in review and review['verified'] is not True)
            or review.get('status', 'reviewed') != 'reviewed'):
        raise ValueError('Source-backed review identity or decision is invalid')
    # This is a security-scoped event review, not an XBRL fact/dimension mapping.
    # Do not repurpose a preferred-share, segment or accounting fact review.
    if (review.get('share_class', 'ordinary') != 'ordinary'
            or review.get('dimensions') or trace.get('dimensions')):
        raise ValueError('Share-count review has an incompatible share class or dimension')
    url = trace.get('source_url', '')
    if _document_receipt(url) != receipt:
        raise ValueError('Review source receipt does not match')
    if not isinstance(raw_source, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', raw_source):
        raise ValueError('Invalid source archive name')
    root = (store.root / 'raw').resolve()
    directory = (root / raw_source).resolve()
    if not directory.is_relative_to(root):
        raise ValueError('Source archive is outside the raw store')
    try:
        metadata = json.loads((directory / (digest + '.json')).read_bytes())
        if (not isinstance(metadata, dict) or metadata.get('sha256') != digest
                or metadata.get('url') != url):
            raise ValueError('Review archive source identity does not match')
        read_original(directory, digest)
    except (OSError, UnicodeError) as exc:
        raise ValueError('Reviewed source archive is unavailable') from exc
    return receipt, digest


def _index(store, code, company):
    index = store.get(review_index_key(code), {}, use_redis=False)
    if index and (index.get('version') != VERSION or index.get('security_code') != code
                  or index.get('company_id') != company
                  or not isinstance(index.get('entries'), dict)):
        raise ValueError('Review index scope does not match')
    return index


def _review_key(envelope):
    """The full envelope binds company/hash; the storage key fits VARCHAR(128)."""
    code, receipt = envelope.get('security_code', ''), envelope.get('receipt', '')
    review_index_key(code)
    if not isinstance(receipt, str) or not re.fullmatch(r'\d{14}', receipt):
        raise ValueError('Stored review identity changed')
    identity = hashlib.sha256(json.dumps(envelope, sort_keys=True).encode()).hexdigest()
    return f'share-unit-review:{code}:{receipt}:{identity}'


def record_review(store, *, security_code, company_id, review, raw_source,
                  replaces=None):
    """Persist an explicit reviewer decision. No discovery, inference or promotion.

    A different decision or new bytes under the same receipt require the caller
    to identify the prior review ID. Old versions remain immutable. A corrected
    filing with a new receipt always requires its own independently reviewed entry.
    """
    _scope(store, security_code, company_id)
    receipt, digest = _validate(store, security_code, company_id, review, raw_source)
    envelope = {'version': VERSION, 'security_code': security_code, 'company_id': company_id,
                'receipt': receipt, 'source_hash': digest, 'raw_source': raw_source,
                'review': deepcopy(review)}
    key = _review_key(envelope)
    with store.lock:
        index = _index(store, security_code, company_id)
        entries = dict(index.get('entries', {}))
        previous = entries.get(receipt)
        if previous == key:
            if store.get(key, {}, use_redis=False) != envelope:
                raise ValueError('Immutable review version changed')
            return key
        if previous != replaces:
            raise ValueError('Review replacement requires the exact previous review ID')
        saved = store.get(key, None, use_redis=False)
        if saved is not None and saved != envelope:
            raise ValueError('Immutable review version changed')
        if saved is None:
            store.put(key, envelope)
        entries[receipt] = key
        store.put(review_index_key(security_code), {'version': VERSION,
            'security_code': security_code, 'company_id': company_id, 'entries': entries})
    return key


def load_reviews(store, *, security_code, company_id, receipts):
    """Read only exact requested receipts; revalidate scope, envelope and raw bytes."""
    _scope(store, security_code, company_id)
    entries = _index(store, security_code, company_id).get('entries', {})
    results = []
    for receipt in sorted(set(receipts)):
        key = entries.get(receipt)
        if not key:
            continue  # A missing review remains a blocker in extend_reviewed_history.
        envelope = store.get(key, {}, use_redis=False)
        expected = _review_key(envelope)
        if (key != expected or envelope.get('version') != VERSION
                or envelope.get('security_code') != security_code
                or envelope.get('company_id') != company_id or envelope.get('receipt') != receipt):
            raise ValueError('Stored review identity changed')
        actual_receipt, digest = _validate(store, security_code, company_id,
                                          envelope.get('review'), envelope.get('raw_source', ''))
        if actual_receipt != receipt or digest != envelope.get('source_hash'):
            raise ValueError('Stored review source changed')
        results.append(deepcopy(envelope['review']))
    return results
