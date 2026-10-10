"""Fixtures are synthetic and never constitute live provider evidence."""
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from services.market_dashboard.market_context import (
    BRIEFS, DOCUMENTS, LINKS, ContextRepository, attach_routes, market_context_loop,
)
from services.market_dashboard.store import Store


@pytest.fixture
def setup_context(tmp_path):
    store = Store('sqlite://', tmp_path)
    rows = [
        {'code': '005930', 'name': '삼성전자', 'market': 'KOSPI', 'security_type': 'stock',
         'price': 100, 'change': 2.5, 'value': 1000, 'as_of': '2026-10-02T15:30:00+09:00', 'venue': 'KRX'},
        {'code': '000660', 'name': 'SK하이닉스', 'market': 'KOSPI', 'security_type': 'stock',
         'price': 200, 'change': None, 'value': None, 'as_of': '2026-10-02T15:30:00+09:00', 'venue': 'KRX'},
        {'code': '005935', 'name': '삼성전자우', 'market': 'KOSPI', 'security_type': 'stock',
         'price': 90, 'change': -1, 'value': 500, 'as_of': '2026-10-01T15:30:00+09:00', 'venue': 'KRX'},
        {'code': '999999', 'name': '제외', 'market': 'KONEX', 'value': 9999, 'as_of': '2026-10-03'},
        {'code': '888888', 'name': 'ETF', 'market': 'KOSPI', 'security_type': 'ETF', 'value': 8888, 'as_of': '2026-10-03'},
    ]
    store.put('universe', {'rows': rows, 'received_at': '2026-10-08T10:00:00Z'})
    repository = ContextRepository(store)
    repository.initialize()
    app = FastAPI()
    attach_routes(app, '/api/market/v1', lambda: store)
    return store, repository, TestClient(app)


def snapshot(title='반도체 소식', stamp='2026-10-02T16:00:00+09:00', article='000001'):
    return {'source': 'fixture_non_production', 'source_digest': 'fixture-evidence',
        'received_at': '2026-10-08T10:00:00Z', 'rows': [{
            'title': title, 'url': f'https://n.news.naver.com/mnews/article/001/{article}',
            'publisher': 'fixture', 'published_at': stamp,
            'body': 'Never expose article body', 'client_secret': 'Do not expose'}]}


def test_latest_receipt_does_not_change_market_date_or_merge_stale_rows(setup_context):
    store, repo, client = setup_context
    store.put('indices', {'rows': [{'code': 'KOSPI', 'price': 100, 'as_of': '2026-10-01T15:30:00+09:00'},
                                 {'code': 'KOSDAQ', 'price': 200, 'as_of': '2026-10-02T15:30:00+09:00'}]})
    assert repo.capture_market() == '2026-10-02'
    result = client.get('/api/market/v1/market-brief').json()
    assert result['date'] == '2026-10-02'
    assert result['excluded_other_dates'] == 1
    assert [r['code'] for r in result['groups'][0]['rows']] == ['005930', '000660']
    assert result['groups'][0]['breadth'] == {'up': 1, 'down': 0, 'flat': 0, 'unknown': 1}
    assert result['groups'][0]['value'] == 1000
    assert [r['code'] for r in result['indices']] == ['KOSDAQ']
    absent = client.get('/api/market/v1/market-brief?date=2026-09-30').json()
    assert absent['status'] == 'not_collected'
    assert absent['groups'] == [] and absent['indices'] == []


def test_market_dates_persist_and_same_day_growth_is_bounded(setup_context):
    store, repo, _ = setup_context
    repo.capture_market()
    old = repo.brief('2026-10-02')
    for amount in (1100, 1200, 1300):
        universe = store.get('universe')
        universe['rows'][0]['value'] = amount
        store.put('universe', universe)
        repo.capture_market()
    with store.read_connection() as db:
        assert db.execute(select(func.count()).select_from(BRIEFS)).scalar() == 1
    assert repo.brief()['revision'] != old['revision']
    universe = store.get('universe')
    universe['rows'][0]['as_of'] = '2026-10-05T15:30:00+09:00'
    universe['rows'][0]['value'] = 1400
    store.put('universe', universe)
    repo.capture_market()
    assert repo.brief('2026-10-02')['groups'][0]['value'] == 1300
    assert repo.brief()['date'] == '2026-10-05'
    assert repo.brief()['available_dates'] == ['2026-10-05', '2026-10-02']


def test_different_trading_venues_remain_separate(setup_context):
    store, repo, _ = setup_context
    universe = store.get('universe')
    universe['rows'][1].update(venue='통합', value=2000)
    store.put('universe', universe)
    repo.capture_market()
    assert [(r['venue'], r['value']) for r in repo.brief()['groups']] == [('KRX', 1000), ('통합', 2000)]


def test_news_versions_are_preserved_and_pagination_deduplicates_associations(setup_context):
    store, repo, client = setup_context
    repo.ingest_news('005930', snapshot())
    repo.ingest_news('000660', snapshot())
    later_receipt = snapshot()
    later_receipt.update(received_at='2026-10-09T01:00:00Z', source_digest='new-batch-hash')
    repo.ingest_news('005930', later_receipt)
    repo.ingest_news('005930', snapshot(title='정정된 제목'))
    repo.ingest_news('005930', snapshot(article='000002', stamp='2026-10-01T15:00:00+09:00'))
    assert repo.ingest_news('999999', snapshot()) == 0
    with store.read_connection() as db:
        assert db.execute(select(func.count()).select_from(DOCUMENTS)).scalar() == 3
        assert db.execute(select(func.count()).select_from(LINKS)).scalar() == 3
    first = client.get('/api/market/v1/news?limit=1').json()
    assert first['has_more']
    assert first['rows'][0]['title'] == '정정된 제목'
    assert {r['code'] for r in first['rows'][0]['securities']} == {'005930', '000660'}
    assert 'body' not in first['rows'][0] and 'client_secret' not in first['rows'][0]
    last = client.get('/api/market/v1/news?limit=1&offset=1').json()
    assert not last['has_more'] and last['rows'][0]['url'].endswith('000002')
    assert client.get('/api/market/v1/news?date=2026-10-02').json()['rows'][0]['title'] == '정정된 제목'
    assert client.get('/api/market/v1/news?date=2026-10-08').json()['rows'] == []


def test_unknown_news_date_is_not_replaced_with_collection_time(setup_context):
    _, repo, _ = setup_context
    repo.ingest_news('005930', snapshot(stamp='invalid'))
    assert repo.news()['rows'][0]['published_at'] is None
    assert repo.news(day='2026-10-08')['rows'] == []
    assert repo.relations('005930')['rows'] == []


def test_correction_with_identical_receipt_clock_preserves_latest_and_original(setup_context, monkeypatch):
    _, repo, _ = setup_context
    monkeypatch.setattr('services.market_dashboard.market_context.now', lambda: '2026-10-11T00:00:00+00:00')
    repo.ingest_news('005930', snapshot(title='원래 제목'))
    repo.ingest_news('005930', snapshot(title='정정된 제목'))
    assert repo.news()['rows'][0]['title'] == '정정된 제목'
    with repo.store.read_connection() as db:
        observations = db.execute(select(DOCUMENTS).order_by(DOCUMENTS.c.recorded_at)).mappings().all()
    assert len(observations) == 2
    assert observations[0]['recorded_at'] < observations[1]['recorded_at']
    assert observations[0]['published_at'] == observations[1]['published_at']
    # Refetch of an older unchanged version must not make it the latest revision.
    repo.ingest_news('005930', snapshot(title='원래 제목'))
    assert repo.news()['rows'][0]['title'] == '정정된 제목'


def test_news_association_is_not_promoted_to_business_relation(setup_context):
    _, repo, client = setup_context
    repo.ingest_news('005930', snapshot())
    repo.ingest_news('000660', snapshot())
    result = client.get('/api/market/v1/securities/005930/relations').json()
    assert len(result['rows']) == 1
    relation = result['rows'][0]
    assert relation['kind'] == 'shared_news'
    assert relation['label'] == '같은 관련 기사'
    assert relation['evidence']['excerpt_kind'] == 'headline'
    assert relation['review_status'] == 'source_observed'
    assert repo.relations('005930', day='2026-10-01')['rows'] == []


def business_relation(**patch):
    return {'subject_code': '005930', 'object_code': '000660', 'kind': 'business_partner',
        'review_status': 'reviewed', 'valid_from': '2026-10-02', 'valid_until': '2026-10-05',
        'evidence': {'url': 'https://example.com/issuer/report', 'document_id': 'fixture_non_production',
            'excerpt': 'Fixture evidence only', 'published_at': '2026-10-02'}, **patch}


def test_business_evidence_validity_and_candidate_separation(setup_context, monkeypatch):
    monkeypatch.setattr('services.market_dashboard.market_context.now', lambda: '2026-10-11T00:00:00+00:00')
    _, repo, _ = setup_context
    repo.save_relation(business_relation(review_status='candidate'))
    assert repo.relations('005930', day='2026-10-03')['rows'] == []
    repo.save_relation(business_relation())
    assert len(repo.relations('005930', day='2026-10-03')['rows']) == 1
    assert repo.relations('005930', day='2026-10-01')['rows'] == []
    assert repo.relations('005930', day='2026-10-06')['rows'] == []
    for invalid in ({'evidence': {}}, {'object_code': '999999'}, {'valid_from': '2026-10-01'},
                    {'review_status': 'source_observed'}, {'kind': 'same_news_means_supplier'}):
        with pytest.raises(ValueError):
            repo.save_relation(business_relation(**invalid))


def test_bad_links_do_not_escape_into_public_news(setup_context):
    _, repo, _ = setup_context
    for url in ('javascript:alert(1)', 'https://name:secret@example.com/data',
                'http://127.0.0.1/private', 'http://localhost/secret'):
        data = snapshot()
        data['rows'][0]['url'] = url
        assert repo.ingest_news('005930', data) == 0


def test_input_validation_and_scope(setup_context):
    _, _, client = setup_context
    for suffix in ('?date=2026-02-30', '?date=2026-10-02T09:00:00', '?date=2099-01-01', '?limit=101', '?offset=-1'):
        assert client.get('/api/market/v1/news' + suffix).status_code == 422
    assert client.get('/api/market/v1/news?code=999999').status_code == 404
    assert client.get('/api/market/v1/securities/888888/relations').status_code == 404


def test_public_reads_do_not_write_or_require_migration(tmp_path, monkeypatch):
    store = Store('sqlite://', tmp_path)
    def forbidden():
        raise AssertionError('Public query attempted a write')
    monkeypatch.setattr(store.engine, 'begin', forbidden)
    repo = ContextRepository(store)
    assert repo.news()['status'] == 'not_collected'
    assert repo.brief()['status'] == 'not_collected'
    assert repo.relations('005930')['status'] == 'not_collected'


def test_projection_imports_existing_news_without_any_provider_call(setup_context):
    store, repo, _ = setup_context
    store.put('news:005930', snapshot())
    asyncio.run(market_context_loop(store, once=True))
    assert repo.news()['rows'][0]['title'] == '반도체 소식'
    assert repo.brief()['date'] == '2026-10-02'
