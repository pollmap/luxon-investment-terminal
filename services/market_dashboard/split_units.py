"""Normalize reviewed share units, including already-restated comparatives.

Only explicit filing/period unit reviews are accepted. A period end alone does
not tell whether the issuer has retrospectively restated EPS or share counts.
Source facts are immutable; returned transformations have separate identities.
"""
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json

VERSION = 'reviewed-split-units-v1'
SHARES = '{http://www.xbrl.org/2003/instance}shares'


def sourced(row):
    return bool(row.get('verified') is True and row.get('source_trace', {}).get('source_url')
                and row.get('source_trace', {}).get('sha256'))


def valid_history(evidence):
    try:
        if not sourced(evidence) or evidence.get('complete_split_history') is not True:
            return False
        start, end = date.fromisoformat(evidence['start']), date.fromisoformat(evidence['end'])
        if start > end or evidence.get('basis_date') != evidence['end']:
            return False
        if not all(evidence.get(k) for k in ('basis_id','security_code','company_id')):
            return False
        seen = set()
        for event in evidence.get('splits', []):
            day = date.fromisoformat(event['effective_date'])
            ratio = Decimal(str(event['new_shares'])) / Decimal(str(event['old_shares']))
            if (not sourced(event) or event.get('kind') != 'split' or not start <= day <= end
                    or not ratio.is_finite() or ratio <= 0 or Decimal(str(event['old_shares'])) <= 0
                    or event['effective_date'] in seen):
                return False
            seen.add(event['effective_date'])
        return bool(seen)
    except (KeyError, TypeError, ValueError, InvalidOperation, ZeroDivisionError):
        return False


def factor_after(evidence, as_of):
    """A restated fact uses its reviewed unit date, not the accounting period."""
    date.fromisoformat(as_of)
    if not evidence['start'] <= as_of <= evidence['basis_date']:
        raise ValueError('Share unit outside reviewed history')
    result = Decimal(1)
    for event in evidence['splits']:
        if as_of < event['effective_date'] <= evidence['basis_date']:
            result *= Decimal(str(event['new_shares'])) / Decimal(str(event['old_shares']))
    return result


def normalize_fact(fact, evidence):
    if not valid_history(evidence) or fact.get('company_id') != evidence['company_id']:
        return fact
    if fact.get('split_normalization'):
        # Replays do not apply the factor a second time. A different policy must
        # restart from the immutable source, not a previously normalized fact.
        return fact
    classes = [v.rsplit('}',1)[-1] for k,v in (fact.get('dimensions') or {}).items()
               if k.rsplit('}',1)[-1] == 'ClassesOfShareCapitalAxis']
    if classes and classes != ['OrdinarySharesMember']:
        return fact
    reviews = [r for r in evidence.get('fact_unit_reviews', []) if sourced(r)
        and all(r.get(k) == fact.get(k) for k in ('filing_id','basis','period_start','period_end'))]
    if not reviews:
        from .financial_dates import publication_date
        published, _ = publication_date(fact)
        for interval in evidence.get('unchanged_intervals', []):
            if (sourced(interval) and interval.get('no_split_interval') is True
                    and interval.get('company_id') == evidence['company_id']
                    and interval.get('security_code') == evidence['security_code']
                    and published and fact.get('period_start') and fact.get('period_end')
                    and evidence['start'] <= interval['start'] <= fact['period_start'] <= fact['period_end']
                    <= published[:10] <= interval['end'] <= evidence['end']
                    and not any(interval['start'] <= e['effective_date'] <= interval['end'] for e in evidence['splits'])):
                reviews.append({'unit_as_of':interval['end'],'source_trace':interval['source_trace']})
    if len(reviews) != 1 or not fact.get('fact_id'):
        return fact
    review = reviews[0]
    try:
        factor = factor_after(evidence, review['unit_as_of'])
        value = Decimal(str(fact['value']))
        if not value.is_finite():
            return fact
    except (KeyError, ValueError, InvalidOperation):
        return fact
    unit = fact.get('unit', '')
    concept = fact.get('account_id', '').rsplit('}', 1)[-1]
    if unit == SHARES and concept != 'WeightedAverageShares':
        return fact  # Older filings can misdeclare EPS as a shares-only unit.
    if concept in ('BasicEarningsLossPerShare','DilutedEarningsLossPerShare') and not unit.endswith('KRW/' + SHARES):
        return fact
    # Profit/cash flow totals do not change in a split. Share quantities and
    # currency-per-share values change in opposite directions.
    multiplier = factor if unit == SHARES else 1 / factor if unit.endswith('/' + SHARES) else Decimal(1)
    original = fact['fact_id']
    identity = hashlib.sha256(json.dumps([VERSION,original,evidence['basis_id'],str(factor),
        review['source_trace'],evidence['source_trace']],sort_keys=True).encode()).hexdigest()
    from .eps_calculation import rounding
    return {**fact, 'value':str(value * multiplier), 'exact_value':str(value * multiplier),
        'fact_id':identity, 'input_fact_ids':[original],
        'normalized_rounding_tolerance':str(rounding(fact) * abs(multiplier)),
        'split_normalization':{'version':VERSION,'factor':str(factor),'original_value':str(value),
            'original_fact_id':original,'unit_as_of':review['unit_as_of'],'basis_id':evidence['basis_id']},
        'source_trace':{**fact.get('source_trace',{}),'formula_version':VERSION,
            'formula':'source value * reviewed share-unit multiplier', 'multiplier':str(multiplier),
            'input_fact_ids':[original],'unit_review':review['source_trace'],
            'split_history':evidence['source_trace']}}


def normalize_prices(snapshot, evidence):
    if snapshot.get('adjustment') != 'raw' or not valid_history(evidence):
        return snapshot
    rows = []
    for row in snapshot.get('rows', []):
        day = str(row.get('date', ''))[:10]
        if not evidence['start'] <= day <= evidence['end']:
            continue  # Never join incompatible raw/normalized prices in one line.
        factor = factor_after(evidence, day)
        fresh = {**row, 'raw_ohlcv':{k:row.get(k) for k in ('open','high','low','close','volume')},
                 'share_unit_factor':str(factor)}
        for field in ('open','high','low','close'):
            if row.get(field) is not None:
                fresh[field] = float(Decimal(str(row[field])) / factor)
        if row.get('volume') is not None:
            fresh['volume'] = float(Decimal(str(row['volume'])) * factor)
        rows.append(fresh)
    return {**snapshot,'rows':rows,'adjustment':'split', 'normalization_version':VERSION,
        'per_share_basis':evidence['basis_id'],'per_share_basis_start':evidence['start'],
        'per_share_basis_end':evidence['end'],'share_basis_evidence':evidence['source_trace']}
