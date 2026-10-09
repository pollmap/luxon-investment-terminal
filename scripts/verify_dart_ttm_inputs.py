"""Collect actual period evidence for a small representative slice; never invent dates."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from services.market_dashboard.runtime import load_worker_credentials
from services.market_dashboard.store import Store
from services.market_dashboard.providers import PublicProvider
from services.market_dashboard.authenticated_sources import AuthenticatedSources
from services.market_dashboard.dart_periods import DartPeriodCollector
from services.market_dashboard.analysis import trailing_twelve_months
from services.market_dashboard.xbrl_facts import collect_xbrl


async def main():
    load_worker_credentials()
    store=Store();provider=PublicProvider(store);source=AuthenticatedSources(provider)
    try:
        collector=DartPeriodCollector(source)
        for _ in range(12):
            state=await collector.collect(['005930','000660'],checkpoint_key='dart-period-progress:representative-desktop')
            print(json.dumps(state),flush=True)
            if state['status'] in ('ready','current','error','retry_wait','credentials_missing'):break
        for code in ('005930','000660'):
            filings=store.get('filings:'+code,{}).get('rows',[])
            quarterly=[r for r in filings if any(word in r.get('title','') for word in ('사업보고서','반기보고서','분기보고서'))]
            for item in quarterly[:4]:
                title=item['title']
                report='11011' if '사업보고서' in title else '11012' if '반기보고서' in title else '11013' if '.03' in title else '11014' if '.09' in title else None
                if not report:continue
                result=await collect_xbrl(source,code,item['receipt'],report,publication=item.get('date'))
                print(code,'XBRL',json.dumps(result),flush=True)
            official=store.get('official:'+code,{})
            facts=official.get('period_facts',[])+official.get('xbrl_facts',[])
            ttm=trailing_twelve_months(facts)
            print(code,json.dumps({'facts':len(facts),'ttm_rows':len(ttm.get('rows',[])),'ttm_status':ttm.get('status')}),flush=True)
    finally:await provider.client.aclose()


asyncio.run(main())
