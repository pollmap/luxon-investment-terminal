"""Reported EPS facts. Do not infer currency, share class, or split alignment."""
from collections import Counter, defaultdict
from datetime import date
from .analysis import numeric
from .financial_dates import publication_date
from .taxonomy import full_ifrs_account

VERSION = 'reported-eps-v2'


def reported_eps(official, metric='EPS'):
    concept = {'EPS':'BasicEarningsLossPerShare', 'EPS_DILUTED':'DilutedEarningsLossPerShare'}[metric]
    groups, rejected = defaultdict(list), Counter()
    for fact in official.get('xbrl_facts', []):
        account = fact.get('account_id', '')
        from .reviewed_notes import verified_note
        if account.rsplit('}', 1)[-1] != concept or (not full_ifrs_account(account) and not verified_note(fact)):
            continue
        dims = fact.get('dimensions') or {}
        if any(key.rsplit('}', 1)[-1] not in ('ConsolidatedAndSeparateFinancialStatementsAxis', 'ClassesOfShareCapitalAxis') for key in dims):
            rejected['dimension_unverified'] += 1
            continue
        scope = [value.rsplit('}', 1)[-1] for key, value in dims.items()
                 if key.rsplit('}', 1)[-1] == 'ConsolidatedAndSeparateFinancialStatementsAxis']
        if scope and (len(scope) != 1 or {'ConsolidatedMember':'CFS', 'SeparateMember':'OFS'}.get(scope[0]) != fact.get('basis')):
            rejected['accounting_scope_conflict'] += 1
            continue
        members = {value.rsplit('}', 1)[-1] for key,value in dims.items() if key.rsplit('}',1)[-1]=='ClassesOfShareCapitalAxis'}
        if not members or members == {'OrdinarySharesMember'}:
            share_class = 'ordinary'  # IFRS basic EPS concept denotes ordinary equity.
        elif members == {'PreferenceSharesMember'}:
            share_class = 'preferred'
        else:
            rejected['share_class_unverified'] += 1
            continue
        unit = fact.get('unit','')
        if fact.get('currency') != 'KRW' or not unit.endswith('KRW/{http://www.xbrl.org/2003/instance}shares'):
            # Older DART instances sometimes declare EPS with a plain shares unit.
            # Preserve them as source facts, never silently turn shares into won.
            rejected['per_share_unit_unverified'] += 1
            continue
        try:
            start, end = date.fromisoformat(fact['period_start']), date.fromisoformat(fact['period_end'])
        except (KeyError,TypeError,ValueError):
            rejected['period_unverified'] += 1
            continue
        days = (end-start).days+1
        frequency = 'annual' if 350 <= days <= 380 else 'quarterly' if 70 <= days <= 100 else 'cumulative' if 100 < days < 350 else None
        amount = numeric(fact.get('value'))
        if not frequency or not fact.get('period_verified') or fact.get('basis') not in ('CFS','OFS') or amount is None or not fact.get('fact_id') or not fact.get('source_trace'):
            rejected['evidence_required'] += 1
            continue
        published, _ = publication_date(fact)
        if published and published[:10] < end.isoformat():
            rejected['publication_before_period_end'] += 1
            continue
        row = {**fact, 'metric':metric, 'value':float(amount), 'exact_value':str(amount),
               'unit':'원/주', 'share_class':share_class, 'share_class_verified':True,
               'frequency':frequency, 'published_at':published, 'period':end.isoformat(),
               'formula_version':VERSION, 'per_share_basis':fact.get('per_share_basis')}
        key = (fact.get('company_id') or fact.get('entity_id'), fact['basis'], share_class,
               start.isoformat(), end.isoformat(), fact.get('filing_id'))
        groups[key].append(row)
    rows = []
    for candidates in groups.values():
        if len({numeric(row['exact_value']) for row in candidates}) != 1:
            rejected['conflicting_facts'] += len(candidates)
            continue
        # Equivalent primary-statement and note facts are one observation.
        best = max(candidates, key=lambda r:(bool(r['published_at']), len(r.get('dimensions') or {})))
        # Equivalent reported amounts are still not proof of identical split
        # units. Keep the reported figure, but do not unlock a comparison line.
        bases = {r.get('per_share_basis') for r in candidates}
        best['per_share_basis'] = next(iter(bases)) if len(bases) == 1 else None
        best['input_fact_ids'] = sorted({r['fact_id'] for r in candidates})
        rows.append(best)
    return {'rows':sorted(rows,key=lambda r:(r['period_end'], r.get('published_at') or '', r.get('filing_id') or '')),
            'rejected':dict(rejected), 'formula_version':VERSION}


def security_eps(read_model, code, corporation_id, *, basis='CFS', frequency='all'):
    """Only a DART stock-code identity can assign ordinary EPS to a security."""
    rows = []
    for row in read_model.get('rows', []):
        if (not corporation_id or (row.get('company_id') or row.get('entity_id')) != corporation_id
                or row['share_class'] != 'ordinary' or row['basis'] != basis
                or (frequency != 'all' and row['frequency'] != frequency)):
            continue
        rows.append({**row,'security_code':code})
    return rows


def latest_observations(rows):
    latest = {}
    for row in rows:
        key = (row['basis'],row['share_class'],row['period_start'],row['period_end'])
        if key not in latest or (row.get('published_at') or '',row.get('filing_id') or '') > (latest[key].get('published_at') or '',latest[key].get('filing_id') or ''):
            latest[key] = row
    return sorted(latest.values(),key=lambda r:(r['period_end'],r['period_start']),reverse=True)
