"""Fixture-only optional candle cursor and terminal provenance checks."""
import hashlib
import json

import pytest

from services.market_dashboard.candle_pages import validate_page
from services.market_dashboard.store import Store
from services.market_dashboard.toss import TossCollector, normalize_candles


def candle(day):
    return {'timestamp': day + 'T15:30:00+09:00', 'openPrice': '100',
            'highPrice': '110', 'lowPrice': '90', 'closePrice': '105', 'volume': '10'}


class Client:
    def __init__(self, pages, proof=True):
        self.pages, self.proof, self.calls = iter(pages), proof, []

    async def get(self, path, params):
        self.calls.append(dict(params))
        data = next(self.pages)
        digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        return data, ({'source': 'toss', 'fixture_non_production': True, 'sha256': digest,
                       'source_document_id': digest} if self.proof else {})


def test_direct_strict_candle_validator_still_requires_cursor():
    with pytest.raises(ValueError, match='explicit continuation'):
        validate_page({'candles': [candle('2026-10-08')]}, '005930', None, set(), normalize_candles)


@pytest.mark.asyncio
async def test_optional_price_cursor_preserves_valid_ohlc_and_last_cursor(tmp_path):
    store = Store('sqlite://', tmp_path)
    client = Client([{'candles': [candle('2026-10-08')], 'nextBefore': '2026-10-07T15:30:00+09:00'},
                     {'candles': [candle('2026-10-07')]}])
    collector = TossCollector(store, client)
    await collector.history('005930')
    result = await collector.history('005930', resume=True, pages=4)
    assert result['status'] == result['history_terminal_status'] == 'cursor_unknown'
    assert result['next_before'] == '2026-10-07T15:30:00+09:00'
    assert not result['backfill_complete'] and len(result['rows']) == 2 and len(client.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('adjusted,kind,unit', [(True, 'history', 'source'), (False, 'raw-history', 'raw')])
async def test_price_terminal_receipt_matches_symbol_kind_and_adjustment(tmp_path, adjusted, kind, unit):
    collector = TossCollector(Store('sqlite://', tmp_path), Client([{'candles': [candle('2026-10-08')], 'nextBefore': None}]))
    result = await collector.history('005930', adjusted=adjusted)
    receipt = result['history_terminal']
    assert result['backfill_complete'] and result['history_terminal_status'] == 'verified'
    assert receipt['code'] == '005930' and receipt['kind'] == kind
    assert receipt['venue'] == '통합' and receipt['adjustment'] == unit
    assert receipt['source'] == 'toss' and receipt['sha256'] == receipt['source_document_id']


@pytest.mark.asyncio
async def test_missing_cursor_does_not_discard_legacy_complete_but_exposes_unknown_proof(tmp_path):
    store = Store('sqlite://', tmp_path)
    store.put('bars:005930:통합:day', {'rows': [{'time': '2026-10-07T15:30:00+09:00',
               'date': '2026-10-07', 'open': 100, 'high': 110, 'low': 90, 'close': 105, 'volume': 10}],
               'history_complete': True, 'backfill_complete': True, 'next_before': None})
    collector = TossCollector(store, Client([{'candles': [candle('2026-10-08')]}]))
    result = await collector.history('005930')
    assert result['history_complete'] and result['history_terminal_status'] == 'cursor_unknown'
    assert result['history_terminal'] is None and len(result['rows']) == 2


@pytest.mark.asyncio
async def test_explicit_null_without_original_preserves_price_history_cursor(tmp_path):
    store = Store('sqlite://', tmp_path)
    client = Client([{'candles': [candle('2026-10-08')], 'nextBefore': '2026-10-07T15:30:00+09:00'}])
    collector = TossCollector(store, client)
    await collector.history('005930')
    collector.client = Client([{'candles': [candle('2026-10-07')], 'nextBefore': None}], proof=False)
    result = await collector.history('005930', resume=True)
    assert result['status'] == 'terminal_unverified' and not result['backfill_complete']
    assert result['next_before'] == '2026-10-07T15:30:00+09:00' and len(result['rows']) == 2
