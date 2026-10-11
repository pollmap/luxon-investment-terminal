"""Reclassify stored 404s from current catalogues, without another provider call.

Stop the collector before --apply to preserve concurrent checkpoint updates.
No security, history, retry deadline or last good value is removed.
"""
import argparse
from collections import Counter
import json
from services.market_dashboard.store import Store, now
from services.market_dashboard.source_support import diagnose_not_found


def run(apply=False):
    store = Store()
    counts, codes = Counter(), set()
    for key, item in store.list_prefix('toss-backfill:').items():
        if item.get('http_status') != 404:
            continue
        _, code, kind = key.split(':', 2)
        diagnosis = diagnose_not_found(store, code, kind)
        counts[diagnosis['status']] += 1
        codes.add(code)
        if apply:
            store.put(key, {**item, 'diagnosis':diagnosis, 'diagnosed_at':now()})
    print(json.dumps({'securities':len(codes), 'dataset_failures':dict(counts),
                      'applied':apply}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    run(parser.parse_args().apply)
