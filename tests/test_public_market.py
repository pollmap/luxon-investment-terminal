"""Offline contract tests. Fixtures are isolated from the production source store."""

import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from services.market_dashboard.app import BASE, app, assemble_rows, get_store
from services.market_dashboard.authenticated_sources import (
    AuthenticatedSources,
    DartDailyBudgetExceeded,
    normalize_report_section,
    parse_members,
)
from services.market_dashboard.models import Query, apply_query
from services.market_dashboard.providers import (
    PublicProvider,
    normalize_stock,
    number,
    parse_fnguide,
    trace,
)
from services.market_dashboard.store import Store, now


@pytest.fixture
def store(tmp_path):
    return Store(url="sqlite://", root=tmp_path)


def security(code="005930", **values):
    evidence = trace("test_fixture", "https://example.invalid/fixture", "a" * 64, "2026-09-23")
    row = normalize_stock(
        {
            "itemCode": code,
            "stockName": "테스트 종목",
            "closePriceRaw": "10000",
            "accumulatedTradingValueRaw": "250000000000",
            "marketValueRaw": "1000000000000",
            "localTradedAt": "2026-09-23T15:30:00+09:00",
        },
        "KOSPI",
        evidence,
    )
    return {**row, **values}


def test_units_and_unknowns():
    row = security()
    assert row["value"] == 250000000000
    assert row["cap"] == 1000000000000
    assert row["foreign"] is None
    assert number("−2,345") == -2345
    assert number("N/A") is None
    assert number("NaN") is None
    assert row["source_trace"]["available_at"] is None


def test_list_collection_clock_does_not_replace_broker_last_trade(store):
    store.put("universe", {"rows": [security(as_of="2026-09-24T01:00:00+09:00")]})
    store.put("quotes", {"005930": {"price": 10500, "change": 0.55,
        "as_of": "2026-09-23T19:59:59+09:00", "quote_trace": {"source": "toss"}}})
    current = assemble_rows(store)[0][0]
    assert current["price"] == 10500
    assert current["change"] == 0.55
    assert current["as_of"] == "2026-09-23T19:59:59+09:00"


def test_quote_facts_keep_metric_specific_units_and_source_period(store):
    observed_at = "2026-09-23T15:30:00+09:00"
    store.put(
        "universe",
        {"rows": [security()], "coverage": [], "unavailable_markets": []},
    )
    store.put(
        "quotes",
        {
            "005930": {
                "price": 10500,
                "change": 2.5,
                "as_of": observed_at,
                "quote_trace": trace(
                    "test_quote",
                    "https://example.invalid/quote",
                    "d" * 64,
                    observed_at,
                ),
            }
        },
    )

    rows, _ = assemble_rows(store)
    facts = rows[0]["facts"]

    assert facts["price"]["value"] == 10500
    assert facts["price"]["unit"] == "원"
    assert facts["price"]["source_trace"]["unit"] == "원"
    assert facts["change"]["value"] == 2.5
    assert facts["change"]["unit"] == "%"
    assert facts["change"]["source_trace"]["unit"] == "%"
    assert facts["change"]["period"] == observed_at
    assert facts["change"]["source_trace"]["period"] == observed_at


def test_and_or_and_null_sorting():
    rows = [
        security("000001", per=None, roe=20),
        security("000002", per=8, roe=10),
        security("000003", per=15, roe=20),
    ]
    query = Query(
        rules=[{"metric": "roe", "value": 15}],
        any_rules=[
            {"metric": "per", "operator": "lte", "value": 10},
            {"metric": "per", "operator": "gte", "value": 14},
        ],
    )
    assert [r["code"] for r in apply_query(rows, query)["rows"]] == ["000003"]
    for direction in ["asc", "desc"]:
        assert (
            apply_query(rows, Query(sort="per", direction=direction))["rows"][-1]["code"]
            == "000001"
        )
    assert apply_query(rows, Query(codes=[]))["total"] == 0
    with pytest.raises(ValidationError):
        Query(rules=[{"metric": "not_a_metric", "value": 1}])


def test_financial_identity_period_and_estimate():
    headers = [
        {
            "YYMM": f"{2023 + i}/12",
            "EP_CHK": "E" if i == 3 else "A",
            "CONSOL_TYPE": "연결",
            "CD": f"VAL{i + 1}",
        }
        for i in range(4)
    ]
    payload = {
        "header": headers,
        "data": [
            {
                "NAME": "EPS",
                "UNIT": "(원)",
                "VAL1": "100",
                "VAL2": "200",
                "VAL3": "300",
                "VAL4": "400",
            }
        ],
    }
    html = (
        '<input id="cmp_cd" value="005930"><script>snpFinancial: '
        + json.dumps(payload)
        + ",\n</script>"
    )
    parsed = parse_fnguide(html, "005930", {})
    assert parsed["financials"][0]["basis"] == "CFS"
    assert parsed["financials"][0]["unit"] == "원"
    assert parsed["financials"][3]["estimate"] is True
    with pytest.raises(ValueError, match="identity"):
        parse_fnguide(html, "000660", {})


def test_financial_columns_without_period_are_not_published():
    payload = {
        "header": [
            {"YYMM": None, "CONSOL_TYPE": "연결", "CD": "VAL1"},
            {"YYMM": "2025/12", "CONSOL_TYPE": "연결", "CD": "VAL2"},
        ],
        "data": [{"NAME": "EPS", "UNIT": "(원)", "VAL1": "999", "VAL2": "42"}],
    }
    html = (
        '<input id="cmp_cd" value="005930"><script>snpFinancial: '
        + json.dumps(payload)
        + ",\n</script>"
    )
    parsed = parse_fnguide(html, "005930", {})
    assert parsed["financials"] == [
        {
            "name": "EPS",
            "period": "2025/12",
            "frequency": "annual",
            "basis": "CFS",
            "estimate": False,
            "value": 42,
            "unit": "원",
            "formula": None,
            "source_trace": {"period": "2025/12", "unit": "원"},
        }
    ]


def test_brokers_missing_rank_is_not_zero():
    rows = parse_members(
        {
            "shnu_mbcr_name1": "A",
            "total_shnu_qty1": "100",
            "seln_mbcr_name1": "B",
            "total_seln_qty1": "50",
        }
    )
    assert rows == [{"name": "A", "buy": 100, "sell": None}, {"name": "B", "buy": None, "sell": 50}]








@pytest.mark.asyncio
async def test_integration_volume_value_preserves_previous_when_upstream_is_missing(
    store, monkeypatch
):
    provider = PublicProvider(store)
    old_flow_trace = trace(
        "test_fixture", "https://example.invalid/flows", "c" * 64, "2026-09-23", "주"
    )
    store.put(
        "detail:278990",
        {
            "code": "278990",
            "facts": {
                "value": {"value": 125000, "unit": "원"},
                "per": {"value": 8.2, "unit": "배"},
            },
            "flows": [
                {
                    "date": "2026-09-23",
                    "foreign": 12,
                    "institution": 5,
                    "individual": -17,
                    "holding": 42,
                    "source_trace": old_flow_trace,
                }
            ],
            "research": [{"title": "기존 공개 리서치"}],
        },
    )
    payload = {
        "itemCode": "278990",
        "totalInfos": [
            {"code": "accumulatedTradingVolume", "value": "23"},
            {"code": "accumulatedTradingValue", "value": "-"},
            {"code": "per", "value": "N/A", "valueDesc": "2025.12."},
        ],
        "dealTrendInfos": None,
        "researches": None,
    }

    async def fetch(*args):
        return httpx.Response(200, json=payload), "b" * 64

    monkeypatch.setattr(provider, "fetch", fetch)
    try:
        detail = await provider.detail("278990", include_fundamentals=False)
        assert detail["facts"]["volume"]["value"] == 23
        assert detail["facts"]["volume"]["unit"] == "주"
        assert detail["facts"]["value"]["value"] == 125000
        assert detail["facts"]["per"]["value"] == 8.2
        assert detail["flows"][0]["foreign"] == 12
        assert detail["source_status"]["naver_flows"]["status"] == "no_data"
        assert "flows" not in detail["source_status"]
        assert detail["research"][0]["title"] == "기존 공개 리서치"
        assert detail["source_status"]["research"]["status"] == "stale"
    finally:
        await provider.client.aclose()


@pytest.mark.asyncio
async def test_kind_listing_deduplicates_company_identity(store, monkeypatch):
    rows = [
        f"<tr><td>회사{i}</td><td>코넥스</td><td>{i:06d}</td><td>제조업</td><td>제품</td><td>2020-01-01</td><td>12월</td></tr>"
        for i in range(100)
    ]
    content = ("<table>" + "".join(rows + rows[:6]) + "</table>").encode("euc-kr")
    provider = PublicProvider(store)

    async def fetch(*args):
        return httpx.Response(200, content=content), "a" * 64

    monkeypatch.setattr(provider, "fetch", fetch)
    try:
        listings = await provider.listings()
        assert len(listings) == 100
        assert store.get("issuers")["source_row_count"] == 106
        assert len({row["code"] for row in listings}) == 100
    finally:
        await provider.client.aclose()


def test_raw_append_only(store):
    digest = store.raw("test", "https://example.invalid/a", b"v1")
    store.raw("test", "https://example.invalid/a", b"v2")
    assert (store.root / "raw/test" / (digest + ".json")).exists()
    assert len(list((store.root / "raw/test").glob("*.json"))) == 2


@pytest.mark.asyncio
async def test_opendart_major_account_backfill_resumes_and_preserves_full_statements(
    store, monkeypatch
):
    provider = PublicProvider(store)
    source = AuthenticatedSources(provider)
    source.dart = "test-secret-do-not-persist"
    codes = {
        "005930": "00126380",
        "000660": "00164779",
        "035420": "00356361",
    }
    store.put("dart-corporations", {"date": now()[:10], "codes": codes})
    store.put(
        "official:005930",
        {
            "company": {"corp_name": "테스트 회사"},
            "financials_received_at": now(),
            "statements": [
                {
                    "name": "현금및현금성자산",
                    "account_id": "ifrs-full_CashAndCashEquivalents",
                    "statement": "BS",
                    "basis": "CFS",
                    "period": "2025",
                    "current": 123,
                    "previous": 100,
                    "prior": 90,
                    "unit": "KRW",
                    "receipt": "old-receipt",
                }
            ],
        },
    )
    monkeypatch.setenv("DART_MAJOR_ACCOUNT_BATCH_SIZE", "2")
    requests = []

    async def fake_dart_get(url, *, params):
        requested = params["corp_code"].split(",")
        assert len(requested) <= 100
        assert params["bsns_year"] == "2025"
        assert params["reprt_code"] == "11011"
        requests.append((url, requested))
        if "00356361" in requested:
            return httpx.Response(
                200,
                request=httpx.Request("GET", url),
                json={"status": "013", "list": []},
            )
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={
                "status": "000",
                "list": [
                    {
                        "stock_code": "005930",
                        "account_nm": "현금및현금성자산",
                        "sj_div": "BS",
                        "fs_div": "CFS",
                        "bsns_year": "2025",
                        "thstrm_amount": "200",
                        "frmtrm_amount": "180",
                        "bfefrmtrm_amount": "150",
                        "currency": "KRW",
                        "rcept_no": "new-receipt",
                    },
                    {
                        "stock_code": "005930",
                        "account_nm": "유동자산",
                        "sj_div": "BS",
                        "fs_div": "CFS",
                        "bsns_year": "2025",
                        "thstrm_amount": "247,684",
                        "frmtrm_amount": "227,062",
                        "bfefrmtrm_amount": "195,936",
                        "currency": "KRW",
                        "rcept_no": "new-receipt",
                    },
                    {
                        "stock_code": "000660",
                        "account_nm": "매출액",
                        "sj_div": "IS",
                        "fs_div": "CFS",
                        "bsns_year": "2025",
                        "thstrm_amount": "66,193",
                        "frmtrm_amount": "66,193",
                        "bfefrmtrm_amount": "66,193",
                        "currency": "KRW",
                        "rcept_no": "another-receipt",
                    },
                ],
            },
        )

    monkeypatch.setattr(source, "_dart_get", fake_dart_get)
    try:
        first = await source.major_accounts_batch(list(codes), year=2025)
        second = await source.major_accounts_batch(list(codes), year=2025)
        cached = await source.major_accounts_batch(list(codes), year=2025)

        assert first["status"] == "ready" and first["processed"] == 2, store.get(
            "dart-major-accounts-progress"
        ).get("last_error")
        assert second["status"] == "complete" and second["codes"]["035420"] == "no_data"
        assert cached["status"] == "current" and len(requests) == 2

        samsung = store.get("official:005930")
        cash = next(row for row in samsung["statements"] if row["name"] == "현금및현금성자산")
        current_assets = next(row for row in samsung["statements"] if row["name"] == "유동자산")
        assert cash["current"] == 123  # Never downgrade a full single-company statement.
        assert current_assets["current"] == 247684
        assert current_assets["previous"] == 227062
        assert current_assets["source_trace"]["source"] == "opendart"
        assert current_assets["source_trace"]["available_at"] is None
        assert source.dart not in json.dumps(samsung, ensure_ascii=False)

        hynix = store.get("official:000660")
        assert hynix["statements"][0]["basis"] == "CFS"
        assert hynix["statements"][0]["receipt"] == "another-receipt"
        archived = list((store.root / "raw/dart").glob("*.json"))
        assert len(archived) == 2
        assert all(source.dart not in path.read_text(encoding="utf-8") for path in archived)
    finally:
        await provider.client.aclose()


def test_opendart_report_sections_normalize_investor_relevant_fields():
    shareholder = normalize_report_section(
        "shareholders",
        [
            {
                "nm": "최대주주",
                "relate": "본인",
                "stock_knd": "보통주",
                "bsis_posesn_stock_co": "1,000",
                "bsis_posesn_stock_qota_rt": "10.0",
                "trmend_posesn_stock_co": "1,200",
                "trmend_posesn_stock_qota_rt": "12.0",
                "stlm_dt": "2025-12-31",
            }
        ],
    )
    assert shareholder == [
        {
            "name": "최대주주",
            "relationship": "본인",
            "stock_type": "보통주",
            "beginning_shares": 1000,
            "beginning_ratio": 10,
            "ending_shares": 1200,
            "ending_ratio": 12,
            "as_of": "2025-12-31",
        }
    ]
    shares = normalize_report_section(
        "shares",
        [{"se": "보통주", "istc_totqy": "1,500", "tesstk_co": "100", "distb_stock_co": "1,400"}],
    )
    assert shares[0]["issued"] == 1500
    assert shares[0]["treasury"] == 100
    assert shares[0]["circulating"] == 1400
    assert normalize_report_section("employees", [{"sm": "-"}])[0]["total"] is None

    minority = normalize_report_section(
        "minority_shareholders",
        [
            {
                "shrholdr_co": "100",
                "shrholdr_tot_co": "120",
                "shrholdr_rate": "83.3",
                "hold_stock_co": "900",
                "stock_tot_co": "1000",
                "hold_stock_rate": "90",
            }
        ],
    )[0]
    assert minority["holders"] == 100 and minority["share_ratio"] == 90
    holder_change = normalize_report_section(
        "holder_changes",
        [
            {
                "change_on": "2025.03.01",
                "mxmm_shrholdr_nm": "주주",
                "posesn_stock_co": "200",
                "qota_rt": "20",
                "change_cause": "변동",
            }
        ],
    )[0]
    assert holder_change["shares"] == 200 and holder_change["cause"] == "변동"
    auditor = normalize_report_section(
        "auditor",
        [
            {
                "adtor": "감사법인",
                "adt_opinion": "적정",
                "emphs_matter": "강조",
                "core_adt_matter": "핵심",
            }
        ],
    )[0]
    assert auditor["opinion"] == "적정" and auditor["key_audit_matters"] == "핵심"
    treasury = normalize_report_section(
        "treasury",
        [
            {
                "stock_knd": "보통주",
                "bsis_qy": "100",
                "change_qy_acqs": "20",
                "change_qy_dsps": "10",
                "change_qy_incnr": "5",
                "trmend_qy": "105",
            }
        ],
    )[0]
    assert treasury["acquired"] == 20 and treasury["ending"] == 105
    investment = normalize_report_section(
        "investments",
        [
            {
                "inv_prm": "자회사",
                "trmend_blce_qy": "300",
                "trmend_blce_qota_rt": "30",
                "trmend_blce_acntbk_amount": "5000",
                "recent_bsns_year_fnnr_sttus_thstrm_ntpf": "200",
            }
        ],
    )[0]
    assert investment["company"] == "자회사" and investment["ending_ratio"] == 30


@pytest.mark.asyncio
async def test_opendart_company_report_sections_rotate_and_resume(store, monkeypatch):
    provider = PublicProvider(store)
    source = AuthenticatedSources(provider)
    source.dart = "test-secret-do-not-persist"
    store.put("dart-corporations", {"date": now()[:10], "codes": {"005930": "00126380"}})
    payloads = {
        "hyslrSttus.json": {
            "status": "000",
            "list": [
                {
                    "nm": "주요 주주",
                    "trmend_posesn_stock_co": "500",
                    "trmend_posesn_stock_qota_rt": "5",
                }
            ],
        },
        "alotMatter.json": {
            "status": "000",
            "list": [{"se": "현금배당", "thstrm": "1,500", "frmtrm": "1,200"}],
        },
        "stockTotqySttus.json": {
            "status": "000",
            "list": [
                {
                    "se": "보통주",
                    "istc_totqy": "1,000",
                    "tesstk_co": "100",
                    "distb_stock_co": "900",
                }
            ],
        },
        "empSttus.json": {"status": "013", "list": []},
        "exctvSttus.json": {"status": "000", "list": [{"nm": "대표이사", "ofcps": "CEO"}]},
        "mrhlSttus.json": {
            "status": "000",
            "list": [{"shrholdr_co": "100", "hold_stock_rate": "80"}],
        },
        "hyslrChgSttus.json": {
            "status": "000",
            "list": [{"mxmm_shrholdr_nm": "최대주주", "posesn_stock_co": "500"}],
        },
        "accnutAdtorNmNdAdtOpinion.json": {
            "status": "000",
            "list": [{"adtor": "감사법인", "adt_opinion": "적정"}],
        },
        "tesstkAcqsDspsSttus.json": {
            "status": "000",
            "list": [{"stock_knd": "보통주", "trmend_qy": "100"}],
        },
        "otrCprInvstmntSttus.json": {
            "status": "000",
            "list": [{"inv_prm": "자회사", "trmend_blce_qy": "200"}],
        },
    }
    calls = []

    async def fake_dart_get(url, *, params):
        assert params["crtfc_key"] == source.dart
        assert params["corp_code"] == "00126380"
        assert params["bsns_year"] == str(datetime.now(ZoneInfo("Asia/Seoul")).year - 1)
        assert params["reprt_code"] == "11011"
        calls.append(url.rsplit("/", 1)[-1])
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json=payloads[url.rsplit("/", 1)[-1]],
        )

    monkeypatch.setattr(source, "_dart_get", fake_dart_get)
    try:
        results = [await source.periodic_report_facts("005930") for _ in range(10)]
        current = await source.periodic_report_facts("005930")

        assert calls == [
            "hyslrSttus.json",
            "alotMatter.json",
            "stockTotqySttus.json",
            "empSttus.json",
            "exctvSttus.json",
            "mrhlSttus.json",
            "hyslrChgSttus.json",
            "accnutAdtorNmNdAdtOpinion.json",
            "tesstkAcqsDspsSttus.json",
            "otrCprInvstmntSttus.json",
        ]
        assert [result["section"] for result in results] == [
            "shareholders",
            "dividends",
            "shares",
            "employees",
            "executives",
            "minority_shareholders",
            "holder_changes",
            "auditor",
            "treasury",
            "investments",
        ]
        assert current == {"status": "current", "section": None}

        official = store.get("official:005930")
        sections = official["report_facts"]["sections"]
        assert sections["shareholders"]["rows"][0]["ending_ratio"] == 5
        assert sections["dividends"]["rows"][0]["current"] == 1500
        assert sections["shares"]["rows"][0]["circulating"] == 900
        assert sections["employees"]["status"] == "no_data"
        assert sections["executives"]["rows"][0]["name"] == "대표이사"
        assert sections["minority_shareholders"]["rows"][0]["holders"] == 100
        assert sections["holder_changes"]["rows"][0]["name"] == "최대주주"
        assert sections["auditor"]["rows"][0]["opinion"] == "적정"
        assert sections["treasury"]["rows"][0]["ending"] == 100
        assert sections["investments"]["rows"][0]["company"] == "자회사"
        assert source.dart not in json.dumps(official, ensure_ascii=False)
        archived = list((store.root / "raw/dart").glob("*.json"))
        assert len(archived) == 10
        assert all(source.dart not in path.read_text(encoding="utf-8") for path in archived)
    finally:
        await provider.client.aclose()


@pytest.mark.asyncio
async def test_opendart_report_budget_pauses_all_company_sections_until_next_day(
    store, monkeypatch
):
    provider = PublicProvider(store)
    source = AuthenticatedSources(provider)
    source.dart = "test-secret-do-not-persist"
    # The production corporation-cache day and budget reset both use KST.
    store.put("dart-corporations", {
        "date": datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat(),
        "codes": {"005930": "00126380"},
    })
    requests = []

    async def budget_exceeded(url, *, params):
        requests.append(url)
        raise DartDailyBudgetExceeded("test budget")

    monkeypatch.setattr(source, "_dart_get", budget_exceeded)
    try:
        first = await source.periodic_report_facts("005930")
        second = await source.periodic_report_facts("000660")

        assert first == {"status": "rate_limited", "section": "shareholders"}
        assert second == {"status": "rate_limited", "section": None}
        assert len(requests) == 1
        assert store.get("dart-report-facts-retry-after")["retry_after"]
    finally:
        await provider.client.aclose()


def test_api_live_snapshot_order_without_accounts(store, monkeypatch):
    app.dependency_overrides.clear()
    monkeypatch.setattr("services.market_dashboard.app.get_store", lambda: store)
    store.put("universe", {"rows": [security()], "coverage": []})
    store.put(
        "quotes",
        {
            "005930": {
                "price": 9000,
                "change": -1,
                "as_of": "2026-09-22T15:30:00+09:00",
                "quote_trace": {},
            }
        },
    )
    client = TestClient(app)
    assert client.post(BASE + "/screener", json={}).json()["rows"][0]["price"] == 10000
    assert client.get(BASE + "/securities/000000/prices").status_code == 404
    assert client.get(BASE + "/me").status_code == 404
    assert client.put(BASE + "/me", json={"revision": 0, "state": {}}).status_code == 404
    assert client.post(BASE + "/account", json={}).status_code == 404
    plain = client.get(BASE + "/search?q=테스트", headers={"Accept-Encoding": "identity"})
    assert plain.status_code == 200 and "content-encoding" not in plain.headers
    search_payload = plain.json()
    assert search_payload["total"] == 1
    search_row = search_payload["rows"][0]
    assert {"code", "name", "market", "price", "change", "source_trace", "facts"} <= set(
        search_row
    )
    assert search_row["facts"]["price"]["source_trace"]
    assert search_row["facts"]["change"]["source_trace"]
    assert "per" not in search_row and "financials" not in search_row
    assert set(search_row["facts"]) <= {"price", "change"}
    assert client.get(BASE + "/search?q=%20%ED%85%8C%EC%8A%A4%ED%8A%B8%20").json() == search_payload
    with TestClient(app) as stream_client, stream_client.websocket_connect(BASE + "/stream") as ws:
        ws.send_json({"codes": ["005930"]})
        event = ws.receive_json()
        assert event["type"] == "snapshot" and len(event["rows"]) == 1
        assert event["rows"][0]["price"] == 10000
    get_store.cache_clear()


def test_parquet_audit(store):
    import duckdb

    store.archive("prices", [{"date": "2026-09-23", "close": 10000}])
    path = str(next((store.root / "parquet/prices").glob("*.parquet")))
    assert (
        duckdb.connect().execute("select close from read_parquet(?)", [path]).fetchone()[0] == 10000
    )


def test_chart_points_are_ordered_and_deduplicated():
    from services.market_dashboard.visuals import parse_chart

    points = parse_chart(
        {
            "priceInfos": {
                "20260923": [
                    {"localDateTime": "20260923100000", "currentPrice": 110},
                    {"localDateTime": "20260923090000", "currentPrice": 100},
                    {"localDateTime": "20260923100000", "currentPrice": 111},
                    {"localDateTime": "20260923110000", "currentPrice": None},
                ]
            }
        }
    )
    assert points == [
        {"time": "20260923090000", "value": 100.0},
        {"time": "20260923100000", "value": 111.0},
    ]
