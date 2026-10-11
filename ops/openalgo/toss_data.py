"""Read-only Toss adapter for upstream OpenAlgo. Token ownership stays in the collector."""
import os
import httpx
import pandas as pd


class BrokerData:
    timeframe_map = {'1m':'1m','5m':'5m','D':'D','W':'W','M':'M'}

    def __init__(self, auth_token):
        self.auth_token = auth_token  # Internal bridge key, never a Toss token.

    def read(self, symbol, exchange, kind, **params):
        if exchange != 'TOSS_KR':
            raise ValueError('Unsupported market scope')
        response = httpx.get(os.environ['MARKET_CANONICAL_URL'].rstrip('/')+f'/internal/market-data/{symbol}/{kind}',
            headers={'X-Market-Key':self.auth_token}, params=params, timeout=5)
        response.raise_for_status()
        return response.json()

    def get_quotes(self, symbol, exchange):
        return self.read(symbol,exchange,'quotes')

    def get_depth(self, symbol, exchange):
        return self.read(symbol,exchange,'depth')

    def get_history(self, symbol, exchange, interval, start_date, end_date):
        if interval not in self.timeframe_map:
            raise ValueError('Unsupported interval')
        payload = self.read(symbol,exchange,'history',interval=interval,start=start_date,end=end_date)
        # Open interest does not apply to shares. Supply null to prevent upstream's default zero.
        return pd.DataFrame([{**row,'oi':None} for row in payload['rows']], columns=None if payload['rows'] else ['timestamp','open','high','low','close','volume','oi'])
