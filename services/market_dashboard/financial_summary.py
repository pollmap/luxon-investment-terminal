"""Small, source-backed TTM read model; never aggregate business-segment totals."""
from .analysis import trailing_twelve_months

ACCOUNTS = {
    '매출액': ('revenue', '매출액'), '수익(매출액)': ('revenue', '매출액'),
    'Revenue': ('revenue', '매출액'),
    '영업이익': ('operating_income', '영업이익'), '영업이익(손실)': ('operating_income', '영업이익'),
    'OperatingIncomeLoss': ('operating_income', '영업이익'),
    '당기순이익': ('net_income', '당기순이익'), '당기순이익(손실)': ('net_income', '당기순이익'),
    'ProfitLoss': ('net_income', '당기순이익'),
    'CashFlowsFromUsedInOperatingActivities': ('operating_cash', '영업현금흐름'),
    'CashFlowsFromUsedInInvestingActivities': ('investing_cash', '투자현금흐름'),
    'CashFlowsFromUsedInFinancingActivities': ('financing_cash', '재무현금흐름'),
}


def ttm_summary(official, basis='CFS'):
    if basis not in ('CFS', 'OFS'):
        raise ValueError('Invalid accounting basis')
    facts = official.get('period_facts', []) + official.get('xbrl_facts', [])
    facts = [f for f in facts if f.get('basis') == basis and f.get('currency') == 'KRW'
             and all('ConsolidatedAndSeparate' in key for key in (f.get('dimensions') or {}))]
    calculated = trailing_twelve_months(facts)
    selected = {}
    for row in calculated['rows']:
        name = row['account_id'].rsplit('}', 1)[-1].removeprefix('IS:')
        identity = ACCOUNTS.get(name)
        if not identity:
            continue
        key, label = identity
        candidate = {**row, 'metric': key, 'name': label, 'unit': '원'}
        prior = selected.get(key)
        # Prefer a newer complete period; the dated major-account API is the
        # stable fallback when the equivalent taxonomy observation duplicates it.
        priority = (row['period_end'], row['account_id'].startswith('IS:'))
        old_priority = (prior['period_end'], prior['account_id'].startswith('IS:')) if prior else ('', False)
        if priority > old_priority:
            selected[key] = candidate
    order = ['revenue', 'operating_income', 'net_income', 'operating_cash', 'investing_cash', 'financing_cash']
    latest = max((row['period_end'] for row in selected.values()), default=None)
    current = [selected[key] for key in order if key in selected and selected[key]['period_end'] == latest]
    return {'basis': basis, 'status': 'ready' if selected else 'period_evidence_required',
            'rows': current, 'period_end': latest,
            'older_periods': {key: row['period_end'] for key, row in selected.items() if row['period_end'] != latest},
            'per_share_status': 'not_calculated', 'formula': 'sum(verified standalone quarters)'}
