import importlib
import json
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from services.market_dashboard.authenticated_sources import (
    AuthenticatedSources,
    DartDailyBudgetExceeded,
    dart_financial_task_plan,
)
from services.market_dashboard.store import Store

market_app = importlib.import_module("services.market_dashboard.app")


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.content = json.dumps(payload).encode()

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self):
        self.calls = []
        self.current_amount = "1000"

    async def get(self, url, *, params):
        self.calls.append((url, dict(params)))
        if url.endswith("fnlttSinglIndx.json"):
            return FakeResponse(
                {
                    "status": "000",
                    "list": [
                        {
                            "idx_nm": "영업이익률",
                            "idx_code": "M211000",
                            "idx_cl_nm": "수익성지표",
                            "idx_val": "0.256",
                            "stlm_dt": "2026-06-30",
                        }
                    ],
                }
            )
        return FakeResponse(
            {
                "status": "000",
                "list": [
                    {
                        "account_nm": "매출액",
                        "account_id": "ifrs-full_Revenue",
                        "sj_div": "IS",
                        "thstrm_nm": "제 58 기 반기",
                        "thstrm_amount": self.current_amount,
                        "thstrm_add_amount": "5000",
                        "frmtrm_amount": "800",
                        "frmtrm_add_amount": "4000",
                        "bfefrmtrm_amount": "700",
                        "currency": "KRW",
                        "rcept_no": "20260925000001",
                        "ord": "1",
                    }
                ],
            }
        )


@pytest.fixture
def source(tmp_path):
    store = Store(url="sqlite://", root=tmp_path)
    client = FakeClient()
    result = AuthenticatedSources(SimpleNamespace(client=client, store=store))
    result.dart = "secret-test-key"
    return result, store, client


def test_task_plan_covers_both_bases_recent_periods_and_dart_ratios():
    tasks = dart_financial_task_plan(date(2026, 9, 25))

    assert tasks[0]["query_key"] == "statement:2026:11012:CFS"
    assert tasks[1]["query_key"] == "statement:2026:11012:OFS"
    assert any(t["query_key"] == "indicator:2023:11014:M210000" for t in tasks)
    assert any(t["query_key"] == "statement:2024:11011:CFS" for t in tasks)
    assert any(t["query_key"] == "statement:2023:11014:CFS" for t in tasks)
    assert not any(t["query_key"] == "statement:2026:11014:CFS" for t in tasks)


@pytest.mark.asyncio
async def test_backfill_persists_cfs_and_ofs_without_overwriting_and_then_ratios(source):
    authenticated, store, client = source

    first = await authenticated.refresh_financial_data(
        "005930", "00126380", max_tasks=2
    )
    assert first == {"status": "ready", "processed": 2}
    official = store.get("official:005930")
    assert set(official["financial_reports"]) == {"2026:11012:CFS", "2026:11012:OFS"}
    cfs = store.get(official["financial_reports"]["2026:11012:CFS"]["snapshot_key"])
    ofs = store.get(official["financial_reports"]["2026:11012:OFS"]["snapshot_key"])
    assert cfs["rows"][0]["current_cumulative"] == 5000
    assert ofs["rows"][0]["basis"] == "OFS"

    second = await authenticated.refresh_financial_data(
        "005930", "00126380", max_tasks=1
    )
    assert second == {"status": "ready", "processed": 1}
    official = store.get("official:005930")
    assert len(official["financial_reports"]) == 2
    ratio_key = official["financial_indicators"]["2026:11012:M210000"]["snapshot_key"]
    ratio_snapshot = store.get(ratio_key)
    ratio = ratio_snapshot["rows"][0]
    assert ratio["name"] == "영업이익률"
    assert ratio["value"] == 0.256
    assert ratio["unit"] is None

    serialized = __import__("json").dumps(official, ensure_ascii=False)
    assert authenticated.dart not in serialized
    assert all(call[1]["crtfc_key"] == authenticated.dart for call in client.calls)
    assert authenticated.dart not in cfs["rows"][0]["source_trace"]["source_url"]


@pytest.mark.asyncio
async def test_budget_stop_keeps_existing_financial_reports(source):
    authenticated, store, _ = source
    existing = {
        "financial_reports": {"2026:11012:CFS": {"rows": [{"current": 10}], "status": "ready"}},
        "financial_indicators": {},
    }
    store.put("official:005930", existing)

    async def budget_exhausted(url, *, params):
        raise DartDailyBudgetExceeded()

    authenticated._dart_get = budget_exhausted
    result = await authenticated.refresh_financial_data("005930", "00126380", max_tasks=2)

    assert result == {"status": "rate_limited", "processed": 0}
    assert store.get("official:005930")["financial_reports"] == existing["financial_reports"]


def test_financial_api_returns_small_index_and_only_requested_report(source, monkeypatch):
    _, store, _ = source
    query_key = "statement:2026:11012:CFS"
    snapshot_key = f"financial-report:005930:{query_key}:{'a' * 64}"
    store.put(
        "official:005930",
        {
            "financial_reports": {
                "2026:11012:CFS": {
                    "year": "2026",
                    "report_code": "11012",
                    "report_name": "반기",
                    "basis": "CFS",
                    "status": "ready",
                    "row_count": 1,
                    "received_at": "internal-only",
                    "source_hash": "a" * 64,
                    "snapshot_key": snapshot_key,
                }
            },
            "financial_query_progress": {query_key: {"checked_at": "internal-only"}},
        },
    )
    store.put(snapshot_key, {"status": "ready", "rows": [{"name": "매출액", "current": 42}]})
    store.put("universe", {"rows": [{"code": "005930", "name": "삼성전자", "market": "KOSPI"}]})
    monkeypatch.setattr(market_app, "get_store", lambda: store)
    def no_whole_market_read():
        raise AssertionError("A financial report must use the listed-code index")
    monkeypatch.setattr(market_app, "rows_with_details", no_whole_market_read)
    market_app._body_cache.clear()

    client = TestClient(market_app.app)
    index = client.get("/api/market/v1/securities/005930/financials").json()
    detail = client.get(
        "/api/market/v1/securities/005930/financials",
        params={"year": "2026", "report_code": "11012", "basis": "CFS"},
    ).json()

    assert index["reports"] == [
        {
            "year": "2026",
            "report_code": "11012",
            "report_name": "반기",
            "basis": "CFS",
            "status": "ready",
            "row_count": 1,
        }
    ]
    assert "financial_query_progress" not in json.dumps(index)
    assert detail == {"status": "ready", "rows": [{"name": "매출액", "current": 42}]}


@pytest.mark.asyncio
async def test_restatement_adds_a_version_and_keeps_the_old_snapshot(source):
    authenticated, store, client = source

    await authenticated.refresh_financial_data("005930", "00126380", max_tasks=1)
    official = store.get("official:005930")
    old = official["financial_reports"]["2026:11012:CFS"]
    old_snapshot = store.get(old["snapshot_key"])
    assert old_snapshot["rows"][0]["current"] == 1000

    client.current_amount = "1200"
    progress = official["financial_query_progress"]
    progress["statement:2026:11012:CFS"]["checked_at"] = "2000-01-01T00:00:00+00:00"
    store.put("official:005930", official)
    # Missing periods advance too; a recheck is reserved within five visits.
    for _ in range(4):
        await authenticated.refresh_financial_data("005930", "00126380", max_tasks=1)

    updated = store.get("official:005930")["financial_reports"]["2026:11012:CFS"]
    new_snapshot = store.get(updated["snapshot_key"])
    assert new_snapshot["rows"][0]["current"] == 1200
    assert old_snapshot["rows"][0]["current"] == 1000
    assert updated["prior_hashes"] == [old["source_hash"]]


@pytest.mark.asyncio
async def test_financial_response_keeps_facts_written_while_request_waited(source):
    authenticated, store, client = source
    original_get = client.get

    async def concurrent_get(url, *, params):
        current = store.get("official:005930", {})
        store.put(
            "official:005930", {**current, "period_facts": [{"evidence": "parallel-collector"}]}
        )
        return await original_get(url, params=params)

    client.get = concurrent_get
    await authenticated.refresh_financial_data("005930", "00126380", max_tasks=1)
    official = store.get("official:005930")
    assert official["period_facts"] == [{"evidence": "parallel-collector"}]
    assert official["financial_reports"]["2026:11012:CFS"]["status"] == "ready"


@pytest.mark.asyncio
async def test_selected_old_report_is_collected_before_background_tasks(source):
    authenticated, store, client = source
    store.put('financial-request:005930',{'query_key':'statement:2015:11011:CFS'})
    await authenticated.refresh_financial_data('005930','00126380',max_tasks=1)
    assert client.calls[0][1]['bsns_year']=='2015'
    assert store.get('official:005930')['financial_reports']['2015:11011:CFS']['status']=='ready'
