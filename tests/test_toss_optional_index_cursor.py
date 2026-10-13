"""Fixture-only index continuation and source receipt contract."""
from copy import deepcopy
import hashlib
import json

import pytest

from services.market_dashboard.store import Store
from services.market_dashboard.toss import TossCollector
from services.market_dashboard.toss_market import TossMarket


def candle(day):
    return {'timestamp': day + 'T00:00:00+09:00', 'openPrice': '-0.2',
            'highPrice': '0', 'lowPrice': '-0.3', 'closePrice': '-0.1', 'volume': '0'}


class Client:
    def __init__(self, pages, proof=True):
        self.pages, self.proof, self.calls = iter(pages), proof, []

    async def get(self, path, params):
        self.calls.append(dict(params))
        data = next(self.pages)
        digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        return data, ({'source': 'toss', 'fixture_non_production': True, 'sha256': digest,
                       'source_document_id': digest} if self.proof else {})


@pytest.mark.asyncio
async def test_missing_cursor_preserves_index_rows_and_prior_continuation(tmp_path):
    store = Store('sqlite://', tmp_path)
    client = Client([{'candles': [candle('2026-10-08')], 'nextBefore': '2026-10-07T00:00:00+09:00'},
                     {'candles': [candle('2026-10-07')]}])
    market = TossMarket(TossCollector(store, client))
    await market.index_candles('KR_BOND_10Y')
    await market.index_candles('KR_BOND_10Y', resume=True)
    saved = store.get('toss-index:KR_BOND_10Y:1d')
    assert saved['status'] == 'cursor_unknown' and not saved['backfill_complete']
    assert saved['next_before'] == '2026-10-07T00:00:00+09:00'
    assert len(saved['rows']) == 2 and saved['rows'][0]['close'] == -0.1
    assert saved['unit'] == '%'


@pytest.mark.asyncio
@pytest.mark.parametrize('proof', [True, False])
async def test_index_terminal_needs_explicit_null_and_source_reference(tmp_path, proof):
    store = Store('sqlite://', tmp_path)
    market = TossMarket(TossCollector(store, Client([{'candles': [], 'nextBefore': None}], proof)))
    await market.index_candles('KOSPI')
    saved = store.get('toss-index:KOSPI:1d')
    assert saved['backfill_complete'] is proof
    assert saved['status'] == ('ready' if proof else 'terminal_unverified')
    if proof:
        assert saved['history_terminal']['explicit_next_before_null'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('cursor', ['', False, '2026-10-07', '2026-10-08T00:00:00+09:00'])
async def test_invalid_index_cursor_does_not_replace_last_normal_series(tmp_path, cursor):
    store = Store('sqlite://', tmp_path)
    client = Client([{'candles': [candle('2026-10-08')], 'nextBefore': '2026-10-07T00:00:00+09:00'},
                     {'candles': [candle('2026-10-07')], 'nextBefore': cursor}])
    market = TossMarket(TossCollector(store, client))
    await market.index_candles('KR_BOND_10Y')
    before = deepcopy(store.get('toss-index:KR_BOND_10Y:1d'))
    with pytest.raises(ValueError):
        await market.index_candles('KR_BOND_10Y', resume=True)
    assert store.get('toss-index:KR_BOND_10Y:1d') == before
