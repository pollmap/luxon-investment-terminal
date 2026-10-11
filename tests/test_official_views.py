"""fixture_non_production: read models never replace or silently stale source facts."""
from services.market_dashboard.store import Store
from services.market_dashboard.official_views import read_view


def test_projected_reads_do_not_load_large_source(tmp_path, monkeypatch):
    s=Store('sqlite://',tmp_path)
    raw={'company':{'name':'fixture'},'xbrl_facts':[{'raw':'x'*10000}], 'statements':[1]}
    s.put('official:005930',raw)
    original=s.get
    def get(key,*args,**kwargs):
        assert key != 'official:005930'
        return original(key,*args,**kwargs)
    monkeypatch.setattr(s,'get',get)
    assert read_view(s,'005930','company')=={'company':{'name':'fixture'}}
    assert read_view(s,'005930','fundamentals')['statements']==[1]
    assert original('official:005930')==raw


def test_correction_updates_source_and_views_in_same_transaction(tmp_path):
    s=Store('sqlite://',tmp_path)
    s.put('official:005930',{'company':{'name':'old'}})
    revision=s.revisions(('official:005930',))['official:005930']
    assert not s.put('official:005930',{'company':{'name':'old'}})
    assert s.revisions(('official:005930',))['official:005930']==revision
    s.put('official:005930',{'company':{'name':'corrected'}})
    assert read_view(s,'005930','company')['company']['name']=='corrected'
    state=s.get('official-view:005930:company')
    assert state['source_revision']==s.revisions(('official:005930',))['official:005930']


def test_old_or_missing_projection_rebuilds_without_rewriting_source(tmp_path):
    s=Store('sqlite://',tmp_path)
    s.put('official:005930',{'company':{'name':'verified'}})
    revision=s.revisions(('official:005930',))['official:005930']
    s.put('official-view:005930:company',{'source_revision':'outdated','payload':{'company':{'name':'wrong'}}})
    assert read_view(s,'005930','company')['company']['name']=='verified'
    assert s.revisions(('official:005930',))['official:005930']==revision
    assert read_view(s,'999999','company') is None


def test_independent_share_review_update_and_retraction_rebuild_analysis_only(tmp_path):
    s=Store('sqlite://',tmp_path)
    raw={'company':{'name':'fixture'},'xbrl_facts':[],
         'share_basis_evidence':{'basis_id':'old-embedded'}}
    s.put('official:005930',raw)
    revision=s.revisions(('official:005930',))['official:005930']
    assert read_view(s,'005930','analysis')['share_basis_evidence']['basis_id']=='old-embedded'
    s.put('share-basis:005930',{'basis_id':'reviewed-new'})
    assert read_view(s,'005930','analysis')['share_basis_evidence']['basis_id']=='reviewed-new'
    state=s.get('official-view:005930:analysis')
    assert state['share_basis_revision']==s.revisions(('share-basis:005930',))['share-basis:005930']
    assert s.get('official:005930')==raw
    assert s.revisions(('official:005930',))['official:005930']==revision
    company=s.get('official-view:005930:company')
    s.put('share-basis:005930',{})
    assert read_view(s,'005930','analysis')['share_basis_evidence']=={}
    assert s.get('official-view:005930:company')==company


def test_basis_change_during_projection_does_not_publish_stale_cache(tmp_path,monkeypatch):
    from services.market_dashboard import official_views
    s=Store('sqlite://',tmp_path);s.put('official:005930',{'xbrl_facts':[]})
    s.put('share-basis:005930',{'basis_id':'first'})
    old=s.get('official-view:005930:analysis')
    original=official_views.project
    def project(source,view):
        s.put('share-basis:005930',{'basis_id':'second'})
        return original(source,view)
    monkeypatch.setattr(official_views,'project',project)
    assert read_view(s,'005930','analysis')['share_basis_evidence']['basis_id']=='first'
    assert s.get('official-view:005930:analysis')==old
    monkeypatch.setattr(official_views,'project',original)
    assert read_view(s,'005930','analysis')['share_basis_evidence']['basis_id']=='second'


def test_review_controls_actual_ttm_value_and_retraction_without_source_rewrite(tmp_path):
    from tests.test_eps_calculation import observation
    s=Store('sqlite://',tmp_path);facts=[]
    for start,end,published in [('2025-01-01','2025-03-31','2025-05-10'),
        ('2025-04-01','2025-06-30','2025-08-10'),('2025-07-01','2025-09-30','2025-11-10'),
        ('2025-10-01','2025-12-31','2026-03-10')]:
        facts+=observation(start,end,100,10,published,share_basis=None)
    raw={'xbrl_facts':facts};s.put('official:005930',raw)
    assert read_view(s,'005930','analysis')['ttm_eps']['rows'][-1]['value'] is None
    evidence={'company_id':'fixture','security_code':'005930','verified':True,'no_split_interval':True,
        'start':'2025-01-01','end':'2026-03-31','basis_id':'review-v1',
        'source_trace':{'source_url':'https://example.test/review','sha256':'a'*64}}
    s.put('share-basis:005930',evidence)
    assert read_view(s,'005930','analysis')['ttm_eps']['rows'][-1]['value']==40
    # An old pre-tagged source cannot keep verification after withdrawal.
    tagged={**raw,'xbrl_facts':[{**f,'per_share_basis':'review-v1'} for f in facts]}
    s.put('official:005930',tagged)
    s.put('share-basis:005930',{})
    assert read_view(s,'005930','analysis')['ttm_eps']['rows'][-1]['value'] is None
    assert s.get('official:005930')==tagged
