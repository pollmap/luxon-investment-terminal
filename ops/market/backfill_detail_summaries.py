"""Rebuild small detail projections without loading all histories into memory.

PostgreSQL row locks serialize with detail writers; both paths lock the source
before writing the projection. Originals are never changed. Safe to resume.
"""
import json
import time

from sqlalchemy import select

from services.market_dashboard.detail_summary import project
from services.market_dashboard.store import Store


def run():
    store = Store()
    table = store.snapshots
    started = time.monotonic()
    with store.engine.connect() as db:
        keys = db.execute(select(table.c.key).where(table.c.key.like('detail:%'))
                          .order_by(table.c.key)).scalars().all()
    source_bytes = summary_bytes = changed = 0
    for index, key in enumerate(keys, 1):
        with store.engine.begin() as db:
            source = db.execute(select(table.c.payload).where(table.c.key == key)
                                .with_for_update()).scalar_one_or_none()
            if source is None:
                continue
            summary = project(json.loads(source))
            summary_key = 'detail-summary:' + key.removeprefix('detail:')
            changed += bool(store.put(summary_key, summary, _connection=db))
            source_bytes += len(source.encode('utf-8'))
            summary_bytes += len(json.dumps(summary, ensure_ascii=False).encode('utf-8'))
        if index % 100 == 0:
            print(json.dumps({'processed': index, 'total': len(keys)}), flush=True)
    print(json.dumps({'processed': len(keys), 'changed': changed,
                      'source_bytes': source_bytes, 'summary_bytes': summary_bytes,
                      'seconds': round(time.monotonic() - started, 2)}), flush=True)


if __name__ == '__main__':
    run()
