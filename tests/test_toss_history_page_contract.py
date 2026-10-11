"""fixture_non_production: a malformed page is not an exhausted history cursor."""

from copy import deepcopy

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
        return next(self.pages), {
            'source': 'fixture_non_production', 'page': len(self.calls),
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
        page('2026-10-07', '2026-10-06'), {'records': []},
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
