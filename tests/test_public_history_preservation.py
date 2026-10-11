from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from services.market_dashboard.providers import PublicProvider
from services.market_dashboard.store import Store


@pytest.mark.asyncio
async def test_provider_window_does_not_erase_older_history_or_claim_completeness(tmp_path):
    s=Store('sqlite://',tmp_path);p=PublicProvider(s)
    try:
        s.put('prices:136660',{'rows':[{'date':'2010-01-04','close':100}],
              'source_trace':{'source':'naver','sha256':'old'}})
        p.fetch=AsyncMock(return_value=(SimpleNamespace(text='<item data="20261002|100|110|90|105|500"/>'),'new'))
        result=await p.prices('136660')
        assert [r['date'] for r in result['rows']]==['2010-01-04','2026-10-02']
        assert result['rows'][0]['source_trace']['sha256']=='old'
        assert result['rows'][1]['source_trace']['source']=='naver'
        assert result['requested_count']==20000 and not result['history_complete']
        assert result['response_rows']==1 and result['row_count']==2
        assert 'count=20000' in p.fetch.call_args.args[0]
        again=await p.prices('136660')
        assert again['row_count']==2
        p.fetch=AsyncMock(return_value=(SimpleNamespace(text='<chart/>'),'empty'))
        with pytest.raises(ValueError,match='No historical'):
            await p.prices('136660')
        assert s.get('prices:136660')['rows']==again['rows']
    finally:
        await p.client.aclose()


@pytest.mark.asyncio
async def test_different_source_history_is_not_silently_merged(tmp_path):
    s=Store('sqlite://',tmp_path);p=PublicProvider(s)
    try:
        s.put('prices:136660',{'rows':[{'date':'2010-01-04','close':100}],
                             'source_trace':{'source':'another_provider'}})
        p.fetch=AsyncMock(return_value=(SimpleNamespace(text='<item data="20261002|100|110|90|105|500"/>'),'new'))
        with pytest.raises(ValueError,match='provider mismatch'):
            await p.prices('136660')
        assert s.get('prices:136660')['rows'][0]['close']==100
    finally:
        await p.client.aclose()
