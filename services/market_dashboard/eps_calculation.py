"""Basic EPS reconciliation and period-weighted TTM. Never sum reported EPS."""
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json

from .analysis import numeric
from .financial_dates import publication_date
from .taxonomy import full_ifrs_account

VERSION = 'basic-eps-weighted-v4'
EPS_ACCOUNTS = {'BasicEarningsLossPerShare', 'WeightedAverageShares',
    'ProfitLossAttributableToOrdinaryEquityHoldersOfParentEntity', 'ProfitLossAttributableToOwnersOfParent'}

def local(value):
    return value.rsplit('}',1)[-1]

def days(start,end):
    return (date.fromisoformat(end)-date.fromisoformat(start)).days+1

def rounding(fact):
    if fact.get('split_normalization'):
        tolerance = numeric(fact.get('normalized_rounding_tolerance'))
        if tolerance is not None and tolerance >= 0:
            return tolerance
    decimals = fact.get('decimals')
    if decimals not in (None,'INF'):
        try:
            return Decimal(10)**(-int(decimals))/2
        except (ValueError,OverflowError):
            pass
    # EPS is published to the source's displayed decimal places. Source facts
    # remain unmodified, and both the tolerance and the residual are disclosed.
    # DART can repeat the same integer EPS as 3834 and 3834.0 in one filing.
    # A formatting zero must not change reconciliation or depend on DB row order.
    # Keep integer precision even when Decimal.normalize() produces 1E+3.
    exponent=min(0,Decimal(str(fact['value'])).normalize().as_tuple().exponent)
    return Decimal(10)**exponent/2

def eps_inputs(facts):
    grouped, rejected = defaultdict(lambda:defaultdict(list)), Counter()
    for fact in facts:
        name = local(fact.get('account_id',''))
        from .reviewed_notes import verified_note
        if name not in EPS_ACCOUNTS or (not full_ifrs_account(fact.get('account_id','')) and not verified_note(fact)):
            continue
        dims = {local(k):local(v) for k,v in (fact.get('dimensions') or {}).items()}
        if set(dims)-{'ConsolidatedAndSeparateFinancialStatementsAxis','ClassesOfShareCapitalAxis'}:
            rejected['dimension_unverified']+=1
            continue
        if dims.get('ClassesOfShareCapitalAxis','OrdinarySharesMember')!='OrdinarySharesMember':
            continue
        scope=dims.get('ConsolidatedAndSeparateFinancialStatementsAxis')
        if scope and {'ConsolidatedMember':'CFS','SeparateMember':'OFS'}.get(scope)!=fact.get('basis'):
            rejected['accounting_scope_conflict']+=1
            continue
        if name=='ProfitLossAttributableToOwnersOfParent' and dims.get('ClassesOfShareCapitalAxis')!='OrdinarySharesMember':
            continue  # Undivided parent profit is not common-share earnings.
        try:
            length=days(fact['period_start'],fact['period_end'])
        except (KeyError,ValueError,TypeError):
            continue
        publication,_=publication_date(fact)
        if not publication or publication[:10]<fact['period_end']:
            rejected['publication_evidence_required']+=1
            continue
        if not (fact.get('company_id') or fact.get('entity_id')) or not fact.get('filing_id') or not 70<=length<=380 or not fact.get('period_verified') or fact.get('basis') not in ('CFS','OFS') or not fact.get('source_trace') or not fact.get('fact_id') or numeric(fact.get('value')) is None:
            rejected['evidence_required']+=1
            continue
        role='eps' if name=='BasicEarningsLossPerShare' else 'shares' if name=='WeightedAverageShares' else 'profit'
        unit=fact.get('unit','')
        valid_unit=(unit=='{http://www.xbrl.org/2003/instance}shares') if role=='shares' else (fact.get('currency')=='KRW' and (unit.endswith('KRW/{http://www.xbrl.org/2003/instance}shares') if role=='eps' else unit.endswith('}KRW')))
        if not valid_unit:
            rejected['unit_unverified']+=1
            continue
        key=(fact.get('company_id') or fact.get('entity_id'),fact['basis'],fact['period_start'],fact['period_end'],fact.get('filing_id'))
        grouped[key][role].append(fact)
    rows=[]
    for (company,basis,start,end,filing),groups in grouped.items():
        if set(groups)!={'profit','shares','eps'}:
            rejected['numerator_denominator_or_eps_missing']+=1
            continue
        if any(len({numeric(f['value']) for f in fs})!=1 for fs in groups.values()):
            rejected['conflicting_facts']+=1
            continue
        profit,shares,eps=(min(groups[k],key=lambda f:f['fact_id']) for k in ('profit','shares','eps'))
        denominator=numeric(shares['value'])
        if denominator<=0:
            rejected['nonpositive_shares']+=1
            continue
        amount=numeric(profit['value'])
        calculated=amount/denominator
        tolerance=rounding(eps)+rounding(profit)/denominator+abs(calculated)*rounding(shares)/denominator
        residual=abs(calculated-numeric(eps['value']))
        if residual>tolerance:
            rejected['reported_eps_mismatch']+=1
            continue
        # A reconciled EPS cannot be known before every input is public. Notes
        # may be published/corrected after the primary EPS statement.
        used = [f for fs in groups.values() for f in fs]
        publication = max(publication_date(f)[0] for f in used)
        basis_ids={f.get('per_share_basis') for fs in groups.values() for f in fs}
        share_basis=next(iter(basis_ids)) if len(basis_ids)==1 and None not in basis_ids else None
        rows.append({'company_id':company,'basis':basis,'share_class':'ordinary','share_class_verified':True,
            'period_start':start,'period_end':end,'period_verified':True,'days':days(start,end),
            'profit':str(amount),'weighted_shares':str(denominator),'reported_eps':str(numeric(eps['value'])),
            'value':str(calculated),'reconciliation_residual':str(residual),'reconciliation_tolerance':str(tolerance),
            'published_at':publication,'filing_id':filing,'currency':'KRW','unit':'원/주',
            'input_fact_ids':sorted({f['fact_id'] for fs in groups.values() for f in fs}),
            'source_trace':{**eps['source_trace'], 'available_at':publication,
                'formula':'ordinary attributable profit / period weighted ordinary shares',
                'input_fact_ids':sorted({f['fact_id'] for f in used}), 'formula_version':VERSION},
            'per_share_basis':share_basis,'formula_version':VERSION})
    return {'rows':rows,'rejected':dict(rejected)}

def _same_verified_ocf_input(left, right):
    """An unchanged comparative is not a new accounting amount or share unit."""
    fields = (
        'company_id', 'basis', 'metric', 'period_start', 'period_end', 'share_class',
        'currency', 'unit', 'days', 'formula', 'formula_version', 'per_share_basis',
        'attribution', 'dimensions',
    )
    return (
        left.get('metric') == right.get('metric') == 'OCF'
        and bool(right.get('per_share_basis'))
        and all(row.get('period_verified') is True
                and row.get('share_class_verified') is True for row in (left, right))
        and all(left.get(key) == right.get(key) for key in fields)
        and all(numeric(left.get(key)) is not None
                and numeric(left.get(key)) == numeric(right.get(key))
                for key in ('profit', 'weighted_shares'))
    )


def quarters_at(rows, as_of):
    eligible=[r for r in rows if r.get('published_at') and r['published_at']<=as_of]
    latest={}
    for row in sorted(eligible,key=lambda r:(r['published_at'],r.get('filing_id') or '')):
        latest[(row['period_start'],row['period_end'])]=row
    quarters={}
    for row in latest.values():
        if 70<=row['days']<=100:
            quarters[(row['period_start'],row['period_end'])]=row
    for row in latest.values():
        if row['days']<=100:
            continue
        preceding=[r for r in latest.values() if r['period_start']==row['period_start'] and 70<=days(r['period_end'],row['period_end'])-1<=100]
        if not preceding:
            continue
        previous=max(preceding,key=lambda r:r['period_end'])
        # A newly corrected earlier cumulative amount cannot be subtracted from
        # an older, unrevised later amount. Wait for matching later disclosure.
        if previous['published_at'] > row['published_at']:
            if (row.get('metric') != 'OCF' or previous.get('metric') != 'OCF'
                    or row.get('per_share_basis') != previous.get('per_share_basis')):
                continue
            versions = [r for r in eligible if (r['period_start'], r['period_end'])
                        == (previous['period_start'], previous['period_end'])]
            earlier = [r for r in versions if r['published_at'] <= row['published_at']]
            original = max(earlier, key=lambda r: (r['published_at'], r.get('filing_id') or ''),
                           default=None)
            # Keep original public evidence only for an unchanged comparative.
            # A real correction, even if later reversed, must remain blocked.
            if (original is None or any(not _same_verified_ocf_input(version, previous)
                    for version in versions
                    if version['published_at'] >= original['published_at'])):
                continue
            previous = original
        start=(date.fromisoformat(previous['period_end'])+timedelta(days=1)).isoformat()
        key=(start,row['period_end'])
        if key in quarters and quarters[key]['published_at'] >= row['published_at']:
            continue
        length=days(*key)
        share_days=numeric(row['weighted_shares'])*row['days']-numeric(previous['weighted_shares'])*previous['days']
        if share_days<=0 or row.get('per_share_basis')!=previous.get('per_share_basis'):
            continue
        numerator = numeric(row['profit'])-numeric(previous['profit'])
        denominator = share_days/length
        ids = sorted(set(row['input_fact_ids']+previous['input_fact_ids']))
        quarters[key]={**row,'period_start':start,'days':length,
            'profit':str(numerator), 'numerator':str(numerator),
            'weighted_shares':str(denominator),
            'value':str(numerator/denominator), 'exact_value':str(numerator/denominator),
            'input_fact_ids':ids,
            'source_trace':{**row.get('source_trace', {}),
                'available_at':row['published_at'], 'input_fact_ids':ids},
            'derivation':'same_fiscal_start_cumulative_difference'}
    return sorted(quarters.values(),key=lambda r:(r['period_end'],r['period_start']))

def calculate_ttm_eps(facts):
    inputs=eps_inputs(facts)
    groups=defaultdict(list)
    for row in inputs['rows']:
        groups[(row['company_id'],row['basis'])].append(row)
    output=[]
    rejected=Counter(inputs['rejected'])
    for group in groups.values():
        seen=set()
        for publication in sorted({r['published_at'] for r in group if r['published_at']}):
            quarters=quarters_at(group,publication)
            for index in range(3,len(quarters)):
                window=quarters[index-3:index+1]
                if not all(date.fromisoformat(a['period_end'])+timedelta(days=1)==date.fromisoformat(b['period_start']) for a,b in zip(window,window[1:])):
                    continue
                total_days=sum(r['days'] for r in window)
                if not 350<=total_days<=380:
                    continue
                identity=tuple(sorted({i for r in window for i in r['input_fact_ids']}))
                if identity in seen:
                    continue
                seen.add(identity)
                bases={r.get('per_share_basis') for r in window}
                verified=len(bases)==1 and None not in bases
                weighted=sum(numeric(r['weighted_shares'])*r['days'] for r in window)/total_days
                numerator=sum(numeric(r['profit']) for r in window)
                # Arithmetic and split alignment are separate gates. A source
                # without share-basis evidence cannot produce a comparison line.
                if not verified:
                    rejected['share_basis_required']+=1
                output.append({**window[-1], 'period_start':window[0]['period_start'],
                    'frequency':'ttm','metric':'EPS','days':total_days,'profit':str(numerator),
                    'weighted_shares':str(weighted),'value':float(numerator/weighted) if verified else None,
                    'exact_value':str(numerator/weighted) if verified else None,
                    'status':'ready' if verified else 'share_basis_required',
                    'published_at':publication, 'input_fact_ids':list(identity),
                    'source_trace':{**window[-1]['source_trace'],
                        'available_at':publication, 'input_fact_ids':list(identity),
                        'formula':'sum ordinary attributable quarterly profit / day-weighted ordinary shares',
                        'formula_version':VERSION},
                    'fact_id':hashlib.sha256(json.dumps([VERSION,identity]).encode()).hexdigest(),
                    'formula_version':VERSION})
    return {'rows':output,'inputs':inputs['rows'],'rejected':dict(rejected),'formula_version':VERSION}


def ttm_readiness(inputs, ttm_rows, reported_rows):
    """A valid older year is not proof that the latest TTM can be calculated."""
    target=max((r['period_end'] for r in inputs+reported_rows),default=None)
    candidates=[r for r in ttm_rows if r['period_end']==target]
    latest=max(candidates,key=lambda r:r.get('published_at') or '',default=None)
    return {'status':latest['status'] if latest else 'period_inputs_required',
            'period_end':target,'period_start':latest.get('period_start') if latest else None,
            'input_count':len(latest.get('input_fact_ids',[])) if latest else 0,
            'price_basis_status':'requires_price_alignment'}
