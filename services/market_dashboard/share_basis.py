"""Bounded, evidence-backed share-unit intervals. Never infer splits from prices."""
import re
from copy import deepcopy
from datetime import date, timedelta

from .financial_dates import publication_date

DISCLOSURE_REVIEW_VERSION = 'complete-dart-disclosure-window-v1'
SHARE_UNIT_CHANGE_TERMS = (
    '주식분할', '액면분할', '주식병합', '액면병합', '무상증자', '주식배당',
    '자본감소', '감자결정',
    '합병', '분할', '주식교환', '주식이전', '주식의포괄적교환', '주식의포괄적이전',
    '주식포괄적교환', '주식포괄적이전',
)
SHARE_COUNT_EVENT_TERMS = (
    '유상증자', '주식관련사채', '신주인수권', '전환사채', '증권예탁증권',
    'DR발행', '주식소각', '자기주식취득', '자기주식처분',
)


def extend_reviewed_history(evidence, *, security_code, company_id, target_end,
                            disclosure_end, disclosure_type, pages, filings,
                            issuer_trace, filing_reviews=None):
    """Extend a reviewed share basis only across a complete, hashed issuer index.

    The index must cover the interval through the latest price date. Any
    potentially share-changing issuer disclosure blocks automatic extension.
    """
    if not valid_evidence(evidence, company_id) or not (
            evidence.get('complete_split_history') is True
            or evidence.get('no_split_interval') is True):
        raise ValueError('Existing verified share-unit evidence is required')
    if (evidence.get('security_code') != security_code
            or evidence.get('company_id') != company_id):
        raise ValueError('Security and issuer identity do not match')
    try:
        previous_end = date.fromisoformat(evidence['end'])
        target = date.fromisoformat(target_end)
        covered_until = date.fromisoformat(disclosure_end)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('Invalid share-basis review dates') from exc
    if target <= previous_end or target > covered_until:
        raise ValueError('Disclosure coverage does not contain the target price date')
    if disclosure_type != 'I':
        raise ValueError('Issuer disclosure index type I is required')
    if not isinstance(issuer_trace, dict) or not issuer_trace.get('source_url') or not re.fullmatch(
            r'[0-9a-f]{64}', str(issuer_trace.get('sha256', ''))):
        raise ValueError('Issuer share-history source trace is required')
    if issuer_trace.get('company_id') not in (None, company_id):
        raise ValueError('Issuer share-history company does not match')

    try:
        total_pages = int(pages[0]['total_pages'])
        ordered = sorted(pages, key=lambda page: int(page['page']))
        page_numbers = [int(page['page']) for page in ordered]
        if total_pages < 1 or page_numbers != list(range(1, total_pages + 1)):
            raise ValueError('Disclosure pages are incomplete')
        for page in ordered:
            query = page.get('request', {})
            confirmed_empty = (page.get('status') == '013' and total_pages == 1
                and page.get('empty_response_verified') is True
                and page.get('returned_count') == 0 and not filings
                and query.get('corp_code') == company_id and query.get('pblntf_ty') == 'I'
                and query.get('last_reprt_at') == 'N'
                and query.get('bgn_de') == (previous_end + timedelta(days=1)).strftime('%Y%m%d')
                and query.get('end_de') == target.strftime('%Y%m%d'))
            if (page.get('sha256_valid') is not True
                    or not re.fullmatch(r'[0-9a-f]{64}', str(page.get('sha256', '')))
                    or int(page.get('total_pages', 0)) != total_pages
                    or page.get('status') != '000' and not confirmed_empty
                    or page.get('company_id') not in (None, company_id)
                    or page.get('disclosure_type') not in (None, disclosure_type)):
                raise ValueError('Disclosure page evidence is invalid')
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc) in (
                'Disclosure pages are incomplete', 'Disclosure page evidence is invalid'):
            raise
        raise ValueError('Disclosure page evidence is invalid') from exc

    blockers = []
    share_count_events = []
    for filing in filings:
        try:
            filing_day = date.fromisoformat(str(filing['date'])[:10].replace('-', ''))
            title = str(filing['title'])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('Disclosure filing row is incomplete') from exc
        if filing.get('company_id') not in (None, company_id):
            raise ValueError('Disclosure filing belongs to another issuer')
        if previous_end < filing_day <= target:
            event = {'date': filing_day.isoformat(), 'receipt': filing.get('receipt'),
                     'title': title.strip()}
            # Formatting spaces are not a reason to miss a share-unit event.
            # The unmodified source title remains in the review evidence.
            title_key = re.sub(r'\s+', '', title)
            if any(term in title_key for term in SHARE_UNIT_CHANGE_TERMS):
                blockers.append(event)
            elif any(term in title_key for term in SHARE_COUNT_EVENT_TERMS):
                share_count_events.append({**event, 'company_id': company_id})
    if blockers:
        raise ValueError('Share-changing issuer disclosure requires manual review')

    reviews = {str(row.get('receipt')): row for row in filing_reviews or []
               if isinstance(row, dict) and row.get('receipt')}
    reviewed_count_events = []
    for event in share_count_events:
        review = reviews.get(str(event.get('receipt')))
        trace = review.get('source_trace', {}) if isinstance(review, dict) else {}
        if (not review or review.get('company_id') != company_id
                or review.get('effect') != 'share_count_change_same_unit'
                or not str(review.get('reason', '')).strip()
                or trace.get('company_id') != company_id
                or trace.get('receipt') != event.get('receipt')
                or not trace.get('source_url')
                or not re.fullmatch(r'[0-9a-f]{64}', str(trace.get('sha256', '')))):
            raise ValueError('Share-count disclosure requires a source-backed unit review')
        reviewed_count_events.append({**event, 'review': deepcopy(review)})

    review = {
        'version': DISCLOSURE_REVIEW_VERSION,
        'security_code': security_code,
        'company_id': company_id,
        'window_start': (previous_end + timedelta(days=1)).isoformat(),
        'target_end': target.isoformat(),
        'disclosure_end': covered_until.isoformat(),
        'disclosure_type': disclosure_type,
        'total_pages': total_pages,
        'page_hashes': [page['sha256'] for page in ordered],
        'page_statuses': [page['status'] for page in ordered],
        'filing_rows_reviewed': sum(int(page.get('returned_count', 0)) for page in ordered),
        'share_changing_filings': blockers,
        'share_count_events': reviewed_count_events,
        'issuer_trace': deepcopy(issuer_trace),
    }
    trace = deepcopy(evidence['source_trace'])
    trace['disclosure_window_review'] = review
    return {
        **deepcopy(evidence),
        'basis_id': f"{evidence['basis_id'].split(':reviewed-through:')[0]}:reviewed-through:{target:%Y%m%d}",
        'end': target.isoformat(),
        'basis_date': target.isoformat(),
        'source_trace': trace,
        'review_extension': review,
    }


def valid_evidence(evidence, company=None):
    from .split_units import valid_history
    if evidence and valid_history(evidence):
        return company is None or evidence.get('company_id') == company
    return bool(evidence and evidence.get('verified') is True and evidence.get('no_split_interval') is True
                and evidence.get('basis_id') and evidence.get('security_code')
                and evidence.get('start') and evidence.get('end') and evidence['start'] <= evidence['end']
                and evidence.get('source_trace',{}).get('sha256')
                and evidence.get('source_trace',{}).get('source_url')
                and (company is None or evidence.get('company_id') == company))


def aligned_facts(facts, evidence):
    if not valid_evidence(evidence):
        return facts
    result=[]
    for fact in facts:
        publication,_=publication_date(fact)
        start,end=fact.get('period_start'),fact.get('period_end')
        if (valid_evidence(evidence, fact.get('company_id') or fact.get('entity_id'))
                and start and end and evidence['start'] <= start <= end <= evidence['end']
                and publication):
            if evidence.get('complete_split_history'):
                from .split_units import normalize_fact
                fact = normalize_fact(fact, evidence)
                if fact.get('split_normalization',{}).get('basis_id') != evidence['basis_id']:
                    result.append(fact)
                    continue
            elif publication[:10] > evidence['end']:
                from .split_units import sourced
                reviews = [r for r in evidence.get('fact_unit_reviews', [])
                    if sourced(r) and all(r.get(k) == fact.get(k)
                        for k in ('filing_id','basis','period_start','period_end'))
                    and r.get('company_id') == evidence.get('company_id')
                    and r.get('security_code') == evidence['security_code']]
                try:
                    unit_day = date.fromisoformat(reviews[0]['unit_as_of']).isoformat() if len(reviews)==1 else None
                except (KeyError,TypeError,ValueError):
                    unit_day = None
                if not unit_day or not evidence['start'] <= unit_day <= evidence['end']:
                    result.append(fact)
                    continue
                fact = {**fact, 'share_unit_review':reviews[0]}
            result.append({**fact, 'per_share_basis':evidence['basis_id'],
                           'share_basis_evidence':evidence['source_trace']})
        else:
            result.append(fact)
    return result


def aligned_prices(snapshot, evidence, code):
    if not valid_evidence(evidence) or evidence['security_code'] != code or snapshot.get('adjustment') != 'raw':
        return snapshot
    if evidence.get('complete_split_history'):
        from .split_units import normalize_prices
        return normalize_prices(snapshot, evidence)
    return {**snapshot, 'per_share_basis':evidence['basis_id'],
            'per_share_basis_start':evidence['start'], 'per_share_basis_end':evidence['end'],
            'share_basis_evidence':evidence['source_trace']}
