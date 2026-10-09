"""Reversible per-dataset 404 diagnosis against the latest Toss catalogues."""
import hashlib
from datetime import UTC, datetime, timedelta


def _catalogue_summary(market, item, rows):
    symbols = sorted({str(row.get('symbol')) for row in rows if row.get('symbol')})
    digest = hashlib.sha256('\n'.join(symbols).encode()).hexdigest()
    trace = item.get('source_trace')
    return {
        'market': market,
        'received_at': item.get('received_at'),
        'row_count': len(rows),
        'symbols_sha256': digest,
        'source': trace.get('source') if isinstance(trace, dict) else None,
    }, digest, symbols


def diagnose_not_found(store, code, kind):
    """A complete absence from Toss is distinct from a dashboard catalogue mismatch.

    Only compact catalogue evidence is persisted. Full source traces can contain
    large request metadata and are already retained with the catalogue snapshot.
    """
    evidence = []
    revisions = []
    complete = True
    found = False
    for market in ('KOSPI', 'KOSDAQ', 'KR_ETC'):
        item = store.get('toss-listing:' + market, {})
        rows = item.get('rows', [])
        try:
            stamp = datetime.fromisoformat(item['received_at'])
            fresh = stamp.tzinfo is not None and datetime.now(UTC) - stamp < timedelta(days=2)
        except (KeyError, TypeError, ValueError):
            fresh = False
        valid = item.get('status') == 'ready' and fresh and bool(item.get('source_trace'))
        complete = complete and valid
        if valid:
            summary, revision, symbols = _catalogue_summary(market, item, rows)
            evidence.append(summary)
            revisions.append(revision)
            found = found or code in symbols
    catalogue_revision = hashlib.sha256('|'.join(revisions).encode()).hexdigest() if complete else None
    in_service_universe = code in set(store.listed_codes())
    if not complete:
        status, retryable = 'catalogue_check_pending', True
    elif found:
        status, retryable = 'endpoint_or_parameters', True
    elif in_service_universe:
        # Keep the KRX-listed security in our universe. Stop retrying all seven
        # Toss endpoints until the provider's own catalogue changes.
        status, retryable = 'provider_catalogue_omission', False
    else:
        status, retryable = 'catalogue_mismatch', True
    return {
        'security_code': code,
        'kind': kind,
        'status': status,
        'retryable': retryable,
        'catalogue_revision': catalogue_revision,
        'catalogue_evidence': evidence,
        'reason': (
            'Toss lists are complete and fresh but do not contain this listed security; '
            'the security remains in the service universe and will be retried if the Toss catalogue changes.'
            if status == 'provider_catalogue_omission'
            else '404 is not proof that the listed company or all its data is unavailable'
        ),
    }
