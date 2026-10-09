"""Run a read-only live contract probe and collect normalized representative securities."""
import argparse
import asyncio
import json
from services.market_dashboard.store import Store
from services.market_dashboard.toss import TossCollector


async def run(codes, stream_seconds):
    store = Store()
    collector = TossCollector(store)
    try:
        for code in codes:
            store.request(code)
            await collector.history(code, '1d', pages=15)
            await collector.history(code, '1m', pages=5)
            await collector.snapshot(code)
            await collector.fundamentals_of_trading(code)
            print(json.dumps({'code':code,'daily_rows':len(store.get(f'bars:{code}:통합:day')['rows']),
                'depth_rows':len(store.get('depth:'+code)['book']), 'toss_price_received':bool(store.get('quotes').get(code)),
                'financing_status':{k:v.get('status') for k,v in store.get('financing:'+code,{}).items() if isinstance(v,dict)}}),flush=True)
        if stream_seconds:
            try:
                await asyncio.wait_for(collector.stream(),stream_seconds)
            except TimeoutError:
                pass
            print(json.dumps({'stream':store.get('provider-toss-stream',{})}),flush=True)
    finally:
        await collector.client.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--codes',nargs='+',default=['005930','000660'])
    parser.add_argument('--stream-seconds',type=int,default=15)
    args=parser.parse_args()
    asyncio.run(run(args.codes,args.stream_seconds))
