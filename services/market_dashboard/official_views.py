"""Versioned public read models, separate from large accounting source facts."""
import logging
from sqlite3 import OperationalError as SQLiteOperationalError

from sqlalchemy.exc import OperationalError

logger = logging.getLogger(__name__)

PROJECTION_VERSION = 17
FIELDS = {
    'company': ('company', 'report_facts', 'source_trace', 'company_source_trace', 'issuer_link'),
    'fundamentals': ('company', 'statements', 'source_trace', 'financials_received_at',
        'full_financials_periods', 'major_accounts_received_at', 'major_accounts_period',
        'major_accounts_status'),
    'analysis': (),
    'reports': ('financial_reports','financial_indicators'),
}


def with_share_basis(official, evidence):
    """Apply the independent ledger without mutating collected source facts.

    Call only when a ledger row exists. An empty row is a retraction, whereas
    no row preserves the legacy embedded-evidence contract.
    """
    return {**official, 'share_basis_evidence': evidence,
            'xbrl_facts': [{k: v for k, v in fact.items() if k != 'per_share_basis'}
                           for fact in official.get('xbrl_facts', [])]}


def project(official, view):
    if view not in FIELDS:
        raise ValueError('Invalid official read model')
    if view == 'analysis':
        from .per_share import reported_eps
        from .eps_calculation import calculate_ttm_eps
        from .financial_summary import ttm_summary
        from .fundamental_metrics import calculate_fundamental_metrics
        from .share_basis import aligned_facts
        xbrl = aligned_facts(official.get('xbrl_facts',[]), official.get('share_basis_evidence'))
        calculated = calculate_ttm_eps(xbrl)
        eps = reported_eps({**official,'xbrl_facts':xbrl})
        eps['rows'].extend(row for row in calculated['rows'] if row['status']=='ready')
        fundamentals = calculate_fundamental_metrics(xbrl, official.get('cashflow_reviews',[]))
        fundamentals['rows'].extend(reported_eps({**official,'xbrl_facts':xbrl}, 'EPS_DILUTED')['rows'])
        return {'eps':eps, 'ttm_eps':calculated, 'fundamental_metrics':fundamentals,
                'share_basis_evidence':official.get('share_basis_evidence'),
                'ttm':{basis:ttm_summary(official,basis) for basis in ('CFS','OFS')},
                'per_share_facts':official.get('per_share_facts',[])}
    if view == 'reports' and official:
        from .financial_timeline import annual_contexts
        return {**{key: official[key] for key in FIELDS[view] if key in official},
                'annual_period_contexts': annual_contexts(official)}
    return {key: official[key] for key in FIELDS[view] if key in official} if official else None


def read_view(store, code, view):
    if view not in FIELDS:
        raise ValueError('Invalid official read model')
    if view in ('reports','company'):
        from .issuer_documents import company_link,company_profile
        link=company_link(store,code)
        if link:
            source=read_view(store,link['issuer_security_code'],view)
            if view=='company':
                own=project(store.get('official:'+code,{},use_redis=False),view)
                return company_profile(source or {},own,link)
            return {**(source or {}),'issuer_link':link}
    source_key = 'official:' + code
    key = 'official-view:' + code + ':' + view
    basis_key = 'share-basis:' + code
    dependencies = (source_key,basis_key) if view == 'analysis' else (source_key,)
    revisions = store.revisions(dependencies)
    revision = revisions.get(source_key)
    if revision is None:
        return None
    cached = store.get(key, {}, use_redis=False)
    basis_revision = revisions.get(basis_key)
    if (cached.get('source_revision') == revision and cached.get('projection_version') == PROJECTION_VERSION
            and (view != 'analysis' or cached.get('share_basis_revision') == basis_revision)):
        return cached['payload']
    # Existing snapshots migrate on their next read without rewriting source data.
    source = store.get(source_key, {}, use_redis=False)
    if view == 'analysis' and basis_revision is not None:
        # The independent reviewed unit ledger is authoritative. A retracted
        # review must not fall back to an old embedded verification.
        source = with_share_basis(source, store.get(basis_key, {}, use_redis=False))
    data = project(source, view)
    if store.revisions(dependencies) == revisions:
        try:
            store.put(key, {'source_revision':revision, 'projection_version':PROJECTION_VERSION, 'payload':data,
                **({'share_basis_revision':basis_revision} if view == 'analysis' else {})})
        except (OperationalError, SQLiteOperationalError, OSError) as exc:
            # A read-model cache is optional. A full disk or read-only database
            # must not prevent a valid projection from reaching the caller.
            # Never log exception text: it may contain SQL payloads or paths.
            logger.warning('Official read-model cache write failed: %s', type(exc).__name__)
    return data
