"""Bounded PG17 canary in an exclusively created disposable schema.

Run with the existing MARKET_DATABASE_URL in the environment, never on CLI.
No production table, credentials or source payload is read or printed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.market_dashboard.store import Store  # noqa: E402


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    if not args.run:
        print(json.dumps({'status': 'dry_run', 'schema': 'new isolated quote_canary_<uuid>',
                          'connections': 4, 'writes': 44, 'timeouts_seconds': 5}))
        return
    url = make_url(os.environ['MARKET_DATABASE_URL'])
    if url.get_backend_name() != 'postgresql':
        raise ValueError('PostgreSQL is required')
    schema = 'quote_canary_' + uuid4().hex
    admin = create_engine(url, connect_args={'connect_timeout': 5,
        'options': '-c statement_timeout=5000 -c lock_timeout=5000'})
    scoped = url.update_query_dict({'options':
        f'-csearch_path={schema} -cstatement_timeout=5000 -clock_timeout=5000',
        'connect_timeout': '5'})
    stores, created = [], False
    started = time.monotonic()
    report = {'fixture_non_production': True, 'schema': schema, 'status': 'failed'}
    # Avoid deleting any shared Redis key: this test uses only the new schema.
    saved_redis = os.environ.pop('MARKET_REDIS_URL', None)
    try:
        with admin.begin() as db:
            version = db.execute(text('SHOW server_version_num')).scalar_one()
            if int(version) // 10000 != 17:
                raise ValueError('This canary requires the deployed PG17 major')
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
            created = True
        with tempfile.TemporaryDirectory(prefix='quote-canary-') as root:
            stores = [Store(scoped.render_as_string(hide_password=False), root) for _ in range(4)]
            barrier = threading.Barrier(4)
            def write(index):
                barrier.wait(timeout=5)
                for step in range(10):
                    stores[index].update_quote('005930', {'daily_facts': {
                        f'fixture{index}_{step}': {'value': step, 'period': '2026-10-08'}}})
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(write, index) for index in range(4)]
                for future in futures:
                    future.result(timeout=30)
            assert len(stores[0].get_quote('005930')['daily_facts']) == 40
            good = {'price': 100, 'as_of': '2026-10-08T15:30:00+09:00',
                    'quote_trace': {'source': 'toss'}}
            stores[0].update_quote('005930', good)
            stores[1].update_quote('005930', {**good, 'price': 0})
            stores[2].update_quote('005930', {**good, 'price': 50,
                'as_of': '2026-10-07T15:30:00+09:00'})
            before = stores[0].revisions(('quote:005930',))
            stores[3].update_quote('000660', good)
            assert stores[0].revisions(('quote:005930',)) == before
            assert stores[1].get('quotes')['005930']['price'] == 100
            assert len(stores[2].get_quote('005930')['daily_facts']) == 40
            report['status'] = 'passed'
    except Exception as exc:
        # SQL/connection exception messages can contain connection information.
        report['error_type'] = type(exc).__name__
    finally:
        for store in stores:
            store.engine.dispose()
        if created:
            # This unguessable schema was created by this invocation only.
            with admin.begin() as db:
                db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            report['schema_removed'] = True
        admin.dispose()
        if saved_redis is not None:
            os.environ['MARKET_REDIS_URL'] = saved_redis
    report['elapsed_seconds'] = round(time.monotonic() - started, 3)
    print(json.dumps(report))
    if report['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    run()
