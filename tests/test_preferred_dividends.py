import io
import zipfile
from types import SimpleNamespace

import pytest

from services.market_dashboard.corporate_events import collect_events
from services.market_dashboard.preferred_dividends import parse_named_preferred, project_preferred_dividends
from services.market_dashboard.store import Store


def document(name='삼성전자우', amount='375', ordinary='374'):
    return (f'<table><tr><td>3. 1주당 배당금(원)</td><td>보통주식</td><td>{ordinary}</td></tr>'
        '<tr><td>종류주식</td><td>375</td></tr>'
        '<tr><td>6. 배당기준일</td><td>2025-09-30</td></tr>'
        '<tr><td>7. 배당금지급 예정일자</td><td>2025-11-19</td></tr></table>'
        '<table><tr><th>종류주식명</th><th>종류주식구분</th><th>1주당 배당금(원)</th><th>배당금총액(원)</th></tr>'
        f'<tr><td>{name}</td><td>우선주</td><td>{amount}</td><td>100,000,000</td></tr></table>').encode()


def parse(content):
    return parse_named_preferred(content,code='005935',filing_id='20251030000001',
        published_at='2025-10-30',evidence={'source':'fixture_non_production'})


def test_named_annex_selects_own_amount_not_ordinary_or_total():
    row=parse(document())[0]
    assert row['amount']=='375' and row['security_code']=='005935'
    assert row['share_class']=='preferred' and row['share_label']=='삼성전자우'
    assert row['payment_status']=='announced' and not row['verified']
    assert row['scheduled_payment_date']=='2025-11-19'
    assert row['source_trace']['security_mapping']['annex_security_name']=='삼성전자우'


def test_absent_or_conflicting_named_class_is_not_inferred():
    assert parse(document(name='다른 우선주'))==[]
    with pytest.raises(ValueError,match='disagree'):
        parse(document(amount='376'))
    with pytest.raises(ValueError,match='class'):
        parse(document().replace('우선주</td>'.encode(),'보통주</td>'.encode()))


def seed(store):
    store.put('universe',{'rows':[{'code':'005930','name':'삼성전자'},{'code':'005935','name':'삼성전자우'}]})
    store.put('dart-corporations',{'codes':{'005930':'issuer-fixture'}})
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:
        archive.writestr('fixture.xml',document())
    digest=store.raw('dart-events','fixture_non_production',stream.getvalue())
    receipt='20251030000001'
    store.put('corporate-event-index:005930',{'rows':[{'receipt':receipt,'date':'20251030','title':'현금배당결정'}]})
    store.put('corporate-events-job:005930',{'documents':{receipt:{'raw_hash':digest,'status':'parsed'}}})
    store.put('events:005930',{'rows':[{'id':'ordinary','amount':'374'}]})
    return digest


@pytest.mark.asyncio
async def test_preferred_collection_uses_issuer_archive_without_api_or_common_copy(tmp_path):
    store=Store('sqlite://',tmp_path)
    digest=seed(store)
    await collect_events(SimpleNamespace(store=store),'005935')
    first=store.get('events:005935')
    assert first['rows'][0]['amount']=='375'
    assert first['rows'][0]['source_trace']['sha256']==digest
    assert store.get('events:005930')['rows']==[{'id':'ordinary','amount':'374'}]
    assert not first['coverage']['verified']
    await collect_events(SimpleNamespace(store=store),'005935')
    assert store.get('events:005935')==first


def test_missing_archive_is_pending_not_provider_unavailable(tmp_path):
    store=Store('sqlite://',tmp_path)
    seed(store)
    store.put('corporate-events-job:005930',{'documents':{'20251030000001':{'raw_hash':'a'*64}}})
    project_preferred_dividends(store,'005935')
    assert not store.get('events:005935')
    assert store.get('preferred-events-job:005935')['documents']['20251030000001']=='issuer_archive_missing'


def test_archive_replay_cannot_resurrect_an_event_already_corrected(tmp_path):
    store=Store('sqlite://',tmp_path)
    seed(store)
    later={'id':'later','kind':'dividend','filing_id':'20251130000001',
        'title':'[기재정정]현금배당결정','security_code':'005935','share_class':'preferred',
        'record_date':'2025-09-30','amount':'376','currency':'KRW','payment_status':'announced'}
    store.put('events:005935',{'rows':[later]})
    project_preferred_dividends(store,'005935')
    rows=store.get('events:005935')['rows']
    old=next(row for row in rows if row['filing_id']=='20251030000001')
    assert old['superseded_by']=='later'
    assert next(row for row in rows if row['id']=='later')['amount']=='376'


def test_new_preferred_date_evidence_invalidates_completed_projection(tmp_path,monkeypatch):
    """fixture_non_production: payment evidence can arrive after parsing."""
    store=Store('sqlite://',tmp_path)
    digest=seed(store)
    project_preferred_dividends(store,'005935')
    before=store.get('events:005935')
    assert store.get('preferred-events-job:005935')['status']=='ready'
    event=before['rows'][0]
    def forbidden(*args,**kwargs):
        pytest.fail('An observation-only update must not reparse all filing archives')
    monkeypatch.setattr('services.market_dashboard.preferred_dividends.parse_named_preferred',forbidden)
    evidence={
        'id':'fixture-payment','security_code':'005935','share_class':'preferred',
        'record_date':event['record_date'],'amount':event['amount'],'currency':'KRW',
        'reviewed':True,'ex_date':'2025-09-29','confirmed_payment_date':'2025-11-20',
        'source_trace':{'source':'fixture_non_production','source_url':'https://example.test/payment',
            'sha256':'a'*64,'available_at':'2025-11-21'},
    }
    store.put('dividend-observations:005935',{'rows':[evidence]})
    original=store.get('events:005930')
    project_preferred_dividends(store,'005935')
    after=store.get('events:005935')
    row=after['rows'][0]
    assert row['payment_status']=='confirmed' and row['payment_date']=='2025-11-20'
    assert row['scheduled_payment_date']=='2025-11-19' and row['ex_date']=='2025-09-29'
    assert row['verified'] and row['source_trace']['sha256']==digest
    assert row['amount']=='375' and row['share_class']=='preferred'
    assert row['field_evidence']['payment_date'][0]['observation_id']=='fixture-payment'
    assert not after['coverage']['verified']
    assert after['received_at']==before['received_at']
    assert store.get('events:005930')==original
    project_preferred_dividends(store,'005935')
    assert store.get('events:005935')==after
    # Later contradictory evidence must also invalidate the completed cache,
    # while an incomplete replay cannot silently clear the conflict.
    conflict={**evidence,'id':'fixture-conflict','confirmed_payment_date':'2025-11-21'}
    store.put('dividend-observations:005935',{'rows':[evidence,conflict]})
    project_preferred_dividends(store,'005935')
    conflicted=store.get('events:005935')['rows'][0]
    assert not conflicted['verified']
    assert conflicted['status']=='conflicting_dividend_evidence'
    store.put('dividend-observations:005935',{'rows':[evidence]})
    project_preferred_dividends(store,'005935')
    assert store.get('events:005935')['rows'][0]['status']=='conflicting_dividend_evidence'
    store.engine.dispose()
