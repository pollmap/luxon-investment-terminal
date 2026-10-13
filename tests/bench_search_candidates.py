"""Bounded offline microbench; no provider, live database or WAN load test."""
import importlib
import json
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

platform.processor = lambda: 'fixture_non_production'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.market_dashboard import search_view, request_timings as timings
from services.market_dashboard.store import Store

market = importlib.import_module('services.market_dashboard.app')


def run(module):
    with tempfile.TemporaryDirectory(prefix='stocks-search-microbench-') as directory:
        store = Store('sqlite://', Path(directory))
        market.get_store = lambda: store
        market._security_universe_cache = None
        rows = [{'code': str(100000+i), 'name': f'삼성 fixture {i}' if i < 80 else f'다른기업 {i}',
                 'aliases': [f'별칭{i}'] if i < 80 else [], 'market': 'KOSPI',
                 'price': 100+i, 'value': 1000-i, 'as_of': '2026-10-08',
                 'source_trace': {'source': 'fixture_non_production'}} for i in range(280)]
        store.put('universe', {'rows': rows})
        store.put('enriched', {})
        store.update_quotes({row['code']: {'price':row['price'],'as_of':'2026-10-08T09:00:00+09:00',
            'quote_trace':{'source':'toss','fixture_non_production':True}} for row in rows})
        saved = {name:getattr(search_view,name) for name in ('identity_candidates','candidate_dependencies',
                  'candidates','selected_inputs')}
        for name in saved:
            setattr(search_view, name, getattr(module, name))
        captures, original = [], module._decode
        def decoded(records, sqlite, paths=module.PATHS):
            captures.append(len(records))
            return original(records, sqlite, paths)
        module._decode = decoded
        results = {}
        try:
            for position,query in enumerate(('삼성', '별칭79', '')):
                market.search_payload(query, 12)
                observations = []
                for turn in range(4):
                    # More than 30 changed quotes, with a stable match set.
                    previous=store.revisions(('quotes',))
                    store.update_quotes({str(100000+i): {'price':300+turn+i,
                        'as_of':f'2026-10-08T09:{position*4+turn+1:02d}:00+09:00',
                        'quote_trace':{'source':'toss','fixture_non_production':True}} for i in range(40)})
                    assert store.revisions(('quotes',)) != previous, 'Benchmark quote change was rejected'
                    captures.clear()
                    with timings.collect() as record:
                        started = time.perf_counter()
                        response = market.search_payload(query, 12)
                        elapsed = (time.perf_counter()-started)*1000
                    observations.append({'elapsed_ms':round(elapsed,3),'decoded_records':sum(captures),
                        'timing_ms':record.durations, 'counts':record.counts,
                        'rows':len(response['rows']), 'total':response['total']})
                results[query or '<empty>'] = {'median_ms':round(statistics.median(
                    item['elapsed_ms'] for item in observations),3),'observations':observations}
        finally:
            module._decode = original
            for name, function in saved.items():
                setattr(search_view,name,function)
            store.engine.dispose()
        return results


if __name__ == '__main__':
    reference = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    before_source = subprocess.check_output(['git','show',reference+':services/market_dashboard/search_view.py'],
                                           cwd=ROOT,text=True,encoding='utf-8')
    baseline = types.ModuleType('services.market_dashboard._search_microbench_baseline')
    baseline.__package__ = 'services.market_dashboard'
    exec(compile(before_source,'<committed-search-baseline>','exec'),baseline.__dict__)
    result = {'fixture_non_production':True,'baseline_commit':reference,'universe_rows':280,
              'matched_broad_rows':80,'changed_quotes_per_turn':40,'turns_per_query':4,
              'mode':'sequential_SQLite_projection_microbench','external_requests':0,
              'before':run(baseline),'after':run(search_view)}
    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:{q:item['median_ms'] for q,item in result[key].items()}
                      for key in ('before','after')},ensure_ascii=False))
