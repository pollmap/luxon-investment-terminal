"""Reported annual amounts with independently verified fiscal-period contexts."""
from datetime import date
import re

from .analysis import numeric
from .financial_dates import publication_date

VERSION = 'financial-timeline-v3'

ACCOUNTS = {'매출액': {'ifrs-full_Revenue','ifrs_Revenue'},
            '영업이익': {'dart_OperatingIncomeLoss'},
            '당기순이익': {'ifrs-full_ProfitLoss','ifrs_ProfitLoss'}}


def _publication(fact, period_end):
    """Period evidence and publication evidence are independent contracts.

    Collector trace clocks are never filing clocks. A valid reported amount and
    fiscal context survive an unavailable/retracted/contradictory publication.
    """
    for source in (fact, fact.get('source_trace') or {}):
        flags = source.get('quality_flags') or ()
        if isinstance(flags, str):
            flags = (flags,)
        if isinstance(flags, dict):
            flags = tuple(key for key,value in flags.items() if value)
        if not isinstance(flags, (list, tuple, set)):
            return None, False, 'publication_time_unverified'
        if 'publication_time_unverified' in flags:
            return None, False, 'publication_time_unverified'
    explicit = {key:fact.get(key) for key in (
        'available_at','published_at','publication_date','rcept_dt')}
    available_at, date_only = publication_date(explicit)
    if available_at is None:
        return None, False, 'publication_not_available'
    if date.fromisoformat(available_at[:10]) < period_end:
        return None, False, 'publication_before_period_end'
    return available_at, date_only, None


def annual_contexts(official):
    """Project only explicit duration facts; a year label never supplies dates."""
    result = []
    names = {account: name for name, accounts in ACCOUNTS.items() for account in accounts}
    for fact in (*official.get('period_facts', []), *official.get('xbrl_facts', [])):
        if not isinstance(fact, dict) or fact.get('period_verified') is not True:
            continue
        name = names.get(fact.get('account_id'))
        if name is None and fact.get('account_id') == 'IS:' + str(fact.get('name')):
            name = fact.get('name') if fact.get('name') in ACCOUNTS else None
        basis = fact.get('basis')
        if name is None or basis not in ('CFS', 'OFS') or fact.get('currency') != 'KRW':
            continue
        unit = fact.get('unit')
        if fact.get('kind') not in (None, 'duration') or not (unit in ('원', 'KRW') or
                isinstance(unit, str) and unit.endswith('}KRW') and '/' not in unit and '*' not in unit):
            continue
        try:
            start, end = (date.fromisoformat(fact[key]) for key in ('period_start', 'period_end'))
        except (KeyError, TypeError, ValueError):
            continue
        if not 350 <= (end - start).days + 1 <= 380:
            continue
        dimensions = fact.get('dimensions') or {}
        if not isinstance(dimensions, dict):
            continue
        dimensions = {str(k).rsplit('}', 1)[-1]: str(v).rsplit('}', 1)[-1]
                      for k, v in dimensions.items()}
        axis = 'ConsolidatedAndSeparateFinancialStatementsAxis'
        if dimensions and dimensions != {axis: {'CFS': 'ConsolidatedMember',
                                               'OFS': 'SeparateMember'}[basis]}:
            continue
        trace = fact.get('source_trace') or {}
        receipt = str(fact.get('filing_id') or trace.get('filing_id') or '')
        value = numeric(fact.get('exact_value', fact.get('value')))
        digest = trace.get('sha256') or trace.get('source_document_id')
        if (not trace or len(receipt) != 14 or not receipt.isdigit() or value is None
                or not re.fullmatch(r'[0-9a-f]{64}', str(digest or ''))
                or fact.get('quality_status') in ('rejected', 'unverified', 'revoked')
                or trace.get('quality_status') in ('rejected', 'unverified', 'revoked')):
            continue
        available_at, date_only, publication_reason = _publication(fact, end)
        result.append({'name': name, 'basis': basis, 'filing_id': receipt,
                       'period_start': start.isoformat(), 'period_end': end.isoformat(),
                       'exact_value': str(value), 'available_at': available_at,
                       'publication_date_only': date_only,
                       'publication_verified': available_at is not None,
                       'publication_reason': publication_reason})
    return result


def _period(row, name, basis, value, contexts):
    trace = row.get('source_trace') or {}
    receipt = str(row.get('receipt') or trace.get('filing_id') or '')
    matches = [f for f in contexts if f.get('name') == name and f.get('basis') == basis
               and f.get('filing_id') == receipt and numeric(f.get('exact_value')) == value]
    periods = {(f['period_start'], f['period_end']) for f in matches}
    if len(periods) != 1:
        return {'period_start': None, 'period_end': None, 'period_verified': False,
                'available_at': None,
                'publication_verified':False, 'publication_reason':'actual_period_unverified',
                'period_reason': 'conflicting_period_contexts' if periods else 'actual_period_unverified'}
    start, end = next(iter(periods))
    usable = [f for f in matches if f.get('publication_verified') is True and f.get('available_at')]
    clocks = {f['available_at'] for f in usable}
    blockers = {f.get('publication_reason') for f in matches} & {
        'publication_time_unverified','publication_before_period_end'}
    available_at = next(iter(clocks)) if len(clocks)==1 and not blockers else None
    reason = ('publication_before_period_end' if 'publication_before_period_end' in blockers
              else 'publication_time_unverified' if blockers
              else 'conflicting_publication_contexts' if len(clocks)>1
              else 'publication_not_available' if available_at is None else None)
    return {'period_start': start, 'period_end': end, 'period_verified': True,
            'available_at': available_at,
            'publication_verified':available_at is not None, 'publication_reason':reason,
            'publication_date_only': bool(available_at) and all(f.get('publication_date_only', True) for f in usable),
            'period_reason': None}


def annual_timeline(store, reports, basis='CFS', *, contexts=()):
    result=[]
    for report in sorted(reports.values(),key=lambda r:r.get('year','')):
        if report.get('status')!='ready' or report.get('basis')!=basis or report.get('report_code')!='11011': continue
        snapshot=store.get(report['snapshot_key'],{}) if report.get('snapshot_key') else report
        for name,accounts in ACCOUNTS.items():
            rows=[r for r in snapshot.get('rows',[]) if r.get('statement') in ('IS','CIS') and r.get('unit')=='KRW' and r.get('account_id') in accounts]
            values={numeric(r.get('current')) for r in rows if numeric(r.get('current')) is not None}
            if len(values)!=1: continue
            value=values.pop()
            result.append({'name':name,'period':report['year'],'frequency':'annual','basis':basis,
                           'estimate':False,'value':float(value/100000000),'unit':'억원',
                           'source_trace':rows[0].get('source_trace'),
                           **_period(rows[0], name, basis, value, contexts)})
    return {'rows':result,'basis':basis,'status':'ready' if result else 'pending',
            'revision': VERSION,
            'period_coverage': {'reported': len(result),
                                'verified': sum(row['period_verified'] for row in result)}}
