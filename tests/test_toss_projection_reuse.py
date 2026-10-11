"""fixture_non_production: unchanged heads, corrections and projection recovery."""
import pytest

from services.market_dashboard.store import Store
from services.market_dashboard.time_series import put_series
from services.market_dashboard.toss import TossCollector, projection_field_hash


def seed(store, value=10):
    put_series(store, 'toss:investor-trading:005930', {'rows': [
        {'date': '2026-09-30', 'individual': {'netBuyVolume': value},
         'source_trace': {'source': 'fixture_non_production'}}
    ]}, reverse=True)


def test_unchanged_head_skips_history_but_same_size_correction_is_projected(tmp_path, monkeypatch):
    store = Store('sqlite://', tmp_path)
    collector = TossCollector(store, object())
    seed(store)
    collector._project_trading('005930', 'investor-trading', {'status': 'ready'})
    original = store.time_series.rows
    reads = []
    def measured(*args, **kwargs):
        reads.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(store.time_series, 'rows', measured)
    seed(store)
    collector._project_trading('005930', 'investor-trading', {'status': 'ready'})
    assert reads == []
    seed(store, 25)
    collector._project_trading('005930', 'investor-trading', {'status': 'ready'})
    assert len(reads) == 1
    assert store.get('toss-normalized:flows:005930')['rows'][0]['individual'] == 25
    marker = store.metadata('toss-projection:investor-trading:005930')
    assert (marker['rebuilt'], marker['skipped']) == (2, 1)


def test_stale_head_and_overwritten_output_are_not_reused(tmp_path):
    store = Store('sqlite://', tmp_path)
    collector = TossCollector(store, object())
    seed(store)
    collector._project_trading('005930', 'investor-trading', {'status': 'ready'})
    collector._project_trading('005930', 'investor-trading', {'status': 'stale'})
    assert store.get('toss-normalized:flows:005930')['status'] == 'stale'
    store.put('toss-normalized:flows:005930', {'rows': []})
    collector._project_trading('005930', 'investor-trading', {'status': 'stale'})
    assert len(store.get('toss-normalized:flows:005930')['rows']) == 1


def test_partial_write_failure_retries_without_a_new_provider_revision(tmp_path, monkeypatch):
    store = Store('sqlite://', tmp_path)
    collector = TossCollector(store, object())
    seed(store)
    original = store.put
    def fail(key, *args, **kwargs):
        if key == 'detail:005930':
            raise RuntimeError('fixture interrupted write')
        return original(key, *args, **kwargs)
    monkeypatch.setattr(store, 'put', fail)
    with pytest.raises(RuntimeError):
        collector._project_trading('005930', 'investor-trading', {'status': 'ready'})
    assert store.metadata('toss-projection:investor-trading:005930') is None
    monkeypatch.setattr(store, 'put', original)
    collector._project_trading('005930', 'investor-trading', {'status': 'ready'})
    assert store.get('detail:005930')['flows'][0]['individual'] == 10


def test_unrelated_detail_refresh_preserves_normalized_snapshot(tmp_path, monkeypatch):
    store=Store('sqlite://',tmp_path)
    collector=TossCollector(store,object())
    seed(store)
    collector._project_trading('005930','investor-trading',{'status':'ready'})
    normalized_key='toss-normalized:flows:005930'
    normalized=store.metadata(normalized_key)
    revision=store.revisions((normalized_key,))
    detail={**store.get('detail:005930'),'company':{'name':'updated fixture'}}
    store.put('detail:005930',detail)
    def forbidden(*args,**kwargs):
        pytest.fail('Unchanged investor observations must not load history')
    monkeypatch.setattr(store.time_series,'rows',forbidden)
    # Persisted marker works after collector recreation, without a process cache.
    collector=TossCollector(store,object())
    collector._project_trading('005930','investor-trading',{'status':'ready'})
    assert store.metadata(normalized_key)==normalized
    assert store.revisions((normalized_key,))==revision
    assert store.get('detail:005930')==detail
    marker=store.metadata('toss-projection:investor-trading:005930')
    assert marker['skipped']==1
    assert marker['outputs']==store.revisions(marker['outputs'])


def test_lending_update_does_not_rebuild_unchanged_credit_history(tmp_path, monkeypatch):
    store=Store('sqlite://',tmp_path)
    collector=TossCollector(store,object())
    put_series(store,'toss:credit-trades:005930',{'rows':[
        {'date':'2026-09-30','marginLoan':{'balanceQuantity':10},
         'stockLoan':{'balanceQuantity':20},'source_trace':{'source':'fixture_non_production'}}]},reverse=True)
    collector._project_trading('005930','credit-trades',{'status':'ready'})
    credit=store.get('financing:005930')['credit']
    put_series(store,'toss:securities-lending:005930',{'rows':[
        {'date':'2026-09-30','balanceQuantity':30,
         'source_trace':{'source':'fixture_non_production'}}]},reverse=True)
    collector._project_trading('005930','securities-lending',{'status':'ready'})
    keys=('financing:005930','toss-normalized:financing:005930')
    revisions=store.revisions(keys)
    def forbidden(*args,**kwargs):
        pytest.fail('Other financing endpoint must not invalidate credit observations')
    monkeypatch.setattr(store.time_series,'rows',forbidden)
    collector._project_trading('005930','credit-trades',{'status':'ready'})
    assert store.revisions(keys)==revisions
    assert store.get('financing:005930')['credit']==credit
    assert store.get('financing:005930')['lending']['rows'][0]['balance']==30
    assert store.metadata('toss-projection:credit-trades:005930')['skipped']==1


@pytest.mark.parametrize('target,field',[('detail:005930','flows'),('toss-normalized:flows:005930','rows')])
def test_same_length_owned_field_damage_is_rebuilt(tmp_path, monkeypatch, target, field):
    from copy import deepcopy
    store=Store('sqlite://',tmp_path)
    collector=TossCollector(store,object())
    seed(store)
    collector._project_trading('005930','investor-trading',{'status':'ready'})
    damaged=deepcopy(store.get(target))
    damaged[field][0]['individual']=999
    store.put(target,damaged)
    original=store.time_series.rows
    reads=[]
    def measured(*args,**kwargs):
        reads.append(args[0])
        return original(*args,**kwargs)
    monkeypatch.setattr(store.time_series,'rows',measured)
    collector._project_trading('005930','investor-trading',{'status':'ready'})
    assert reads==['toss:investor-trading:005930']
    assert store.get(target)[field][0]['individual']==10


def test_removed_source_is_not_certified_by_an_old_projection(tmp_path):
    store=Store('sqlite://',tmp_path)
    collector=TossCollector(store,object())
    seed(store)
    collector._project_trading('005930','investor-trading',{'status':'ready'})
    key='toss:investor-trading:005930'
    with store.engine.begin() as db:
        db.execute(store.snapshots.delete().where(store.snapshots.c.key==key))
        db.execute(store.time_series.table.delete().where(store.time_series.table.c.series==key))
    collector._project_trading('005930','investor-trading',{'status':'no_data'})
    assert store.get('toss-normalized:flows:005930')['rows']==[]
    assert store.get('detail:005930')['flows']==[]
    seed(store,25)
    collector._project_trading('005930','investor-trading',{'status':'ready'})
    assert store.get('toss-normalized:flows:005930')['rows'][0]['individual']==25


def test_field_hash_ignores_unowned_metadata_but_preserves_values_order_and_trace():
    base={'rows':[
        {'value':1,'source_trace':{'unit':'주','source':'fixture'}}, {'value':2}
    ], 'status':'ready'}
    reordered={'status':'ready','received_at':'later','rows':[
        {'source_trace':{'source':'fixture','unit':'주'},'value':1}, {'value':2}
    ]}
    fields=('rows','status')
    assert projection_field_hash(base,fields)==projection_field_hash(reordered,fields)
    reverse={**base,'rows':list(reversed(base['rows']))}
    assert projection_field_hash(base,fields)!=projection_field_hash(reverse,fields)
    stale={**base,'status':'stale'}
    assert projection_field_hash(base,fields)!=projection_field_hash(stale,fields)
    different_evidence={'rows':[
        {'value':1,'source_trace':{'unit':'원','source':'fixture'}}, {'value':2}
    ], 'status':'ready'}
    assert projection_field_hash(base,fields)!=projection_field_hash(different_evidence,fields)
    assert projection_field_hash({},fields) is None


def test_intervening_output_writer_is_not_certified_by_new_revision(tmp_path,monkeypatch):
    from copy import deepcopy
    store=Store('sqlite://',tmp_path)
    collector=TossCollector(store,object())
    seed(store)
    original=store.put
    fired=False
    def interleaved(key,payload,**kwargs):
        nonlocal fired
        changed=original(key,payload,**kwargs)
        if key=='detail:005930' and not fired:
            fired=True
            damaged=deepcopy(payload)
            damaged['flows'][0]['individual']=999
            original(key,damaged)
        return changed
    monkeypatch.setattr(store,'put',interleaved)
    collector._project_trading('005930','investor-trading',{'status':'ready'})
    assert store.metadata('toss-projection:investor-trading:005930')['outputs']=={}
    monkeypatch.setattr(store,'put',original)
    collector._project_trading('005930','investor-trading',{'status':'ready'})
    assert store.get('detail:005930')['flows'][0]['individual']==10
