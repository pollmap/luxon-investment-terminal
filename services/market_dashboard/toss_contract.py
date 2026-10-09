"""Toss OpenAPI 1.2.24: 24 in-scope public GETs; sector ETF lookup excluded."""
import re

PUBLIC_GET_PATHS = (
    '/api/v1/prices', '/api/v1/orderbook', '/api/v1/trades', '/api/v1/price-limits',
    '/api/v1/candles', '/api/v1/stocks', '/api/v1/stocks/all',
    '/api/v1/stocks/{symbol}/warnings', '/api/v1/stocks/{symbol}/investor-trading',
    '/api/v1/stocks/{symbol}/program-trades', '/api/v1/stocks/{symbol}/short-selling',
    '/api/v1/stocks/{symbol}/credit-trades', '/api/v1/stocks/{symbol}/securities-lending',
    '/api/v1/exchange-rate', '/api/v1/market-calendar/KR', '/api/v1/market-calendar/US',
    '/api/v1/rankings', '/api/v1/market-indicators/prices',
    '/api/v1/market-indicators/{symbol}/candles',
    '/api/v1/market-indicators/{symbol}/investor-trading',
    '/api/v1/sectors', '/api/v1/sectors/rankings',
    '/api/v1/sectors/{sectorId}', '/api/v1/sectors/{sectorId}/stocks',
)
INDICATORS = ('KOSPI', 'KOSDAQ', *(f'KR_BOND_{year}Y' for year in (2,3,5,10,20,30)))
RANK_TYPES = ('MARKET_TRADING_AMOUNT', 'MARKET_TRADING_VOLUME', 'TOP_GAINERS', 'TOP_LOSERS',
              'TOSS_SECURITIES_TRADING_AMOUNT', 'TOSS_SECURITIES_TRADING_VOLUME')
DURATIONS = ('realtime', '1d', '1w', '1mo', '3mo', '6mo', '1y')

def contract_path(path):
    for template in PUBLIC_GET_PATHS:
        symbol = r'(?:KOSPI|KOSDAQ)' if template.endswith('/investor-trading') and 'market-indicators' in template else '(?:' + '|'.join(INDICATORS) + ')' if 'market-indicators' in template else r'[0-9A-Z]{6}'
        pattern = re.escape(template).replace(r'\{symbol\}', symbol).replace(r'\{sectorId\}', r'[A-Za-z0-9_-]{1,40}')
        if re.fullmatch(pattern, path):
            return template
    raise ValueError('Endpoint outside public market-data scope')

def request_group(path):
    template = contract_path(path)
    if template in ('/api/v1/prices', '/api/v1/orderbook', '/api/v1/trades', '/api/v1/price-limits'):
        return 'quotes'
    if template.endswith('/candles'):
        return 'candles'
    if template.startswith('/api/v1/stocks/') and template != '/api/v1/stocks/all':
        return 'trading'
    return 'market'

def validate_ranking(kind, duration):
    if kind not in RANK_TYPES or duration not in DURATIONS:
        raise ValueError('Unsupported ranking selection')
    if kind in ('TOP_GAINERS', 'TOP_LOSERS') and duration == 'realtime':
        raise ValueError('Price-change rankings require a fixed period')

def source_scope(path):
    if '/sectors' in path:
        return '토스 산업 분류'
    if '/market-indicators/' in path or path.endswith('/program-trades'):
        return 'KRX'
    if path.endswith('/exchange-rate') or '/market-calendar/' in path:
        return 'reference'
    return '통합'
