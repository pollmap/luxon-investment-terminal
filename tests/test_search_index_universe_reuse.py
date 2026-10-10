"""Identity-index cache misses reuse a verified universe; SQLite fixtures only."""

import asyncio
import threading

import httpx
import pytest

from services.market_dashboard import app as market
from services.market_dashboard.response_cache import InFlightResponses, RevisionResponseCache
from services.market_dashboard.store import Store


def universe(name="삼성전자"):
    return {"rows": [
        {"code": "005930", "name": name, "market": "KOSPI", "cap": 100,
         "aliases": ["삼성"], "price": 70000, "logo_url": None},
        {"code": "000660", "name": "SK하이닉스", "market": "KOSPI", "cap": 50},
        {"code": "999999", "name": "제외 fixture", "market": "KONEX", "cap": 200},
    ]}


@pytest.fixture
def source(tmp_path, monkeypatch):
    store = Store(root=tmp_path)
    # Reproduce a snapshot larger than the decode LRU limit without allocating
    # the production 38 MB document. Only the shared universe can retain it.
    store.cache_max_bytes = 1
    store.put("universe", universe())
    monkeypatch.setattr(market, "get_store", lambda: store)
    monkeypatch.setattr(market, "_security_universe_cache", None)
    monkeypatch.setattr(market, "_search_responses", RevisionResponseCache())
    monkeypatch.setattr(market, "_search_flights", InFlightResponses())
    yield store
    store.engine.dispose()


def count_universe_reads(source, monkeypatch):
    calls = []
    original = source.get

    def counted(key, *args, **kwargs):
        if key == "universe":
            calls.append(kwargs.get("use_redis", True))
        return original(key, *args, **kwargs)

    monkeypatch.setattr(source, "get", counted)
    return calls


def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=market.app),
                            base_url="http://fixture")


@pytest.mark.asyncio
async def test_primed_universe_is_not_read_again_on_index_response_miss(source, monkeypatch):
    heads = source.revisions(("universe",))
    market.universe_inputs(source, heads["universe"])
    assert "universe" not in source.cache
    reads = count_universe_reads(source, monkeypatch)
    async with client() as api:
        first = await api.get(market.BASE + "/search-index")
        # Eviction of the small response must not decode the full universe again.
        monkeypatch.setattr(market, "_search_responses", RevisionResponseCache())
        second = await api.get(market.BASE + "/search-index")
    assert reads == []
    assert first.status_code == second.status_code == 200
    assert first.content == second.content
    assert first.json()["rows"] == [
        {"code": "005930", "name": "삼성전자", "market": "KOSPI", "logo_url": None,
         "tokens": ["005930", "삼성전자", "ㅅㅅㅈㅈ", "삼성", "ㅅㅅ"]},
        {"code": "000660", "name": "SK하이닉스", "market": "KOSPI", "logo_url": None,
         "tokens": ["000660", "sk하이닉스", "skㅎㅇㄴㅅ"]},
    ]
    assert first.headers["cache-control"] == "public, max-age=300"


@pytest.mark.asyncio
async def test_cold_index_reads_universe_once_and_validates_after_build(source, monkeypatch):
    reads = count_universe_reads(source, monkeypatch)
    heads = []
    original = source.revisions

    def revisions(keys):
        heads.append(tuple(keys))
        return original(keys)

    monkeypatch.setattr(source, "revisions", revisions)
    async with client() as api:
        first = await api.get(market.BASE + "/search-index")
        assert heads == [("universe",), ("universe",)]
        second = await api.get(market.BASE + "/search-index")
    assert reads == [False]
    assert heads == [("universe",)] * 3
    assert first.content == second.content


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["rename", "remove_row", "empty", "delete_snapshot"])
async def test_revision_changes_and_deletion_do_not_reuse_old_index(source, monkeypatch, change):
    async with client() as api:
        assert (await api.get(market.BASE + "/search-index")).status_code == 200
        replacement = universe("삼성전자 변경")
        if change == "remove_row":
            replacement["rows"] = replacement["rows"][1:]
        elif change == "empty":
            replacement["rows"] = []
        if change == "delete_snapshot":
            with source.engine.begin() as db:
                db.execute(source.snapshots.delete().where(source.snapshots.c.key == "universe"))
        else:
            source.put("universe", replacement)
        reads = count_universe_reads(source, monkeypatch)
        result = await api.get(market.BASE + "/search-index")
    assert result.status_code == 200
    assert reads == [False]
    rows = result.json()["rows"]
    if change == "rename":
        assert rows[0]["name"] == "삼성전자 변경"
    elif change == "remove_row":
        assert [row["code"] for row in rows] == ["000660"]
    else:
        assert rows == []
    assert result.headers["cache-control"] == (
        "no-store" if change == "delete_snapshot" else "public, max-age=300")


@pytest.mark.asyncio
@pytest.mark.parametrize("unknown", [None, "", 7])
async def test_unknown_revision_reads_directly_without_reusing_or_caching(
    source, monkeypatch, unknown,
):
    async with client() as api:
        await api.get(market.BASE + "/search-index")
        shared = market._security_universe_cache
        cached = dict(market._search_responses.entries)
        monkeypatch.setattr(source, "revisions", lambda keys: {"universe": unknown})
        reads = count_universe_reads(source, monkeypatch)
        for name in ("첫 변경", "다음 변경"):
            source.put("universe", universe(name))
            result = await api.get(market.BASE + "/search-index")
            assert result.status_code == 200
            assert result.json()["rows"][0]["name"] == name
            assert result.headers["cache-control"] == "no-store"
        assert reads == [False, False]
        assert market._security_universe_cache is shared
        assert dict(market._search_responses.entries) == cached


@pytest.mark.asyncio
@pytest.mark.parametrize("remove", [False, True])
async def test_write_during_build_does_not_cache_under_new_revision(source, monkeypatch, remove):
    market.universe_inputs(source, source.revisions(("universe",))["universe"])
    original = market.universe_inputs
    entered, release = threading.Event(), threading.Event()

    def paused(*args):
        result = original(*args)
        entered.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(market, "universe_inputs", paused)
    async with client() as api:
        request = asyncio.create_task(api.get(market.BASE + "/search-index"))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            source.put("universe", {"rows": []} if remove else universe("새 이름"))
        finally:
            release.set()
        observed = await request
        assert observed.json()["rows"][0]["name"] == "삼성전자"
        assert not market._search_responses.entries
        current = await api.get(market.BASE + "/search-index")
        if remove:
            assert current.json()["rows"] == []
        else:
            assert current.json()["rows"][0]["name"] == "새 이름"
        assert len(market._search_responses.entries) == 1
