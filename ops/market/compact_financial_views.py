"""Compact replaceable read models only; immutable facts and archives stay intact.

Run with the collection worker stopped, after a verified database backup.
"""
import json
from sqlalchemy import select, func
from services.market_dashboard.store import Store
from services.market_dashboard.fact_store import FactStore


def run():
    store=Store()
    facts=FactStore(store)
    results=[]
    with store.read_connection() as db:
        keys=[r[0] for r in db.execute(select(store.snapshots.c.key).where(
            store.snapshots.c.key.startswith('official:'),func.length(store.snapshots.c.payload)>1_000_000))]
    for key in keys:
        code=key.removeprefix('official:')
        snapshot=store.get(key,{},use_redis=False)
        old=snapshot.get('xbrl_facts',[])
        if not old or snapshot.get('xbrl_projection_version')==1:
            continue
        identities={row.get('fact_id') for row in old}
        companies={row.get('company_id') for row in old}
        if None in identities or len(companies)!=1 or None in companies:
            results.append({'code':code,'status':'preserved_identity_not_verified'})
            continue
        with store.read_connection() as db:
            present={r[0] for r in db.execute(select(facts.facts.c.id).where(facts.facts.c.company_id==next(iter(companies))))}
        if not identities.issubset(present):
            results.append({'code':code,'status':'preserved_facts_missing'})
            continue
        before=len(json.dumps(snapshot,ensure_ascii=False).encode())
        snapshot['xbrl_facts']=facts.analysis_facts(next(iter(companies)))
        snapshot['xbrl_projection_version']=1
        store.put(key,snapshot)
        after=len(json.dumps(snapshot,ensure_ascii=False).encode())
        results.append({'code':code,'status':'compacted','before_bytes':before,'after_bytes':after,
                        'source_facts_preserved':len(identities),'view_facts':len(snapshot['xbrl_facts'])})
    print(json.dumps(results))


if __name__=='__main__': run()
