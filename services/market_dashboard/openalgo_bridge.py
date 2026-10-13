"""Private normalized data boundary. Never calls public chart routes or a broker."""
import hmac
import os
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, HTTPException, Request

from .bars import aggregate_bars
from .price_history import price_bounds

router = APIRouter()


def internal_key():
    path = os.getenv('MARKET_INTERNAL_KEY_FILE')
    return Path(path).read_text().strip() if path else ''


def check_internal(request):
    expected = internal_key()
    if not expected or not hmac.compare_digest(request.headers.get('X-Market-Key', ''), expected):
        raise HTTPException(403, 'Internal service only')


@router.get('/internal/market-data/symbols')
def symbols(request: Request):
    check_internal(request)
    from .app import get_store
    return {'symbols': sorted(get_store().listed_codes())}


@router.get('/internal/market-data/health')
def diagnostics(request: Request):
    check_internal(request)
    from .app import get_store
    import shutil
    store = get_store()
    disk = shutil.disk_usage(store.root)
    return {'release': os.getenv('MARKET_RELEASE', 'local'),
            'providers': {name: store.get(name, {}) for name in ('provider-toss', 'provider-toss-stream')},
            'worker': store.get('worker', {}),
            'dated_accounts': store.get('dart-period-progress', {}),
            'toss_backfill': store.get('toss-backfill-progress', {}),
            'disk_used_fraction': (disk.total - disk.free) / disk.total,
            'disk_free_bytes': disk.free}


@router.get('/internal/market-data/{code}/{kind}')
def canonical(code: str, kind: str, request: Request, interval: str = 'D', start: str | None = None, end: str | None = None):
    check_internal(request)
    from .app import get_store, valid_code
    valid_code(code)
    store = get_store()
    if code not in store.listed_codes():
        raise HTTPException(404, 'Unknown security')
    if kind == 'quotes':
        quote = store.get_quote(code)
        if quote.get('venue') != '통합':
            raise HTTPException(404, 'Integrated quote not collected')
        return {'ltp': quote.get('price'), 'timestamp': quote.get('as_of'), 'venue': '통합'}
    if kind == 'depth':
        snapshot = store.get('depth:'+code, {})
        if snapshot.get('venue') != '통합':
            raise HTTPException(404, 'Integrated depth not collected')
        return snapshot
    if kind != 'history' or interval not in ('D','W','M','1m','5m'):
        raise HTTPException(422, 'Unsupported read')
    for bound in (start,end):
        if bound:
            try:
                datetime.strptime(bound, '%Y-%m-%d')
            except ValueError:
                raise HTTPException(422, 'Invalid date')
    if start and end and start > end:
        raise HTTPException(422, 'Invalid date range')
    period = {'D':'day','W':'week','M':'month','1m':'1min','5m':'5min'}[interval]
    snapshot = store.get(f'bars:{code}:통합:'+('day' if interval in ('D','W','M') else period), {})
    if interval in ('D','W','M'):
        rows = aggregate_bars(snapshot.get('rows', []), interval=period, start=start, end=end)['rows']
    else:
        rows = [r for r in snapshot.get('rows', []) if (not start or r['date']>=start) and (not end or r['date']<=end)]
    rows = [{**r, 'timestamp': int((datetime.fromisoformat(r['time']) if r.get('time') else datetime.combine(datetime.fromisoformat(r['date']).date(),time(),ZoneInfo('Asia/Seoul'))).timestamp())} for r in rows]
    return {'rows': rows, 'venue':'통합','adjustment':snapshot.get('adjustment'), 'source_trace':snapshot.get('source_trace'), 'status':snapshot.get('status','not_collected')}


def history_via_openalgo(code, interval, start, end):
    """The sidecar uses upstream OpenAlgo services and our private adapter."""
    url = os.getenv('MARKET_OPENALGO_URL')
    if not url:
        return None
    if start is None or end is None:
        from .app import get_store

        store = get_store()
        key = f"bars:{code}:통합:" + ('5min' if interval=='5min' else 'day')
        first, last = price_bounds(store, store.metadata(key,{}) or {})
        start, end = start or first, end or last
        if start is None or end is None:
            return None  # No invented start date or lifetime bound.
    response = httpx.post(url.rstrip('/')+'/api/v1/history', headers={'X-Market-Key':internal_key()},
        json={'symbol':code,'exchange':'TOSS_KR','interval':{'day':'D','week':'W','month':'M','5min':'5m'}[interval],
              'start_date':start,'end_date':end}, timeout=6)
    response.raise_for_status()
    result = response.json()
    if result.get('status') != 'success':
        raise ValueError('OpenAlgo history failed')
    return result['data']
