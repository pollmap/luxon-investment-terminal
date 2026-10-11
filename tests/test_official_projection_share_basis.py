"""fixture_non_production: eager worker views preserve reviewed unit dependencies."""
from copy import deepcopy

from services.market_dashboard import official_views
from services.market_dashboard.official_views import read_view
from services.market_dashboard.store import Store
from tests.test_eps_calculation import observation


def source_and_review():
    facts=[]
    for start,end,published in [('2025-01-01','2025-03-31','2025-05-10'),
        ('2025-04-01','2025-06-30','2025-08-10'),('2025-07-01','2025-09-30','2025-11-10'),
        ('2025-10-01','2025-12-31','2026-03-10')]:
        facts+=observation(start,end,100,10,published,share_basis=None)
    evidence={'company_id':'fixture','security_code':'005930','verified':True,'no_split_interval':True,
        'start':'2025-01-01','end':'2026-03-31','basis_id':'review-v1',
        'source_trace':{'source_url':'https://example.test/review','sha256':'a'*64}}
    return {'company':{'name':'fixture'},'xbrl_facts':facts},evidence


def test_worker_refresh_and_process_restart_preserve_review_without_read_repair(tmp_path):
    url='sqlite:///'+str(tmp_path/'market.db')
    source,evidence=source_and_review()
    store=Store(url,tmp_path)
    store.put('share-basis:005930',evidence)
    for revision in ('first','refetched'):
        refreshed={**source,'financials_received_at':revision}
        store.put('official:005930',refreshed)
        cached=store.get('official-view:005930:analysis')
        current=store.revisions(('official:005930','share-basis:005930'))
        assert cached['source_revision']==current['official:005930']
        assert cached['share_basis_revision']==current['share-basis:005930']
        assert cached['payload']['ttm_eps']['rows'][-1]['value']==40
        assert store.get('official:005930')==refreshed
        store.engine.dispose()
        store=Store(url,tmp_path)
    assert not store.put('official:005930',refreshed)
    assert store.get('official-view:005930:analysis')==cached
    store.engine.dispose()


def test_missing_ledger_keeps_embedded_contract_and_empty_ledger_retracts_it(tmp_path):
    store=Store('sqlite://',tmp_path)
    source,evidence=source_and_review()
    source={**source,'share_basis_evidence':evidence,
            'xbrl_facts':[{**f,'per_share_basis':'stale-tag'} for f in source['xbrl_facts']]}
    store.put('official:005930',source)
    cached=store.get('official-view:005930:analysis')
    assert cached['share_basis_revision'] is None
    assert cached['payload']['ttm_eps']['rows'][-1]['value']==40
    store.put('share-basis:005930',{})
    store.put('official:005930',{**source,'financials_received_at':'new'})
    cached=store.get('official-view:005930:analysis')
    assert cached['payload']['share_basis_evidence']=={}
    assert cached['payload']['ttm_eps']['rows'][-1]['status']=='share_basis_required'
    assert cached['payload']['ttm_eps']['rows'][-1]['value'] is None


def test_new_disclosure_source_is_used_and_unreviewed_quarter_not_promoted(tmp_path):
    store=Store('sqlite://',tmp_path)
    source,evidence=source_and_review()
    store.put('share-basis:005930',evidence)
    store.put('official:005930',source)
    first=store.get('official-view:005930:analysis')
    updated=deepcopy(source)
    updated['xbrl_facts']+=observation('2026-04-01','2026-06-30',120,10,'2026-08-10',share_basis=None)
    store.put('official:005930',updated)
    cached=store.get('official-view:005930:analysis')
    assert cached['source_revision']!=first['source_revision']
    prior=[r for r in cached['payload']['ttm_eps']['rows'] if r['period_end']=='2025-12-31']
    assert prior and all(r['value']==40 for r in prior)
    assert all(r['status']!='ready' for r in cached['payload']['ttm_eps']['rows'] if r['period_end']>evidence['end'])
    assert store.get('share-basis:005930')==evidence


def test_ledger_change_during_worker_projection_is_rejected_on_read(tmp_path,monkeypatch):
    store=Store('sqlite://',tmp_path)
    source,evidence=source_and_review()
    store.put('share-basis:005930',evidence)
    initial=store.revisions(('share-basis:005930',))['share-basis:005930']
    original=official_views.project
    with store.engine.begin() as transaction:
        def project(payload,view):
            if view=='analysis':
                store.put('share-basis:005930',{},_connection=transaction)
            return original(payload,view)
        monkeypatch.setattr(official_views,'project',project)
        store.put('official:005930',source,_connection=transaction)
    cached=store.get('official-view:005930:analysis')
    assert cached['share_basis_revision']==initial
    assert cached['payload']['ttm_eps']['rows'][-1]['value']==40
    monkeypatch.setattr(official_views,'project',original)
    repaired=read_view(store,'005930','analysis')
    assert repaired['ttm_eps']['rows'][-1]['status']=='share_basis_required'
    assert repaired['share_basis_evidence']=={}
    assert store.get('official-view:005930:analysis')['share_basis_revision']!=initial
