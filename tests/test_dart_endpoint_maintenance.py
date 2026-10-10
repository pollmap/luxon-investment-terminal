"""Fixtures: partial provider maintenance must not stall unrelated real inputs."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from services.market_dashboard.authenticated_sources import (
    AuthenticatedSources,
    DartDailyBudgetExceeded,
    DartProviderUnavailable,
)
from services.market_dashboard.dart_retry import endpoint_identity, provider_retry_for, retry_key
from services.market_dashboard.store import Store

BASE = "https://opendart.fss.or.kr/api/"


def source_for(store, responses):
    calls = []

    class Client:
        async def get(self, url, params):
            calls.append((url, params))
            return httpx.Response(200, content=responses[endpoint_identity(url)])

    source = AuthenticatedSources(SimpleNamespace(store=store, client=Client()))
    source.dart = "fixture_non_production"
    return source, calls


@pytest.mark.parametrize("content", [b'{"status":"800"}', b'<result><status>800</status></result>'])
def test_maintenance_is_shared_for_same_endpoint_only(tmp_path, content):
    store = Store("sqlite://", tmp_path)
    raw = BASE + "fnlttXbrl.xml"
    statement = BASE + "fnlttSinglAcntAll.json"
    source, calls = source_for(store, {raw: content, statement: b'{"status":"000","list":[]}'} )

    async def run():
        with pytest.raises(DartProviderUnavailable):
            await source._dart_get(raw, params={"rcept_no": "fixture"})
        other, blocked = source_for(store, {raw: b"PK"})
        with pytest.raises(DartProviderUnavailable):
            await other._dart_get(raw, params={"rcept_no": "different-filing"})
        assert not blocked
        assert (await source._dart_get(statement, params={})).json()["status"] == "000"

    asyncio.run(run())
    assert len(calls) == 2 and store.get("dart-usage")["count"] == 2
    retry = store.get(retry_key(raw))
    assert retry["scope"] == "endpoint" and retry["endpoint"] == raw and retry["source_hash"]


def test_multiple_outages_do_not_overwrite_each_others_retry(tmp_path):
    store = Store("sqlite://", tmp_path)
    first, second = BASE + "corpCode.xml", BASE + "document.xml"
    source, calls = source_for(store, {first: b'{"status":"800"}', second: b'{"status":"800"}'})

    async def run():
        for endpoint in (first, second, first, second):
            with pytest.raises(DartProviderUnavailable):
                await source._dart_get(endpoint, params={})

    asyncio.run(run())
    assert len(calls) == 2
    assert provider_retry_for(store, first)["endpoint"] == first
    assert provider_retry_for(store, second)["endpoint"] == second


def test_query_secrets_never_enter_retry_keys_or_endpoint_fields(tmp_path):
    store = Store("sqlite://", tmp_path)
    endpoint = BASE + "document.xml"
    source, _ = source_for(store, {endpoint: b'{"status":"800"}'})
    with pytest.raises(DartProviderUnavailable):
        asyncio.run(source._dart_get(endpoint + "?crtfc_key=fixture-secret&rcept_no=1", params={}))
    assert retry_key(endpoint) == retry_key(endpoint + "?crtfc_key=fixture-secret&rcept_no=2")
    for key, value in store.list_prefix("dart-provider-retry").items():
        assert "fixture-secret" not in key + str(value)


def test_legacy_unscoped_retry_stays_conservative_until_deadline(tmp_path):
    store = Store("sqlite://", tmp_path)
    endpoint = BASE + "list.json"
    retry = {"status": "800", "next_at": (datetime.now(UTC) + timedelta(minutes=1)).isoformat()}
    store.put("dart-provider-retry", retry)
    source, calls = source_for(store, {endpoint: b'{"status":"000"}'})
    with pytest.raises(DartProviderUnavailable):
        asyncio.run(source._dart_get(endpoint, params={}))
    assert not calls and provider_retry_for(store, endpoint) == retry
    store.put("dart-provider-retry", {**retry, "next_at": "2000-01-01"})
    assert asyncio.run(source._dart_get(endpoint, params={})).json()["status"] == "000"


def test_daily_limit_is_still_account_wide(tmp_path):
    store = Store("sqlite://", tmp_path)
    first, second = BASE + "fnlttXbrl.xml", BASE + "fnlttSinglAcntAll.json"
    source, calls = source_for(store, {first: b'{"status":"020"}', second: b'{"status":"000"}'})

    async def run():
        for endpoint in (first, second):
            with pytest.raises(DartDailyBudgetExceeded):
                await source._dart_get(endpoint, params={})

    asyncio.run(run())
    assert len(calls) == 1
