"""Toss's own sector taxonomy. Never substitutes official industries or themes."""
import re
from decimal import Decimal

from .toss import numeric
from .service_scope import universe_view

DURATIONS = ('1d', '1w', '1mo', '3mo', '6mo', '1y')
RANK_TYPES = ('TOP_GAINERS', 'MARKET_TRADING_AMOUNT')


def valid_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', value):
        raise ValueError('Invalid sector identifier')
    return value


def percentage(value):
    return numeric(Decimal(str(value)) * 100) if value is not None else None


def identity(row):
    sector_id = valid_id(row['sectorId'])
    parent = row.get('parentSectorId')
    if parent is not None:
        valid_id(parent)
    depth = row['depth']
    if type(depth) is not int or depth not in (0, 1, 2) or not row.get('name'):
        raise ValueError('Invalid sector hierarchy')
    if (depth == 0) != (parent is None) or parent == sector_id:
        raise ValueError('Invalid sector parent')
    return {'id': sector_id, 'name': row['name'], 'parent_id': parent, 'depth': depth}


def context(**data):
    return {'classification': 'toss_sector', 'market_country': 'KR',
            'unit': '원', 'change_unit': '%', **data}


async def collect_catalogue(market):
    data, evidence = await market.client.get('/api/v1/sectors')
    rows = [identity(row) for row in data['sectors']]
    by_id = {r['id']: r for r in rows}
    if len(by_id) != len(rows):
        raise ValueError('Duplicate sector identifier')
    for row in rows:
        if row['parent_id'] and (row['parent_id'] not in by_id or
                by_id[row['parent_id']]['depth'] != row['depth'] - 1):
            raise ValueError('Incomplete sector hierarchy')
    market.save('toss-sectors', context(rows=rows), evidence)


async def collect_ranking(market, kind, duration):
    if kind not in RANK_TYPES or duration not in DURATIONS:
        raise ValueError('Unsupported sector ranking')
    data, evidence = await market.client.get('/api/v1/sectors/rankings',
        {'type': kind, 'duration': duration, 'marketCountry': 'KR'})
    rows = []
    seen = set()
    for row in data['rankings']:
        sector_id = valid_id(row['sectorId'])
        if row.get('currency') != 'KRW' or sector_id in seen:
            raise ValueError('Invalid sector ranking currency or duplicate')
        seen.add(sector_id)
        rows.append({'id': sector_id, 'name': row['name'], 'rank': row['rank'],
                     'change': percentage(row.get('changeRate')),
                     'value': numeric(row.get('tradingAmount')),
                     'cap': numeric(row.get('marketCapitalization'))})
    market.save(f'toss-sector-ranking:{kind}:{duration}',
                context(rows=rows, type=kind, duration=duration, overlapping_membership=True), evidence)


async def collect_detail(market, sector_id):
    valid_id(sector_id)
    data, evidence = await market.client.get(f'/api/v1/sectors/{sector_id}', {'marketCountry': 'KR'})
    row = identity(data)
    if row['id'] != sector_id:
        raise ValueError('Sector identity mismatch')
    performance = {}
    for item in data.get('performance', {}).get('changeRates', []):
        duration = item['duration']
        if duration not in DURATIONS or duration in performance:
            raise ValueError('Unknown or duplicate performance duration')
        performance[duration] = percentage(item.get('changeRate'))
    market.save('toss-sector:'+sector_id, context(**row, summary=data.get('summary'),
                performance=[{'duration': d, 'change': performance.get(d)} for d in DURATIONS]), evidence)


async def collect_stocks(market, sector_id):
    valid_id(sector_id)
    data, evidence = await market.client.get(f'/api/v1/sectors/{sector_id}/stocks', {'marketCountry': 'KR'})
    known = {r['code']: r for r in universe_view(market.store.get('universe', {})).get('rows', [])}
    rows, excluded = [], []
    seen = set()
    for item in data['stocks']:
        code = item['symbol']
        if code in seen or item.get('currency') != 'KRW':
            raise ValueError('Duplicate sector member or unexpected currency')
        seen.add(code)
        if code not in known:
            excluded.append(code)
            continue
        rows.append({'code': code, 'name': known[code]['name'], 'market': known[code].get('market'),
                     'volume': numeric(item.get('volume')), 'value': numeric(item.get('tradingAmount')),
                     'cap': numeric(item.get('marketCapitalization'))})
    market.save('toss-sector-stocks:'+sector_id, context(id=sector_id, rows=rows,
                membership_scope='common_shares_including_child_sectors',
                observation_basis='latest', excluded_count=len(excluded)), evidence)


def attach_routes(app, base, get_store):
    from fastapi import HTTPException, Query
    from .toss_market import public_snapshot, request_market

    def read(key, operation, **params):
        store = get_store()
        # A transient enqueue failure must not hide the last good snapshot.
        result = public_snapshot(store, key)
        scheduled = request_market(store, key, operation, **params)
        return result or {'rows': [], 'status': 'pending' if scheduled else 'error',
                          'classification': 'toss_sector'}

    def check(sector_id):
        try:
            valid_id(sector_id)
        except ValueError as exc:
            raise HTTPException(422, 'Invalid sector identifier') from exc
        catalogue = get_store().get('toss-sectors', {})
        if catalogue.get('status') == 'ready' and not any(r['id'] == sector_id for r in catalogue.get('rows', [])):
            raise HTTPException(404, 'Unknown sector')

    @app.get(base+'/sectors/catalogue')
    def sector_catalogue():
        return read('toss-sectors', 'sector_catalogue')

    @app.get(base+'/sectors/rankings')
    def sector_rankings(type: str = Query('MARKET_TRADING_AMOUNT'), duration: str = '1d'):
        if type not in RANK_TYPES or duration not in DURATIONS:
            raise HTTPException(422, 'Unsupported sector ranking')
        return read(f'toss-sector-ranking:{type}:{duration}', 'sector_ranking', kind=type, duration=duration)

    @app.get(base+'/sectors/{sector_id}/stocks')
    def sector_stocks(sector_id: str):
        check(sector_id)
        return read('toss-sector-stocks:'+sector_id, 'sector_stocks', sector_id=sector_id)

    @app.get(base+'/sectors/{sector_id}')
    def sector_detail(sector_id: str):
        check(sector_id)
        return read('toss-sector:'+sector_id, 'sector_detail', sector_id=sector_id)
