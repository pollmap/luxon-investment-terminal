"""Bounded public GET audit; never call suppliers or emit raw source traces."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import httpx


def span(rows, field):
    dates = [r[field] for r in rows if r.get(field)]
    return {"first": min(dates) if dates else None, "last": max(dates) if dates else None}


def audit(base, codes, destination):
    result = {"checked_at": datetime.now(timezone.utc).isoformat(), "base": base,
              "scope": "public API observations; not provider completeness or UI validation", "securities": {}}
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=base.rstrip('/') + '/api/market/v1/', timeout=35, follow_redirects=True) as client:
        def request(path):
            start = time.perf_counter()
            try:
                response = client.get(path)
                meta = {"http_status": response.status_code, "elapsed_ms": round((time.perf_counter()-start)*1000),
                        "server_date": response.headers.get('date')}
                if response.status_code != 200:
                    return None, meta
                payload = response.json()
                return payload, meta
            except (httpx.HTTPError, ValueError) as exc:
                return None, {"error": type(exc).__name__, "elapsed_ms": round((time.perf_counter()-start)*1000)}
        def checkpoint():
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')

        health, _ = request('health')
        result['release_before'] = (health or {}).get('release')
        checkpoint()
        for code in codes:
            summary = result['securities'][code] = {}
            payload, meta = request(f'securities/{code}/analysis/metrics')
            summary['metrics'] = {"request": meta, "rows": [{k: m.get(k) for k in (
                'code', 'available', 'comparison_available', 'start', 'end', 'frequencies',
                'comparison_start', 'comparison_end', 'comparison_periods', 'comparison_frequencies', 'status', 'reason')}
                for m in (payload or {}).get('metrics', [])]}
            payload, meta = request(f'securities/{code}/financials/per-share')
            rows = (payload or {}).get('rows', [])
            summary['eps'] = {"request": meta, **{k: (payload or {}).get(k) for k in
                ('status', 'comparison_status', 'ttm_status', 'ttm_readiness', 'rejected')},
                "row_count": len(rows), "period_end": span(rows, 'period_end'),
                "frequencies": dict(Counter(r.get('frequency') for r in rows)),
                "reconciled_input_count": len((payload or {}).get('reconciled_inputs', []))}
            payload, meta = request(f'securities/{code}/events')
            rows = [r for r in (payload or {}).get('rows', []) if r.get('kind') == 'dividend' and not r.get('superseded_by')]
            summary['dividends'] = {"request": meta, "status": (payload or {}).get('status'),
                "row_count": len(rows), "record_dates": span(rows, 'record_date'),
                "payment_dates": span(rows, 'payment_date'), "ex_dates": span(rows, 'ex_date'),
                "confirmed_payment_count": sum(bool(r.get('payment_date')) and r.get('payment_status') == 'confirmed' for r in rows),
                "announced_payment_count": sum(r.get('payment_status') == 'announced' for r in rows),
                "verified_count": sum(r.get('verified') is True for r in rows),
                "status_counts": dict(Counter(r.get('status') for r in rows))}
            summary['valuation'] = {}
            for metric in ('EPS', 'OCF', 'EBITDA'):
                payload, meta = request(f'securities/{code}/analysis/valuation?metric={metric}&years=5')
                series, periods = (payload or {}).get('series', []), (payload or {}).get('periods', [])
                summary['valuation'][metric] = {"request": meta, **{k: (payload or {}).get(k) for k in
                    ('status', 'metric', 'basis', 'frequency', 'multiple', 'multiple_source', 'availability', 'revision')},
                    "price_count": len(series), "price_dates": span(series, 'date'),
                    "comparison_count": sum(r.get('comparison_eligible') is True for r in series),
                    "reference_line_count": sum(r.get('reference_price') is not None for r in series),
                    "comparison_dates": span([r for r in series if r.get('comparison_eligible')], 'date'),
                    "period_count": len(periods), "period_end": span(periods, 'period_end'),
                    "period_reasons": dict(Counter(r.get('reason') for r in periods)),
                    "series_reasons": dict(Counter(r.get('reason') for r in series))}
                checkpoint()
            print(json.dumps({"code": code, "ttm_status": summary['eps']['ttm_status'],
                              "dividends": summary['dividends']['row_count'],
                              "valuation_status": {m: r['status'] for m, r in summary['valuation'].items()}}, ensure_ascii=False), flush=True)
        health, _ = request('health')
        result['release_after'] = (health or {}).get('release')
        result['same_release'] = bool(result.get('release_before') and result.get('release_before') == result.get('release_after'))
        checkpoint()
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='https://stocks.62-171-141-206.sslip.io')
    parser.add_argument('--codes', default='005930,000660,005380,005935')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    codes = args.codes.split(',')
    if len(codes) > 10 or any(len(code) != 6 or not code.isalnum() for code in codes):
        raise SystemExit('Select at most 10 security codes')
    audit(args.base, codes, args.output)
