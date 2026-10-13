"""fixture_non_production: a malformed page is not an exhausted history cursor."""

from copy import deepcopy
import hashlib
import json

import pytest

from services.market_dashboard.store import Store
from services.market_dashboard.toss import TossCollector

KINDS = (
    'investor-trading', 'program-trades', 'short-selling',
    'credit-trades', 'securities-lending',
)


class PageClient:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.calls = []

    async def get(self, path, params):
        self.calls.append((path, dict(params)))
        data = next(self.pages)
        digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        return data, {
            'source': 'toss', 'fixture_non_production': True, 'page': len(self.calls),
            'sha256': digest, 'source_document_id': digest,
        }


def page(date, cursor):
    return {'records': [{'date': date}], 'nextUntil': cursor}


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('malformed', [{}, {'records': None}, {'records': {}}, {'records': ''}])
async def test_malformed_page_preserves_existing_history_and_cursor(tmp_path, kind, malformed):
    store = Store('sqlite://', tmp_path)
    client = PageClient([page('2026-10-08', '2026-10-07'), malformed])
    collector = TossCollector(store, client)
    await collector.fundamentals_of_trading('005930', (kind,))
    key = f'toss:{kind}:005930'
    before = deepcopy(store.get(key))
    revision = store.revisions((key,))

    with pytest.raises(ValueError, match='Trading records must be an array'):
        await collector.fundamentals_of_trading('005930', (kind,), resume=True)

    assert client.calls[-1][1]['until'] == '2026-10-07'
    assert store.get(key) == before
    assert store.revisions((key,)) == revision
    assert not store.metadata(key)['backfill_complete']


@pytest.mark.asyncio
async def test_malformed_later_page_keeps_valid_prefix_for_next_resume(tmp_path):
    store = Store('sqlite://', tmp_path)
    client = PageClient([
        page('2026-10-08', '2026-10-07'), {},
        page('2026-10-07', '2026-10-06'), {'records': [], 'nextUntil': None},
    ])
    collector = TossCollector(store, client)
    key = 'toss:credit-trades:005930'
    with pytest.raises(ValueError, match='Trading records must be an array'):
        await collector.fundamentals_of_trading('005930', ('credit-trades',), pages=4)
    saved = store.get(key)
    assert saved['next_until'] == '2026-10-07'
    assert not saved['backfill_complete']
    assert len(saved['rows']) == 1
    assert saved['rows'][0]['source_trace']['page'] == 1

    await collector.fundamentals_of_trading('005930', ('credit-trades',), resume=True, pages=4)
    assert client.calls[2][1]['until'] == '2026-10-07'
    final = store.get(key)
    assert final['backfill_complete'] and final['next_until'] is None
    assert [row['date'] for row in final['rows']] == ['2026-10-08', '2026-10-07']


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', KINDS)
async def test_explicit_empty_array_remains_a_valid_empty_history(tmp_path, kind):
    store = Store('sqlite://', tmp_path)
    collector = TossCollector(store, PageClient([{'records': [], 'nextUntil': None}]))
    await collector.fundamentals_of_trading('005930', (kind,))
    saved = store.get(f'toss:{kind}:005930')
    assert saved['status'] == 'no_data'
    assert saved['backfill_complete'] and saved['next_until'] is None
    assert not saved['rows']


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', KINDS)
async def test_missing_optional_cursor_keeps_valid_rows_and_previous_cursor(tmp_path, kind):
    store = Store('sqlite://', tmp_path)
    client = PageClient([page('2026-10-08', '2026-10-07'), {'records': [{'date': '2026-10-07'}]}])
    collector = TossCollector(store, client)
    await collector.fundamentals_of_trading('005930', (kind,))
    await collector.fundamentals_of_trading('005930', (kind,), resume=True, pages=4)
    saved = store.get(f'toss:{kind}:005930')
    assert saved['status'] == saved['history_status'] == 'cursor_unknown'
    assert saved['next_until'] == '2026-10-07' and not saved['backfill_complete']
    assert [row['date'] for row in saved['rows']] == ['2026-10-08', '2026-10-07']
    assert len(client.calls) == 2  # An unknown page cannot repeat in this burst.


@pytest.mark.asyncio
async def test_explicit_null_without_original_reference_does_not_close_cursor(tmp_path):
    store = Store('sqlite://', tmp_path)
    client = PageClient([page('2026-10-08', '2026-10-07')])
    collector = TossCollector(store, client)
    await collector.fundamentals_of_trading('005930', ('credit-trades',))
    async def unproven(path, params):
        return {'records': [{'date': '2026-10-07'}], 'nextUntil': None}, {'source': 'fixture_non_production'}
    client.get = unproven
    await collector.fundamentals_of_trading('005930', ('credit-trades',), resume=True)
    saved = store.get('toss:credit-trades:005930')
    assert saved['status'] == 'terminal_unverified'
    assert saved['next_until'] == '2026-10-07' and not saved['backfill_complete']
    assert len(saved['rows']) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('cursor', ['', False, '2026-10-09', '2026-10-07'])
async def test_invalid_or_nonadvancing_trading_cursor_preserves_last_good_page(tmp_path, cursor):
    store = Store('sqlite://', tmp_path)
    collector = TossCollector(store, PageClient([page('2026-10-08', '2026-10-07'), page('2026-10-07', cursor)]))
    await collector.fundamentals_of_trading('005930', ('credit-trades',))
    before = deepcopy(store.get('toss:credit-trades:005930'))
    with pytest.raises(ValueError):
        await collector.fundamentals_of_trading('005930', ('credit-trades',), resume=True)
    assert store.get('toss:credit-trades:005930') == before


@pytest.mark.asyncio
@pytest.mark.parametrize('receipt', [
    {'explicit_next_until_null': True},
    {'explicit_next_until_null': True, 'source': 'fixture_non_production', 'sha256': 'a' * 64},
    {'explicit_next_until_null': True, 'source': 'toss', 'sha256': 'a' * 64,
     'source_document_id': 'b' * 64, 'code': '005930', 'kind': 'credit-trades', 'venue': '국내'},
    {'explicit_next_until_null': True, 'source': 'toss', 'sha256': 'a' * 64,
     'code': '000660', 'kind': 'credit-trades', 'venue': '국내'},
])
async def test_unknown_page_preserves_legacy_claim_without_verifying_wrong_receipt(tmp_path, receipt):
    store = Store('sqlite://', tmp_path)
    key = 'toss:credit-trades:005930'
    store.put(key, {'rows': [{'date': '2026-10-08'}], 'backfill_complete': True,
                    'next_until': None, 'history_terminal': receipt})
    collector = TossCollector(store, PageClient([{'records': [{'date': '2026-10-07'}]}]))
    await collector.fundamentals_of_trading('005930', ('credit-trades',))
    saved = store.get(key)
    assert saved['status'] == 'cursor_unknown' and saved['backfill_complete']
    assert saved['history_terminal_status'] == 'legacy_unverified'
    assert len(saved['rows']) == 2
