"""Read bounded public-data receipts without providers, hydration or schema writes."""

import argparse
import json
import subprocess
from pathlib import Path

INNER = r'''
import json, os, re
from datetime import UTC, datetime
import psycopg
from services.market_dashboard.toss_contract import PUBLIC_GET_PATHS

def label(value):
    valid=isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', value)
    return value if valid else None

def stamp(value):
    if not isinstance(value, str) or len(value)>40: return None
    try: return datetime.fromisoformat(value.replace('Z','+00:00')).isoformat()
    except ValueError: return None

def count(value):
    return value if type(value) in (int,float) and 0<=value<1e16 else None

keys=['toss-api:'+p for p in PUBLIC_GET_PATHS]+[
    'provider-toss-stream','toss-backfill-progress',
    *['storage-capacity:'+q for q in (
        'price-history','financial-reports','xbrl','dated-accounts','share-basis')]]
report={'observed_at':datetime.now(UTC).isoformat(),
    'scope':'stored receipts, not field/option validation',
    'api_release':label(os.environ.get('MARKET_RELEASE')),'in_scope':len(PUBLIC_GET_PATHS),
    'endpoints':[], 'capacity':{},'provider_calls':0,'writes':0}
dsn=os.environ['MARKET_DATABASE_URL'].replace('postgresql+psycopg://','postgresql://',1)
with psycopg.connect(dsn,connect_timeout=5,autocommit=True,
    options='-c statement_timeout=3000 -c idle_in_transaction_session_timeout=10000') as db:
    db.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    try:
        records={k:json.loads(p) for k,p in db.execute(
            'SELECT key,payload FROM market_snapshots WHERE key=ANY(%s) '
            'AND octet_length(payload)<65536',(keys,))}
        for path in PUBLIC_GET_PATHS:
            r=records.get('toss-api:'+path,{})
            last=r.get('last_success') or {}
            metrics=r.get('metrics') or {}
            source=last.get('sha256') or r.get('sha256')
            report['endpoints'].append({'path':path,'status':label(r.get('status')),
                'last_attempt_at':stamp(r.get('last_attempt_at')),
                'last_success_at':stamp(last.get('received_at') or r.get('received_at')),
                'source_sha256':source if isinstance(source,str)
                    and re.fullmatch(r'[a-f0-9]{64}',source) else None,
                'session_requests':count(metrics.get('request_count')),
                'session_successes':count(metrics.get('successful_responses')),
                'last_rows':count(metrics.get('last_response_rows')),
                'last_upstream_ms':count(metrics.get('last_upstream_ms'))})
        stream=records.get('provider-toss-stream',{})
        report['stream']={'status':label(stream.get('status')),
            'accepted':len(stream.get('accepted') or []),
            'rejected':len(stream.get('rejected') or []),
            'received_at':stamp(stream.get('received_at')),'last_event_at':stamp(stream.get('last_event_at')),
            'lossy':stream.get('lossy') is True,'actual_market_session_verified':False}
        for key in keys:
            if key.startswith('storage-capacity:') or key=='toss-backfill-progress':
                r=records.get(key,{})
                c=r.get('capacity') or r
                report['capacity'][key]={'status':label(r.get('status')),'profile':label(c.get('profile')),
                    'used_percent':count(c.get('used_percent')),'free_bytes':count(c.get('free_bytes')),
                    'stop_used_percent':count(c.get('stop_used_percent')),
                    'resume_below_used_percent':count(c.get('resume_below_used_percent')),
                    'write_verified':c.get('write_verified'),'at':stamp(r.get('updated_at'))}
    finally: db.execute('ROLLBACK')
report['successful_receipts']=sum(
    bool(r['last_success_at'] and r['source_sha256']) for r in report['endpoints'])
print(json.dumps(report))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or not args.output.parent.is_dir():
        raise ValueError('New output in an existing folder required')
    command = ['ssh', '-o', 'BatchMode=yes', 'chanhee-vps',
               'docker exec -i -w /app korea-stocks-market-api-1 python -']
    result = subprocess.run(command, input=INNER.encode(), capture_output=True, timeout=45)
    if result.returncode:
        raise RuntimeError('Read-only receipt inspection failed')
    report = json.loads(result.stdout)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    keys = ('observed_at', 'api_release', 'in_scope', 'successful_receipts', 'stream', 'capacity')
    print(json.dumps({k: report[k] for k in keys}))


if __name__ == '__main__':
    main()
