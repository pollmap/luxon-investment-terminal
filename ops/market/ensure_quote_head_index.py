"""Scoped online index for quote revision reads; never change financial rows.

Run via stdin in the existing API container. Default is a read-only query plan.
The partial index excludes unrelated snapshots and large payloads.
"""
import argparse
import json
import os
import shutil

INDEX = 'market_snapshot_quote_heads_idx'
QUERY = ("SELECT key, updated_at FROM market_snapshots "
         "WHERE key IN ('quotes', 'quote-book') OR key LIKE 'quote:%'")
DDL = ("CREATE INDEX CONCURRENTLY IF NOT EXISTS " + INDEX +
       " ON market_snapshots (key, updated_at) "
       "WHERE key IN ('quotes', 'quote-book') OR key LIKE 'quote:%'")


def inspect(db):
    row = db.execute("SELECT i.indisvalid, pg_relation_size(i.indexrelid), "
                     "pg_get_indexdef(i.indexrelid) FROM pg_index i "
                     "WHERE i.indexrelid=to_regclass(%s)", (INDEX,)).fetchone()
    return {'present': row is not None, 'valid': bool(row and row[0]),
            'bytes': row[1] if row else 0, 'definition': row[2] if row else None,
            'plan': db.execute('EXPLAIN (FORMAT JSON) ' + QUERY).fetchone()[0]}


def run(db, *, apply=False, free_bytes=None):
    before = inspect(db)
    if not apply:
        return {'mode': 'readonly', 'before': before, 'changed': False}
    if free_bytes is None or free_bytes < 256 * 1024**2:
        raise ValueError('Insufficient space for the bounded lookup index')
    if before['present'] and not before['valid']:
        raise ValueError('Existing invalid lookup index requires explicit repair')
    if before['present'] and ('(key, updated_at)' not in before['definition']
                              or 'quote:%' not in before['definition']):
        raise ValueError('Existing lookup index definition differs')
    db.execute(DDL)
    after = inspect(db)
    if not after['valid'] or after['bytes'] > 64 * 1024**2:
        raise ValueError('Lookup index validation or size budget failed')
    return {'mode': 'applied', 'before': before, 'after': after,
            'changed': not before['present'], 'data_rows_modified': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected-release', required=True)
    args = parser.parse_args()
    if os.environ.get('MARKET_RELEASE') != args.expected_release:
        raise ValueError('Release changed')
    url = os.environ.get('MARKET_DATABASE_URL', '')
    if not url.startswith(('postgresql://', 'postgresql+psycopg://')):
        raise ValueError('PostgreSQL required')
    import psycopg
    with psycopg.connect(url.replace('postgresql+psycopg://', 'postgresql://', 1),
                         autocommit=True, connect_timeout=5,
                         options='-c statement_timeout=60000 -c lock_timeout=3000') as db:
        if not args.apply:
            db.execute('SET default_transaction_read_only=on')
        print(json.dumps(run(db, apply=args.apply, free_bytes=shutil.disk_usage(
            os.environ.get('MARKET_STORAGE_DIR', '/app/storage/public-market')).free)))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'error': type(exc).__name__}))
        raise SystemExit(1) from None
