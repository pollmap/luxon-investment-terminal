"""Root-operated metadata audit. Default invocation does not connect anywhere.

--run uses one SSH process and one already-running market-api container. It never
constructs Store, hydrates series, calculates facts, enqueues work, or calls a
provider. This report is not a whole-dataset or comparison-verification receipt.
The one universe identity projection allows 10 seconds; other statements allow
3 seconds, with the same 120-second total snapshot deadline and no retry.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

SSH = ["ssh", "-i", "C:/Users/lch68/.ssh/openclaw_vmi3138243_ed25519",
       "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "root@62.171.141.206"]

INNER = r'''
import json
import os
import re
import signal
import time
from collections import Counter
from datetime import datetime, timezone

KINDS = ('history', 'investor-trading', 'program-trades', 'short-selling',
         'credit-trades', 'securities-lending', 'raw-history')
SOURCES = ('quote', 'fundamentals', 'flows', 'history', 'filings', 'brokers',
           'official', 'short_trades', 'short_balance', 'lending', 'credit', 'stock_loan')
STATES = {'ready', 'partial', 'stale', 'no_data', 'unavailable', 'credentials_missing',
          'error', 'rate_limited', 'pending', 'queued', 'collecting',
          'not_in_provider_catalogue', 'out_of_scope', 'out_of_universe'}
CONTROL_KEYS = ('toss-backfill-progress', 'storage-capacity:price-history',
                'storage-capacity:financial-reports', 'storage-capacity:xbrl',
                'storage-capacity:dated-accounts', 'storage-capacity:share-basis',
                'history-progress', 'dart-full-report-progress', 'xbrl-discovery',
                'share-basis-bootstrap-cursor', 'dart-provider-retry')
CONTROL_LABELS = ('toss_history_queue', 'price_history_capacity', 'financial_capacity',
                  'xbrl_capacity', 'dated_accounts_capacity', 'share_basis_capacity',
                  'price_history_queue', 'financial_queue', 'xbrl_discovery',
                  'share_basis_initial_review', 'dart_retry')
MAX_SECONDS = 120
started = time.monotonic()
queries = 0
stage = 'imports'


class Refused(Exception):
    pass


def check():
    if time.monotonic() - started > MAX_SECONDS:
        raise Refused('deadline')


def expired(*_):
    raise Refused('deadline')


def number(value):
    return value if type(value) in (int, float) and value >= 0 else None


def stamp(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).isoformat()
    except ValueError:
        return None


def period(value):
    parsed = stamp(value)
    return parsed[:10] if parsed else None


def status(value):
    return value if value in STATES else 'unknown'


def execute(db, sql, params=()):
    global queries
    check()
    queries += 1
    result = db.execute(sql, params)
    check()
    return result


def batches(values):
    for offset in range(0, len(values), 256):
        yield values[offset:offset + 256]


def snapshots(db, keys, maximum=262144):
    """Exact primary keys; never call a snapshot reader that hydrates references."""
    for batch in batches(keys):
        cursor = execute(db,
            'SELECT key, updated_at, CASE WHEN octet_length(payload)<=%s '
            'THEN payload ELSE NULL END FROM market_snapshots WHERE key=ANY(%s)',
            (maximum, batch))
        # Fetch one bounded manifest at a time rather than materializing a batch.
        for key, revision, payload in cursor:
            check()
            yield key, revision, json.loads(payload) if payload is not None else None


def bounds(record, first, last):
    first, last = period(first), period(last)
    if first:
        record['first_observed_period'] = min(record.get('first_observed_period') or first, first)
    if last:
        record['last_observed_period'] = max(record.get('last_observed_period') or last, last)


def controls(db):
    global stage
    stage = 'controls'
    result = {}
    labels = dict(zip(CONTROL_KEYS, CONTROL_LABELS))
    for key, revision, value in snapshots(db, list(CONTROL_KEYS), 65536):
        if not isinstance(value, dict):
            result[labels[key]] = {'not_verified': 1}
            continue
        capacity = value.get('capacity') or value
        item = {'observed_at': stamp(revision)}
        for field in ('cursor', 'phase', 'attempts', 'visits', 'used_percent', 'free_bytes'):
            n = number(value.get(field, capacity.get(field)))
            if n is not None:
                item[field] = n
        # Counts rather than free-form provider/error text or request metadata.
        item['capacity_limited'] = int(value.get('status') == 'capacity_limited')
        item['provider_maintenance_800'] = int(value.get('status') == '800')
        item['write_verified'] = int(capacity.get('write_verified') is True)
        item['write_verification_observed'] = int(type(capacity.get('write_verified')) is bool)
        item['retry_at'] = stamp(value.get('next_at') or value.get('retry_after'))
        result[labels[key]] = item
    return result


def collect(db, codes):
    global stage
    stage = 'collection'
    result = {name: {'total': len(codes), 'recorded_states': Counter(),
                    'checked_value_receipt': 0, 'legacy_presence_claim': 0,
                    'not_verified': 0} for name in SOURCES}
    found = set()
    for key, _, payload in snapshots(db, ['collection:' + c for c in codes]):
        code = key.split(':', 1)[1]
        found.add(code)
        sources = payload.get('sources', {}) if isinstance(payload, dict) else {}
        for name, item in result.items():
            row = sources.get(name) or {}
            current = status(row.get('status', 'pending'))
            item['recorded_states'][current] += 1
            if (row.get('period_scope') == 'checked_normalized_rows'
                    and number(row.get('validated_rows'))
                    and period(row.get('as_of'))):
                item['checked_value_receipt'] += 1
                interval = row.get('period') or {}
                bounds(item, interval.get('start'), interval.get('end'))
            elif current in ('ready', 'partial', 'stale'):
                item['legacy_presence_claim'] += 1
                item['not_verified'] += 1
            else:
                item['not_verified'] += 1
    for item in result.values():
        missing = len(codes) - len(found)
        item['recorded_states']['missing'] += missing
        item['not_verified'] += missing
    return result


def jobs(db, codes):
    global stage
    result = {}
    for kind in KINDS:
        stage = 'jobs.' + kind
        item = {'total': len(codes), 'recorded_states': Counter(), 'rows_reported': 0,
                'securities_with_reported_rows': 0,
                'end_of_history_receipt': 0, 'end_of_history_and_current_ready': 0,
                'cursor_remaining': 0, 'completion_cursor_conflict': 0,
                'provider_404_unresolved': 0, 'authentication_error': 0,
                'not_verified': 0}
        found = 0
        cursor_name = 'next_before' if kind in ('history', 'raw-history') else 'next_until'
        for _, _, row in snapshots(db, [f'toss-backfill:{c}:{kind}' for c in codes], 65536):
            found += 1
            if not isinstance(row, dict):
                item['recorded_states']['oversized'] += 1
                item['not_verified'] += 1
                continue
            current = status(row.get('status'))
            item['recorded_states'][current] += 1
            cursor = bool(row.get(cursor_name))
            complete = row.get('backfill_complete') is True and not cursor
            item['rows_reported'] += number(row.get('rows')) or 0
            item['securities_with_reported_rows'] += int(bool(number(row.get('rows'))))
            item['cursor_remaining'] += int(cursor)
            item['end_of_history_receipt'] += int(complete)
            item['completion_cursor_conflict'] += int(
                cursor and row.get('backfill_complete') is True)
            item['end_of_history_and_current_ready'] += int(
                complete and current == 'ready' and row.get('source_status') == 'ready')
            item['provider_404_unresolved'] += int(
                current == 'error' and row.get('http_status') == 404)
            item['authentication_error'] += int(
                current == 'error' and row.get('http_status') in (401, 403))
            item['not_verified'] += int(not complete)
            bounds(item, row.get('first_date'), row.get('last_date'))
        item['recorded_states']['missing'] += len(codes) - found
        item['not_verified'] += len(codes) - found
        result[kind] = item
    return result


# Reduce existing cached projections in SQL; do not return facts or read official
# source payloads. Oversized or stale caches are explicitly not verified.
PROJECTION_SQL = """
WITH selected AS MATERIALIZED (
 SELECT key, updated_at, CASE WHEN octet_length(payload)<=2097152
 THEN payload::jsonb ELSE NULL END AS j
 FROM market_snapshots WHERE key=ANY(%s)
)
SELECT key, updated_at, j IS NULL,
 j->>'source_revision', j->>'share_basis_revision', j->>'projection_version',
 COALESCE((SELECT jsonb_agg(jsonb_build_object(
   'metric', metric, 'frequency', frequency, 'rows', n,
   'first', first_period, 'last', last_period)) FROM (
   SELECT r->>'metric' AS metric, r->>'frequency' AS frequency,
          count(*) AS n, min(r->>'period_start') AS first_period,
          max(r->>'period_end') AS last_period
   FROM jsonb_array_elements(
      CASE WHEN jsonb_typeof(j#>'{payload,eps,rows}')='array'
      THEN j#>'{payload,eps,rows}' ELSE '[]'::jsonb END ||
      CASE WHEN jsonb_typeof(j#>'{payload,fundamental_metrics,rows}')='array'
      THEN j#>'{payload,fundamental_metrics,rows}' ELSE '[]'::jsonb END
   ) AS r
   WHERE r->>'metric' IN ('EPS','OCF','EBITDA')
     AND r->>'frequency' IN ('annual','ttm')
     AND jsonb_typeof(r->'value')='number'
   GROUP BY r->>'metric', r->>'frequency'
 ) counts), '[]'::jsonb)
FROM selected
"""


def projections(db, codes):
    global stage
    stage = 'projection_revisions'
    revisions = {}
    keys = [prefix + c for c in codes for prefix in ('official:', 'share-basis:')]
    for batch in batches(keys):
        revisions.update(execute(db,
                                 'SELECT key,updated_at FROM market_snapshots WHERE key=ANY(%s)',
                                 (batch,)).fetchall())
    states = Counter()
    metrics = {m: {f: {'securities_with_cached_numeric_rows': 0, 'cached_rows': 0}
                   for f in ('annual', 'ttm')} for m in ('EPS', 'OCF', 'EBITDA')}
    found = 0
    stage = 'projection_counts'
    for batch in batches(['official-view:' + c + ':analysis' for c in codes]):
        for row in execute(db, PROJECTION_SQL, (batch,)):
            key, _, oversized, source, basis, version, groups = row
            found += 1
            code = key.split(':')[1]
            if oversized:
                states['oversized_not_verified'] += 1
                continue
            if (source is None or source != revisions.get('official:' + code)
                    or basis != revisions.get('share-basis:' + code)
                    or version != str(PROJECTION_VERSION)):
                states['stale_not_verified'] += 1
                continue
            states['current_cache'] += 1
            for group in groups:
                item = metrics[group['metric']][group['frequency']]
                item['securities_with_cached_numeric_rows'] += 1
                item['cached_rows'] += group['rows']
                bounds(item, group['first'], group['last'])
    states['missing_not_verified'] += len(codes) - found
    return {'total': len(codes), 'cache_states': states, 'stored_metric_rows': metrics,
            'comparison_verified': None, 'comparison_tested': 0,
            'comparison_not_verified': len(codes)}


def universe_diagnostic(db, observed_at):
    global stage
    stage = 'universe_diagnostic'
    # Exactly one PK and three scalar outputs. PostgreSQL validates textual JSON
    # under the unchanged 3s timeout; no body is transferred or jsonb built.
    row = execute(db, 'SELECT octet_length(payload),json_typeof(payload::json) '
                     'FROM market_snapshots WHERE key=%s', ('universe',)).fetchone()
    kind = row[1] if row else None
    if kind not in (None, 'object', 'array', 'string', 'number', 'boolean', 'null'):
        raise Refused('unexpected_json_type')
    return {'observed_at': observed_at,
            'universe_diagnostic': {'exists': int(row is not None),
                                    'payload_bytes': row[0] if row else None,
                                    'top_level_type': kind}}


# Scope v2 uses code/market; retain name/security_type too, because these are the
# identity/classification fields of the saved universe producer. No facts, logos,
# URLs, financial histories or source traces cross the database connection.
UNIVERSE_SQL = """
WITH source AS MATERIALIZED (
 SELECT updated_at,octet_length(payload) AS source_bytes,payload::json AS body
 FROM market_snapshots WHERE key=%s
), identity_source AS MATERIALIZED (
 SELECT source.updated_at,source.source_bytes,u.received_at,u.rows,
        CASE WHEN json_typeof(u.rows)='array' THEN json_array_length(u.rows) END AS n
 FROM source CROSS JOIN LATERAL
 json_to_record(source.body) AS u(received_at text,rows json)
), projected AS MATERIALIZED (
 SELECT updated_at,source_bytes,n,
        json_build_object('received_at',received_at,'rows',
          (SELECT json_agg(json_build_object('code',r.code,'market',r.market,
                          'name',r.name,'security_type',r.security_type))
           FROM json_to_recordset(CASE WHEN n BETWEEN 1 AND 10000
                THEN rows ELSE '[]'::json END)
           AS r(code text,market text,name text,security_type text)))::text AS identities
 FROM identity_source
)
SELECT updated_at,source_bytes,n,octet_length(identities),
       CASE WHEN octet_length(identities)<=16777216 THEN identities END
FROM projected
"""


def read_universe(db):
    # A single large JSON value must be parsed even though only identities leave
    # PostgreSQL. This exception applies to this exact PK SELECT, not the batches.
    # SET LOCAL is transaction-scoped: failure takes main()'s ROLLBACK path and
    # ends the audit. Never retry a timed-out statement or mask its SQLSTATE.
    check()
    db.execute('SET LOCAL statement_timeout=10000')
    row = execute(db, UNIVERSE_SQL, ('universe',)).fetchone()
    db.execute('SET LOCAL statement_timeout=3000')
    check()
    return row


def coverage_report(db, captured, included, scope_version):
    global stage
    stage = 'universe_projection'
    # A future scope contract must be reviewed before reducing its input fields.
    if scope_version != 2:
        raise Refused('scope_version_review_required')
    universe = read_universe(db)
    if universe is None or universe[4] is None:
        raise Refused('universe_required')
    revision, source_bytes, row_count, projection_bytes, encoded = universe
    payload = json.loads(encoded)
    rows = payload.get('rows')
    if (not isinstance(rows, list) or not 1 <= len(rows) <= 10000
            or row_count != len(rows)):
        raise Refused('universe_invalid')
    stage = 'scope_identity'
    codes = sorted({r['code'] for r in rows if included(r)})
    if not codes or any(not re.fullmatch(r'[0-9A-Z]{6}', c) for c in codes):
        raise Refused('identity_invalid')
    return {'observed_at': captured[0].isoformat(), 'scope_version': scope_version,
            'universe_revision_at': stamp(revision),
            'universe_received_at': stamp(payload.get('received_at')),
            'universe_source_bytes': source_bytes,
            'universe_projection_bytes': projection_bytes,
            'target_securities': len(codes), 'controls': controls(db),
            'collection': collect(db, codes), 'history_jobs': jobs(db, codes),
            'analysis_cache': projections(db, codes)}


def main():
    global stage, PROJECTION_VERSION
    stage = 'imports'
    import psycopg
    from services.market_dashboard.service_scope import included, VERSION as SCOPE_VERSION
    from services.market_dashboard.official_views import PROJECTION_VERSION

    stage = 'deadline_setup'
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(MAX_SECONDS)
    stage = 'database_scheme'
    url = os.environ.get('MARKET_DATABASE_URL', '')
    if not url.startswith(('postgresql://', 'postgresql+psycopg://')):
        raise Refused('database_required')
    url = url.replace('postgresql+psycopg://', 'postgresql://', 1)
    stage = 'connect'
    with psycopg.connect(url, connect_timeout=5, autocommit=True,
                        options='-c statement_timeout=3000 -c lock_timeout=1000 '
                                '-c idle_in_transaction_session_timeout=10000') as db:
        stage = 'begin_readonly'
        db.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
        try:
            stage = 'transaction_contract'
            captured = execute(db, 'SELECT transaction_timestamp(), '
                "current_setting('transaction_read_only'),current_setting('transaction_isolation')").fetchone()
            if captured[1:] != ('on', 'repeatable read'):
                raise Refused('readonly_required')
            report = (universe_diagnostic(db, captured[0].isoformat())
                      if globals().get('DIAGNOSE_UNIVERSE')
                      else coverage_report(db, captured, included, SCOPE_VERSION))
        except Exception:
            # Preserve the first failure's stage/SQLSTATE even if cleanup fails.
            try:
                db.execute('ROLLBACK')
            except Exception:
                pass
            raise
        else:
            stage = 'rollback'
            db.execute('ROLLBACK')
    signal.alarm(0)
    stage = 'serialize_report'
    report.update(query_count=queries, elapsed_seconds=round(time.monotonic()-started, 3))
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Never expose exception/SQL/DSN/provider text. A failed snapshot is not a report.
        code = getattr(error, 'sqlstate', None)
        print(json.dumps({'audit_failed': 1, 'complete': 0, 'query_count': queries,
                          'stage': stage, 'error_type': type(error).__name__,
                          'sqlstate': code if isinstance(code, str)
                          and re.fullmatch(r'[A-Z0-9]{5}', code) else None,
                          'elapsed_seconds': round(time.monotonic()-started, 3)}))
        raise SystemExit(1)
'''


def safe_failure(value):
    """Forward only fixed-stage/class/SQLSTATE/numeric diagnostic fields."""
    result = {"audit_failed": 1, "complete": 0}
    stages = {
        "imports", "deadline_setup", "database_scheme", "connect", "begin_readonly",
        "transaction_contract", "universe", "universe_diagnostic", "universe_projection",
        "scope_identity",
        "controls", "collection",
        "projection_revisions", "projection_counts", "rollback", "serialize_report",
        "discover_container", "container_count", "container_python", "decode_inner",
        "inner_exit", "ssh", "decode_remote", "remote_exit", "write_output",
        "jobs.history", "jobs.raw-history", "jobs.investor-trading", "jobs.program-trades",
        "jobs.short-selling", "jobs.credit-trades", "jobs.securities-lending",
    }
    if value.get("stage") in stages:
        result["stage"] = value["stage"]
    error_type = value.get("error_type")
    if isinstance(error_type, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", error_type):
        result["error_type"] = error_type
    sqlstate = value.get("sqlstate")
    if isinstance(sqlstate, str) and re.fullmatch(r"[A-Z0-9]{5}", sqlstate):
        result["sqlstate"] = sqlstate
    for name in ("query_count", "elapsed_seconds", "child_exit_code", "container_count"):
        if type(value.get(name)) in (int, float) and -255 <= value[name] < 10**9:
            result[name] = value[name]
    return result


def remote_source(diagnose_universe=False):
    import inspect

    return """import json, re, subprocess, sys
SAFE_FAILURE
INNER_DECLARATION
REPORT_KEY_DECLARATION
filters=['docker','ps','-q','--filter','label=com.docker.compose.project=korea-stocks',
         '--filter','label=com.docker.compose.service=market-api']
stage='discover_container'
details={}
try:
    ids=subprocess.check_output(filters,timeout=10,text=True,stderr=subprocess.DEVNULL).split()
    stage='container_count'
    details['container_count']=len(ids)
    if len(ids)!=1: raise RuntimeError()
    stage='container_python'
    r=subprocess.run(['docker','exec','-i','-w','/app',ids[0],'python','-'],
        input=INNER.encode(),capture_output=True,timeout=135)
    details['child_exit_code']=r.returncode
    stage='decode_inner'
    v=json.loads(r.stdout)
    if v.get('audit_failed'):
        print(json.dumps(safe_failure(v)))
        sys.exit(1)
    stage='inner_exit'
    if r.returncode or REPORT_KEY not in v: raise RuntimeError()
    print(json.dumps(v,ensure_ascii=False))
except Exception as error:
    print(json.dumps(safe_failure({**details,'stage':stage,'error_type':type(error).__name__})))
    sys.exit(1)
""".replace("SAFE_FAILURE", inspect.getsource(safe_failure), 1).replace(
        "INNER_DECLARATION", "INNER=" + repr(
            "DIAGNOSE_UNIVERSE=" + repr(bool(diagnose_universe)) + "\n" + INNER), 1).replace(
        "REPORT_KEY_DECLARATION", "REPORT_KEY=" + repr(
            "universe_diagnostic" if diagnose_universe else "target_securities"), 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--diagnose-universe", action="store_true",
                        help="Two SELECTs: transaction contract and one universe PK diagnostic")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.run:
        print(json.dumps({"executed": False, "batch_keys": 256,
                          "diagnostic_selects": 2 if args.diagnose_universe else None,
                          "universe_projection_timeout_seconds":
                          3 if args.diagnose_universe else 10,
                          "statement_timeout_seconds": 3, "snapshot_deadline_seconds": 120}))
        return
    if args.output is None or not args.output.is_absolute() or not args.output.parent.is_dir():
        parser.error("existing absolute output parent required")
    if args.output.exists():
        parser.error("output must be new")
    stage = "ssh"
    details = {}
    try:
        result = subprocess.run([*SSH, "python3 -"],
                                input=remote_source(args.diagnose_universe).encode(),
                                capture_output=True, timeout=155)
        details["child_exit_code"] = result.returncode
        stage = "decode_remote"
        value = json.loads(result.stdout)
        if value.get("audit_failed"):
            print(json.dumps(safe_failure(value)))
            raise SystemExit(1)
        stage = "remote_exit"
        report_key = "universe_diagnostic" if args.diagnose_universe else "target_securities"
        if result.returncode or report_key not in value:
            raise RuntimeError()
    except Exception as error:
        print(json.dumps(safe_failure({**details, "stage": stage,
                                       "error_type": type(error).__name__})))
        raise SystemExit(1) from None
    try:
        with args.output.open("x", encoding="utf8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
    except Exception as error:
        print(json.dumps(safe_failure({"stage": "write_output",
                                       "error_type": type(error).__name__})))
        raise SystemExit(1) from None
    print(json.dumps({"observed_at": value["observed_at"],
                      report_key: value[report_key], "query_count": value["query_count"]}))


if __name__ == "__main__":
    main()
