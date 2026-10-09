"""Scoped, reversible retirement of obsolete provider read models; raw evidence stays."""
import argparse
import hashlib
import json
import re
from sqlalchemy import select, or_, not_
from .store import Store, now

SOURCES = {'kis','kiwoom','kiwoom_rest','kiwoom_websocket','한국투자증권 API','KIS','KIWOOM'}
PREFIXES = ('kiwoom-','kis-','provider-kiwoom','provider-kis')
DROP = object()

def obsolete(trace):
    return isinstance(trace,dict) and (trace.get('source') in SOURCES or trace.get('provider') in SOURCES)

def clean(value):
    if isinstance(value,list):
        return [v for row in value if (v:=clean(row)) is not DROP]
    if not isinstance(value,dict):
        return value
    if obsolete(value) or obsolete(value.get('source_trace')):
        return DROP
    result={}
    omit=set()
    for trace_key,fields in {
        'quote_trace':('quote_trace','price','change','as_of','received_at','market_status','venue'),
        'brokers_trace':('brokers_trace','brokers','brokers_received_at'),
        'program_trace':('program_trace','program','program_status'),
    }.items():
        if obsolete(value.get(trace_key)):
            omit.update(fields)
    for key,item in value.items():
        if key in omit:
            continue
        updated=clean(item)
        if updated is not DROP:
            result[key]=updated
    return result

def migrate(store, *, apply=False, backup_sha256=None):
    if apply and not re.fullmatch(r'[a-f0-9]{64}',backup_sha256 or ''):
        raise ValueError('A verified pre-migration backup SHA256 is required')
    report={'version':1,'apply':apply,'backup_sha256':backup_sha256,'deleted':[],'updated':[],'at':now()}
    with store.read_connection() as db:
        key_column=store.snapshots.c.key
        candidates=or_(*(key_column.startswith(p) for p in PREFIXES),
            *(store.snapshots.c.payload.contains(name) for name in ('kiwoom','KIWOOM','"kis"','"KIS"','한국투자증권 API')))
        excluded=or_(*(key_column.startswith(p) for p in ('official:','official-view:','dart','xbrl','filing','toss','retirement:')))
        keys=list(db.execute(select(key_column).where(candidates,not_(excluded))).scalars())
    # Audit/source archives and normalized financial facts are deliberately outside this migration.
    for key in keys:
        delete=key.startswith(PREFIXES)
        if key.startswith(('official:','official-view:','dart','xbrl','filing','toss','retirement:')):
            continue
        value=store.get(key)
        replacement=DROP if delete else clean(value)
        if replacement is DROP:
            report['deleted'].append(key)
        elif replacement!=value:
            report['updated'].append(key)
        else:
            continue
        if apply:
            # An audit snapshot preserves the exact changed record for field-level recovery.
            identity=hashlib.sha256(key.encode()).hexdigest()[:32]
            store.put('retirement:v1:'+identity,{'key':key,'before':value,'backup_sha256':backup_sha256})
            if replacement is DROP:
                with store.lock,store.engine.begin() as db:
                    db.execute(store.snapshots.delete().where(store.snapshots.c.key==key))
                store._drop_cached(key)
                if store.redis:
                    store.redis.delete('market:snapshot:'+key)
            else:
                store.put(key,replacement)
    if apply:
        store.put('retirement:v1:result',report)
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--backup-sha256')
    args=parser.parse_args()
    print(json.dumps(migrate(Store(),apply=args.apply,backup_sha256=args.backup_sha256),ensure_ascii=False))
