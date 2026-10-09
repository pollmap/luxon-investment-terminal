"""Read stored analysis evidence; never turn older verified TTM into latest readiness.

Run inside the market service: python -m scripts.audit_market_analysis --codes ...
No provider calls, credentials or full source payloads are emitted.
"""
import argparse
import json
from collections import Counter

from services.market_dashboard.analysis_inputs import security_facts, analysis_prices, metric_availability
from services.market_dashboard.eps_calculation import ttm_readiness
from services.market_dashboard.store import Store, now


def inspect(store, codes):
    known = set(store.listed_codes())
    result = []
    for code in codes:
        if code not in known:
            result.append({'code': code, 'status': 'outside_service_universe'})
            continue
        facts, official = security_facts(store, code)
        prices, _ = analysis_prices(store, code, 'auto', 5)
        metrics = metric_availability(facts, prices, code)['metrics']
        ttm = official.get('ttm_eps') or {}
        reported = (official.get('eps') or {}).get('rows', [])
        bases = {}
        for basis in ('CFS', 'OFS'):
            inputs = [r for r in ttm.get('inputs', []) if r.get('basis') == basis]
            rows = [r for r in ttm.get('rows', []) if r.get('basis') == basis]
            ready = [r for r in rows if r.get('status') == 'ready']
            bases[basis] = {
                'latest': ttm_readiness(inputs, rows, [r for r in reported if r.get('basis') == basis]),
                'ready_periods': len({(r['period_start'], r['period_end']) for r in ready}),
                'ready_from': min((r['period_end'] for r in ready), default=None),
                'ready_to': max((r['period_end'] for r in ready), default=None),
                'input_periods_without_share_basis': sorted({
                    (r['period_start'], r['period_end'], r.get('filing_id'))
                    for r in inputs if not r.get('per_share_basis')}),
            }
        result.append({'code': code, 'ttm': bases,
            'rejected_inputs': ttm.get('rejected', {}),
            'share_basis_id': prices.get('per_share_basis'),
            'share_basis_end': prices.get('per_share_basis_end'),
            'metrics': [{k: m.get(k) for k in ('code','status','comparison_available',
                'comparison_start','comparison_end','comparison_periods','comparison_frequencies')}
                for m in metrics]})
    counts = Counter(row['ttm']['CFS']['latest']['status'] for row in result if 'ttm' in row)
    return {'checked_at': now(), 'scope': 'stored calculation evidence; excludes source review, UI and live-stream completion',
            'requested': len(codes), 'latest_cfs_ttm_statuses': dict(counts), 'securities': result}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--codes', default='005930,000660,005380')
    args = parser.parse_args()
    print(json.dumps(inspect(Store(), list(dict.fromkeys(args.codes.split(',')))), ensure_ascii=False))
