import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo
import httpx
from services.market_dashboard.store import Store
from services.market_dashboard.disclosures import collect_filings


def test_disclosures_resume_and_keep_receipts_on_failure(tmp_path):
    store=Store(url='sqlite://',root=tmp_path)
    day=datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y%m%d')
    def response(page):
        return httpx.Response(200,request=httpx.Request('GET','https://fixture.invalid'),json={
            'status':'000','total_count':3,'total_page':3,'list':[{'rcept_no':str(page),'rcept_dt':day,'report_nm':'fixture'}]})
    auth=SimpleNamespace(store=store,dart='fixture',_dart_get=AsyncMock(return_value=response(1)))
    first=asyncio.run(collect_filings(auth,'005930','fixture',pages_per_run=1))
    assert first['status']=='partial' and first['pagination']['next_page']==2
    auth._dart_get=AsyncMock(side_effect=RuntimeError('fixture outage'))
    try: asyncio.run(collect_filings(auth,'005930','fixture',pages_per_run=1))
    except RuntimeError: pass
    assert store.get('filings:005930')['rows']==first['rows']
    auth._dart_get=AsyncMock(side_effect=[response(2),response(3)])
    final=asyncio.run(collect_filings(auth,'005930','fixture'))
    assert auth._dart_get.call_args_list[0].kwargs['params']['page_no']=='2'
    assert final['status']=='ready' and len(final['rows'])==3
    assert final['pagination']['complete']
    store.engine.dispose()
