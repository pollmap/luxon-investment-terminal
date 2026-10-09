"""Provider-shaped fixtures only; these are not claims of live API access."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from services.market_dashboard.krx_openapi import KrxOpenApi, daily_rows, identities
from services.market_dashboard.store import Store

TRACE = {"source": "fixture_non_production", "sha256": "fixture"}
IDENTITY = {
    "ISU_SRT_CD": "005930",
    "ISU_CD": "KR7005930003",
    "ISU_NM": "fixture",
    "KIND_STKCERT_TP_NM": "보통주",
    "SECUGRP_NM": "주권",
    "LIST_DD": "19750611",
}
PRICE = {
    "ISU_CD": "005930",
    "BAS_DD": "20260925",
    "TDD_OPNPRC": "100",
    "TDD_HGPRC": "120",
    "TDD_LWPRC": "90",
    "TDD_CLSPRC": "110",
    "ACC_TRDVOL": "1,000",
}


def test_krx_identity_scope_and_ohlc_dates():
    stocks, excluded = identities(
        [
            IDENTITY,
            {
                **IDENTITY,
                "ISU_SRT_CD": "005935",
                "ISU_CD": "KR7005931001",
                "KIND_STKCERT_TP_NM": "우선주",
            },
            {**IDENTITY, "ISU_SRT_CD": "999999", "SECUGRP_NM": "ETF"},
        ],
        "KOSPI",
        "2026-09-25",
        TRACE,
    )
    assert stocks["005935"]["share_class"] == "preferred" and len(excluded) == 1
    rows, rejected = daily_rows([PRICE, {**PRICE, "ISU_CD": "999999"}], stocks, "2026-09-25", TRACE)
    assert rows["005930"]["volume"] == 1000 and len(rejected) == 1
    rows, rejected = daily_rows([{**PRICE, "TDD_HGPRC": "80"}], stocks, "2026-09-25", TRACE)
    assert not rows and rejected[0]["reason"] == "invalid_ohlc"
    rows, _ = daily_rows(
        [{**PRICE, "TDD_OPNPRC": "0", "ACC_TRDVOL": ""}], stocks, "2026-09-25", TRACE
    )
    assert rows["005930"]["open"] is None and rows["005930"]["volume"] is None


def test_krx_collects_separately_and_preserves_last_good_on_failure(tmp_path):
    store = Store(url="sqlite://", root=tmp_path)
    store.put("bars:005930:KRX:raw", {"rows": [{"date": "2026-09-24", "close": 101}]})
    source = KrxOpenApi(store, None, api_key="fixture_non_production")

    async def get(api, as_of):
        return ([IDENTITY] if "base_info" in api else [{**PRICE, "BAS_DD": as_of}]), TRACE

    source.get = get
    assert asyncio.run(source.collect_day("KOSPI", "2026-09-25"))["status"] == "ready"
    assert asyncio.run(source.collect_day("KOSPI", "2026-09-24"))["status"] == "ready"
    assert store.get("identity:krx:005930")["as_of"] == "2026-09-25"
    assert len(store.get("krx-bars:005930:KRX:raw")["rows"]) == 2
    assert store.get("bars:005930:KRX:raw")["rows"][0]["close"] == 101

    async def fail(*args):
        raise httpx.ConnectError("fixture connection loss")

    source.get = fail
    assert asyncio.run(source.collect_day("KOSPI", "2026-09-23"))["status"] == "error"
    assert len(store.get("krx-bars:005930:KRX:raw")["rows"]) == 2
    store.engine.dispose()


def test_krx_rejects_arbitrary_endpoint_before_sending(tmp_path):
    store = Store(url="sqlite://", root=tmp_path)
    source = KrxOpenApi(store, None, api_key="fixture_non_production")
    with pytest.raises(ValueError):
        asyncio.run(source.get("https://example.invalid", "2026-09-25"))
    store.engine.dispose()


