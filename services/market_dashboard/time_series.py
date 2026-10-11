"""Incremental normalized history. Legacy snapshots migrate on their next write."""
import hashlib
import json
import os
import time
from contextlib import nullcontext

from sqlalchemy import Column, MetaData, String, Table, Text, func, select

from .source_trace_store import SourceTraceStore, compact_payloads


def stable(value):
    if isinstance(value,dict):
        return {
            k: stable(v)
            for k, v in value.items()
            if k not in ('source_trace', 'received_at', 'collected_at')
        }
    if isinstance(value,list):
        return [stable(v) for v in value]
    return value

class TimeSeries:
    def __init__(self, store):
        self.store=store
        metadata=MetaData()
        self.table=Table('market_time_series',metadata,
            Column('series',String(128),primary_key=True),Column('time',String(40),primary_key=True),
            Column('digest',String(64),nullable=False),Column('payload',Text,nullable=False))
        metadata.create_all(store.engine)
        self.source_traces = SourceTraceStore(store)
        # Two-phase rollout: dual readers first, reference writes only after the
        # deployment's rollback floor excludes older inline-only releases.
        self.shared_source_traces = os.getenv('MARKET_SHARED_SOURCE_TRACES', '0') == '1'

    def upsert(self,key,rows,*,connection=None,stats=None):
        if self.store.engine.dialect.name=='sqlite':
            from sqlalchemy.dialects.sqlite import insert
        else:
            from sqlalchemy.dialects.postgresql import insert
        unique={}
        for row in rows:
            stamp=row.get('time') or row.get('date')
            if not stamp:
                raise ValueError('Time-series observation lacks a date')
            digest=hashlib.sha256(json.dumps(stable(row),sort_keys=True,allow_nan=False).encode()).hexdigest()
            if stamp in unique and unique[stamp]['digest']!=digest:
                raise ValueError('Conflicting time-series observations')
            unique[stamp] = {
                'series': key,
                'time': stamp,
                'digest': digest,
                # Plain JSON remains readable by previous releases; omit only
                # serialization whitespace, never evidence or historical values.
                'payload': json.dumps(row, ensure_ascii=False, allow_nan=False,
                                      sort_keys=True, separators=(',', ':')),
            }
        context = self.store.engine.begin() if connection is None else nullcontext(connection)
        with self.store.lock, context as db:
            previous={}
            stamps=list(unique)
            for offset in range(0,len(stamps),500):
                previous.update(db.execute(select(self.table.c.time,self.table.c.digest)
                    .where(self.table.c.series==key,self.table.c.time.in_(stamps[offset:offset+500]))).all())
            changed=[r for stamp,r in unique.items() if previous.get(stamp)!=r['digest']]
            inserted=sum(stamp not in previous for stamp in unique)
            updated=len(changed)-inserted
            if self.shared_source_traces and changed:
                bodies, evidence = compact_payloads([r['payload'] for r in changed])
                self.source_traces.persist(evidence, db)
                changed = [{**row, 'payload': body} for row, body in zip(changed, bodies)]
            for i in range(0,len(changed),100):
                statement=insert(self.table).values(changed[i:i+100])
                db.execute(statement.on_conflict_do_update(index_elements=[self.table.c.series,self.table.c.time],
                    set_={'digest':statement.excluded.digest,'payload':statement.excluded.payload}))
        if stats is not None:
            stats.update({'inserted_rows':inserted,'updated_rows':updated,
                          'unchanged_rows':len(unique)-len(changed)})
        return len(changed)

    def rows(self,key,reverse=False,*,start=None,end=None,limit=None):
        query=select(self.table.c.payload).where(self.table.c.series==key)
        if start:
            query=query.where(self.table.c.time>=start)
        if end:
            query=query.where(self.table.c.time<=end)
        query=query.order_by(self.table.c.time.desc() if reverse else self.table.c.time)
        if limit is not None:
            query=query.limit(limit)
        with self.store.read_connection() as db:
            values=db.execute(query).scalars().all()
        return self.source_traces.restore_payloads(values)

    def bounds(self,key):
        with self.store.read_connection() as db:
            return tuple(db.execute(select(func.min(self.table.c.time),func.max(self.table.c.time))
                .where(self.table.c.series==key)).one())

def put_series(store,key,payload,*,reverse=False,stats=None):
    from .store import now
    series=store.time_series
    rows=payload['rows']
    write_started=time.perf_counter()
    with store.lock, store.engine.begin() as db:
        write_stats={}
        changed=series.upsert(key,rows,connection=db,stats=write_stats)
        prior=db.execute(select(store.snapshots.c.payload).where(store.snapshots.c.key==key)).scalar()
        previous=json.loads(prior) if prior else {}
        stamps=[row.get('time') or row.get('date') for row in rows]
        stamps=[stamp for stamp in stamps if stamp]
        if (previous.get('_series_ref')==key and isinstance(previous.get('row_count'),int)
                and 'first_time' in previous and 'last_time' in previous):
            # Series rows are append/correction only. Recounting every historical
            # observation on every page made deep backfills progressively slower.
            count=previous['row_count']+write_stats['inserted_rows']
            first_values = [
                value for value in (previous.get('first_time'), min(stamps) if stamps else None)
                if value
            ]
            last_values = [
                value for value in (previous.get('last_time'), max(stamps) if stamps else None)
                if value
            ]
            first=min(first_values) if first_values else None
            last=max(last_values) if last_values else None
        else:
            # One-time migration/recovery path for legacy or orphaned series.
            count,first,last=db.execute(select(func.count(),func.min(series.table.c.time),func.max(series.table.c.time))
                .select_from(series.table).where(series.table.c.series==key)).one()
        # Rows, cursor and revision commit together, including same-size corrections.
        metadata={**{k:v for k,v in payload.items() if k!='rows'},
            '_series_ref':key,'_series_reverse':reverse,'row_count':count,
            'first_time':first,'last_time':last,
            'series_revision':now() if changed else previous.get('series_revision')}
        written=store.put(key,metadata,_connection=db)
    if stats is not None:
        stats.update(write_stats)
        stats['write_path_ms']=round((time.perf_counter()-write_started)*1000,3)
    if written:
        store.notify_changed(key)
