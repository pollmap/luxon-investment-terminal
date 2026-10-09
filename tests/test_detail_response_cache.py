"""Isolated fixture data: detail freshness and bounded response reuse."""

import gzip
import importlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from services.market_dashboard.response_cache import RevisionResponseCache
from services.market_dashboard.store import Store

market = importlib.import_module("services.market_dashboard.app")


def test_summary_exposes_application_timing_separate_from_freshness(detail_client):
    _, client = detail_client
    for path in ('/search?q=005930', '/securities/005930?view=summary'):
        response = client.get(market.BASE + path)
        assert response.status_code == 200
        timing = re.fullmatch(r'app;dur=(\d+\.\d+)', response.headers['server-timing'].split(',')[0])
        assert timing and float(timing[1]) >= 0
        assert int(response.headers['x-market-validated-at']) > 0
        validated = int(response.headers['x-market-validated-at'])
        expires = int(response.headers['x-accel-expires'].removeprefix('@')) * 1000
        assert 0 < expires + 1000 - validated <= 1000


@pytest.mark.asyncio
async def test_proxy_expiry_does_not_restart_an_existing_validation_clock():
    from starlette.requests import Request
    from starlette.responses import Response
    request = Request({'type':'http', 'method':'GET', 'path':market.BASE+'/search',
                       'query_string':b'q=005930', 'headers':[]})

    async def existing(_request):
        return Response(b'{}', headers={'X-Market-Validated-At':'1791477600123'})

    response = await market.public_validation_clock(request, existing)
    assert response.headers['X-Market-Validated-At'] == '1791477600123'
    assert response.headers['X-Accel-Expires'] == '@1791477600'


@pytest.mark.asyncio
async def test_failed_read_has_no_proxy_expiry():
    from starlette.requests import Request
    from starlette.responses import Response
    request = Request({'type':'http', 'method':'GET', 'path':market.BASE+'/search',
                       'query_string':b'', 'headers':[]})

    async def failed(_request):
        return Response(status_code=503)

    response = await market.public_validation_clock(request, failed)
    assert 'X-Accel-Expires' not in response.headers


def test_summary_projection_excludes_large_histories_and_tracks_real_changes(detail_client,monkeypatch):
    store,client=detail_client
    detail={'company':'fixture_non_production','financials':[{'name':'매출액','value':10}],
            'flows':[{'date':'2025-01-01','individual':1}]*5000}
    store.put('detail:005930',detail)
    persisted_summary=store.get('detail-summary:005930')
    assert persisted_summary['detail']=={'company':'fixture_non_production'}
    first=store.revisions(['detail-summary:005930'])
    store.put('detail:005930',{**detail,'flows':[{'date':'2025-01-02','individual':2}]})
    assert store.revisions(['detail-summary:005930'])==first
    original=store.get
    def guarded(key,*args,**kwargs):
        assert key not in ('detail:005930','toss-normalized:flows:005930','collection:005930')
        return original(key,*args,**kwargs)
    monkeypatch.setattr(store,'get',guarded)
    result=client.get(market.BASE+'/securities/005930?view=summary')
    assert result.status_code==200
    assert result.json()['detail']=={'company':'fixture_non_production'}
    assert 'collection' not in result.json()
    store.put('detail:005930',{**detail,'company':'updated'})
    assert client.get(market.BASE+'/securities/005930?view=summary').json()['detail']['company']=='updated'


def test_flow_update_does_not_rebuild_summary_body(detail_client,monkeypatch):
    store,client=detail_client
    store.put('detail:005930',{'company':'first','flows':[{'value':1}]})
    builds=[]
    original=market.detail_snapshot
    def counted(*args,**kwargs):
        builds.append(1)
        return original(*args,**kwargs)
    monkeypatch.setattr(market,'detail_snapshot',counted)
    path=market.BASE+'/securities/005930?view=summary'
    assert client.get(path).json()['detail']['company']=='first'
    store.put('detail:005930',{'company':'first','flows':[{'value':2}]})
    assert client.get(path).json()['detail']['company']=='first'
    assert len(builds)==1
    store.put('detail:005930',{'company':'second','flows':[{'value':2}]})
    assert client.get(path).json()['detail']['company']=='second'
    assert len(builds)==2


@pytest.fixture
def detail_client(tmp_path, monkeypatch):
    store = Store(url="sqlite://", root=tmp_path)
    row = {"code": "005930", "name": "fixture_non_production", "price": 100,
           "source_trace": {}, "as_of": "2026-09-23"}
    store.put("universe", {"rows": [row]})
    monkeypatch.setattr(market, "get_store", lambda: store)
    monkeypatch.setattr(market, "_detail_responses", RevisionResponseCache())
    monkeypatch.setattr(market, "_search_responses", RevisionResponseCache())
    with TestClient(market.app) as client:
        yield store, client
    store.engine.dispose()


@pytest.mark.parametrize("kind", ["detail", "prices", "collection", "filings", "official"])
def test_dependency_update_is_visible_without_waiting(detail_client, kind):
    store, client = detail_client
    url = market.BASE + "/securities/005930"
    old = client.get(url).json()
    store.put(f"{kind}:005930", {"rows": [{"value": 123}], "fixture_non_production": True})
    new = client.get(url).json()
    assert new[kind] != old[kind]
    assert new[kind]["rows"][0]["value"] == 123


def test_eps_response_and_etag_follow_share_review_and_retraction(detail_client):
    from test_eps_calculation import observation
    store,client=detail_client
    facts=[]
    for start,end,published in [('2025-01-01','2025-03-31','2025-05-10'),
        ('2025-04-01','2025-06-30','2025-08-10'),('2025-07-01','2025-09-30','2025-11-10'),
        ('2025-10-01','2025-12-31','2026-03-10')]:
        facts+=observation(start,end,100,10,published,share_basis=None)
    store.put('dart-corporations',{'codes':{'005930':'fixture'}})
    store.put('official:005930',{'xbrl_facts':facts})
    path=market.BASE+'/securities/005930/financials/per-share'
    first=client.get(path)
    assert first.json()['ttm_status']=='share_basis_required'
    store.put('share-basis:005930',{'verified':True,'no_split_interval':True,
        'basis_id':'fixture','security_code':'005930','company_id':'fixture',
        'start':'2025-01-01','end':'2026-03-31',
        'source_trace':{'source_url':'https://example.test/fixture','sha256':'fixture_non_production'}})
    second=client.get(path,headers={'If-None-Match':first.headers['etag']})
    assert second.status_code==200 and second.json()['ttm_status']=='ready'
    assert second.headers['etag']!=first.headers['etag']
    assert client.get(path,headers={'If-None-Match':second.headers['etag']}).status_code==304
    store.put('share-basis:005930',{})
    third=client.get(path,headers={'If-None-Match':second.headers['etag']})
    assert third.status_code==200 and third.json()['ttm_status']=='share_basis_required'


def test_search_reuses_results_and_invalidates_immediately(detail_client, monkeypatch):
    store, client = detail_client
    row = dict(store.get("universe")["rows"][0], name="삼성전자", market="KOSPI")
    store.put("universe", {"rows": [row]})
    calls = []
    original = market.search_payload

    def counted(query, limit):
        calls.append(query)
        return original(query, limit)

    monkeypatch.setattr(market, "search_payload", counted)
    url = market.BASE + "/search?q=ㅅㅅ"
    for _ in range(3):
        assert client.get(url).json()["rows"][0]["price"] == 100
    assert len(calls) == 1
    store.put("quotes", {"005930": {"price": 200, "quote_trace": {}, "as_of": "2026-09-24"}})
    assert client.get(url).json()["rows"][0]["price"] == 200
    assert len(calls) == 2
    store.put("universe", {"rows": []})
    assert client.get(url).json()["rows"] == []


@pytest.mark.parametrize("kind", ["quotes", "enriched", "universe"])
def test_read_model_update_does_not_wait_for_one_second_ttl(detail_client, kind):
    store, client = detail_client
    url = market.BASE + "/securities/005930"
    assert client.get(url).json()["security"]["price"] == 100
    if kind == "universe":
        payload = store.get(kind)
        payload["rows"][0]["price"] = 200
    else:
        payload = {"005930": {"price": 200, "as_of": "2026-09-24", "quote_trace": {}}}
    store.put(kind, payload)
    assert client.get(url).json()["security"]["price"] == 200


def test_removed_security_and_content_negotiation(detail_client):
    store, client = detail_client
    url = market.BASE + "/securities/005930"
    compressed = client.get(url, headers={"Accept-Encoding": "gzip"})
    plain = client.get(url, headers={"Accept-Encoding": "gzip;q=0, identity"})
    assert compressed.json() == plain.json()
    assert compressed.headers["content-encoding"] == "gzip"
    assert "content-encoding" not in plain.headers
    store.put("universe", {"rows": []})
    assert client.get(url).status_code == 404


def test_unchanged_detail_has_no_body_but_new_data_is_returned(detail_client):
    store, client = detail_client
    url = market.BASE + "/securities/005930"
    first = client.get(url)
    etag = first.headers["etag"]
    unchanged = client.get(url, headers={"If-None-Match": etag})
    assert unchanged.status_code == 304
    assert unchanged.content == b""
    store.put("detail:005930", {"financials": [{"value": 7}]})
    changed = client.get(url, headers={"If-None-Match": etag})
    assert changed.status_code == 200
    assert changed.headers["etag"] != etag
    assert changed.json()["detail"]["financials"][0]["value"] == 7


def test_no_stale_redis_payload_can_be_cached_under_new_revision(detail_client):
    store, client = detail_client

    class OldRedis:
        def get(self, key):
            return json.dumps({"rows": [{"wrong": True}]}).encode()

        def setex(self, *args):
            pass

    store.redis = OldRedis()
    store.put("official:005930", {"rows": [{"value": 7}]})
    result = client.get(market.BASE + "/securities/005930").json()
    assert result["official"]["rows"] == [{"value": 7}]
    assert result["security"]["code"] == "005930"


def test_revision_change_during_build_is_not_cached():
    class Revisions:
        revision = 1

        def revisions(self, keys):
            return {"value": self.revision}

    store, cache = Revisions(), RevisionResponseCache()

    def racing_build():
        store.revision += 1
        return {"value": "old"}

    cache.response(store, "005930", ("value",), racing_build)
    assert not cache.entries
    body = cache.response(store, "005930", ("value",), lambda: {"value": "new"})
    assert json.loads(gzip.decompress(body))["value"] == "new"


def test_concurrent_readers_share_build_and_lru_is_bounded():
    class Revisions:
        def revisions(self, keys):
            return {"value": 1}

    store, cache = Revisions(), RevisionResponseCache(max_bytes=1000, max_entries=2)
    count = 0
    barrier = threading.Barrier(8)

    def build():
        nonlocal count
        count += 1
        return {"value": 1}

    def read(_):
        barrier.wait(timeout=5)
        return cache.response(store, "005930", ("value",), build)

    with ThreadPoolExecutor(max_workers=8) as pool:
        bodies = list(pool.map(read, range(8)))
    assert count == 1
    assert len(set(bodies)) == 1
    for code in ("000660", "005935"):
        cache.response(store, code, ("value",), build)
    assert len(cache.entries) == 2
    assert (store, "005930") not in cache.entries
    assert cache.size == sum(len(entry[1]) for entry in cache.entries.values()) <= 1000
    tiny = RevisionResponseCache(max_bytes=1)
    tiny.response(store, "005930", ("value",), build)
    assert tiny.size == 0 and not tiny.entries


def test_summary_defers_heavy_sections_and_other_views_remain_available(detail_client):
    store, client = detail_client
    store.put('detail:005930', {'company': {'description': 'fixture'}, 'flows': [{'date':'2026-09-01'}], 'financials': [
        {'name': '매출액', 'value': 10}, {'name': 'PER', 'value': 1}]})
    store.put('prices:005930', {'rows': [{'date':'2026-09-01','close':100}]})
    store.put('official:005930', {'statements': [{'name': 'fixture'}]})
    store.put('filings:005930', {'rows':[{'title':'fixture'}]})
    url = market.BASE + '/securities/005930'
    summary = client.get(url + '?view=summary').json()
    assert summary['prices'] is None and summary['official'] is None
    assert summary['filings']['rows'] == [] and 'flows' not in summary['detail']
    assert 'collection' not in summary
    assert summary['detail']['company'] == {'description': 'fixture'}
    assert 'financials' not in summary['detail']
    assert client.get(url + '?view=activity').json()['detail']['flows']
    assert client.get(url + '?view=fundamentals').json()['official']['statements']
    assert client.get(url + '?view=filings').json()['filings']['rows']
    assert client.get(url).json()['prices']['rows']
    assert client.get(url + '?view=invalid').status_code == 422


def test_summary_payload_keeps_visible_values_but_omits_full_provenance_and_financial_history(detail_client):
    store, client = detail_client
    store.put('universe', {'rows': [{
        'code': '005930', 'name': 'fixture_non_production', 'market': 'KOSPI',
        'price': 100, 'change': 1.5, 'as_of': '2026-10-02', 'received_at': '2026-10-05T04:00:00+09:00',
        'market_status': 'CLOSE', 'volume': 10, 'value': 1000, 'cap': 5000,
        'foreign': 20, 'per': 10, 'pbr': 1, 'roe': 8, 'dividend': 2,
        'source_trace': {'payload': 'x' * 5000},
        'facts': {
            'foreign': {'value': 20, 'period': '2026-10-02', 'unit': '%', 'source_trace': {'payload': 'y' * 5000}},
            'per': {'value': 10, 'period': '2025', 'unit': '배'},
            'not_visible': {'value': 99, 'period': '2025', 'unit': '원'},
        },
        'company': {'products': 'fixture', 'source_trace': {'payload': 'z' * 5000}},
    }]})
    store.put('detail:005930', {
        'company': {'description': 'fixture description', 'source_trace': {'payload': 'w' * 5000}},
        'financials': [{'name': '매출액', 'value': 10, 'period': str(year)} for year in range(2000, 2027)],
    })

    response = client.get(market.BASE + '/securities/005930?view=summary')
    assert response.status_code == 200
    result = response.json()
    assert result['security']['price'] == 100 and result['security']['foreign'] == 20
    assert result['security']['facts']['foreign'] == {'value': 20, 'period': '2026-10-02', 'unit': '%'}
    assert 'not_visible' not in result['security']['facts']
    assert 'source_trace' not in result['security'] and 'quote_trace' not in result['security']
    assert result['detail'] == {'company': {'description': 'fixture description'}}
    assert 'source_trace' not in result['company']
    assert len(response.content) < 10000


def test_depth_only_requests_known_security(detail_client):
    store, client = detail_client
    url = market.BASE + '/securities/'
    assert client.get(url + '999999/depth').status_code == 404
    assert store.get('depth-request:999999') is None
    store.put('provider-depth', {'status':'credentials_missing'})
    assert client.get(url + '005930/depth').json()['book_status'] == 'credentials_missing'
    assert store.get('depth-request:005930')['at']


def test_static_section_etag_survives_quote_update(detail_client):
    store, client = detail_client
    url = market.BASE + '/securities/005930?view=company'
    first = client.get(url)
    store.put('quotes', {'005930': {'price': 222, 'as_of': '2026-09-24', 'quote_trace': {}}})
    assert client.get(url, headers={'If-None-Match':first.headers['etag']}).status_code == 304
    assert client.get(market.BASE + '/securities/005930?view=summary').json()['security']['price'] == 222


def test_company_and_financial_sections_do_not_ship_raw_xbrl(detail_client):
    store, client = detail_client
    store.put('official:005930', {'company': {'corp_name': 'fixture_non_production'},
        'report_facts': {'sections': {}}, 'statements': [{'name': '매출액'}],
        'xbrl_facts': [{'raw': 'x' * 10000}], 'financial_reports': {'raw': 'y' * 10000}})
    url = market.BASE + '/securities/005930'
    company = client.get(url + '?view=company').json()['official']
    assert company['company']['corp_name'] == 'fixture_non_production'
    assert 'xbrl_facts' not in company and 'financial_reports' not in company
    financial = client.get(url + '?view=fundamentals').json()['official']
    assert financial['statements'] and 'xbrl_facts' not in financial
    assert client.get(url).json()['official']['xbrl_facts']


def test_peer_group_never_substitutes_unrelated_sector(detail_client):
    store, client = detail_client
    store.put('universe', {'rows': [
        {'code': '005930', 'name': 'fixture', 'sector': 'A', 'cap': 3, 'source_trace': {}},
        {'code': '000660', 'name': 'fixture', 'sector': 'A', 'cap': 2, 'source_trace': {}},
        {'code': '005935', 'name': 'fixture', 'sector': 'B', 'cap': 7, 'source_trace': {}}]})
    result = client.get(market.BASE + '/securities/005930/peers').json()
    assert [r['code'] for r in result['rows']] == ['005930', '000660']
    assert client.get(market.BASE + '/securities/999999/peers').status_code == 404


def test_identity_index_has_initials_but_no_stale_prices(detail_client):
    store, client = detail_client
    store.put('universe', {'rows': [{'code':'005930','name':'삼성전자','market':'KOSPI','price':100,'aliases':['Samsung']} ]})
    result = client.get(market.BASE + '/search-index')
    row = result.json()['rows'][0]
    assert 'ㅅㅅㅈㅈ' in row['tokens'] and 'samsung' in row['tokens']
    assert 'price' not in row and result.headers['cache-control'] == 'public, max-age=300'
