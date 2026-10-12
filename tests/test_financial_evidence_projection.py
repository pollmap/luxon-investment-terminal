"""fixture_non_production: company identity and retained disclosure contexts."""

import asyncio
import hashlib
import json
from copy import deepcopy
from datetime import date
from types import SimpleNamespace

from services.market_dashboard.dart_periods import DartPeriodCollector
from services.market_dashboard.fact_store import FactStore
from services.market_dashboard.financial_questions import build_financial_questions
from services.market_dashboard.financial_summary import ttm_summary
from services.market_dashboard.store import Store

NS = "{https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full}"
PERIODS = [
    ("2025-01-01", "2025-03-31", "2025-05-15"),
    ("2025-04-01", "2025-06-30", "2025-08-15"),
    ("2025-07-01", "2025-09-30", "2025-11-15"),
    ("2025-10-01", "2025-12-31", "2026-03-15"),
]


def facts(account=NS + "ProfitLoss", amount=10, verified=True):
    return [
        {
            "account_id": account,
            "company_id": "corp" if verified else "",
            "basis": "CFS",
            "currency": "KRW",
            "unit": "{iso}KRW",
            "kind": "duration",
            "period_verified": True,
            "period_start": start,
            "period_end": end,
            "value": amount,
            "published_at": published if verified else None,
            "dimensions": {},
            "fact_id": f"{account}:{index}",
            "source_trace": {
                "source": "fixture_non_production",
                "source_document_id": "ab" * 32,
                "available_at": None,
                "unit": "KRW",
                "quality_flags": ["publication_time_unverified"],
            },
        }
        for index, (start, end, published) in enumerate(PERIODS)
    ]


def test_summary_keeps_verified_xbrl_over_undated_legacy_and_does_not_mutate_raw():
    official = {
        "period_facts": facts("IS:당기순이익", 20, False),
        "xbrl_facts": facts() + facts(NS + "CashFlowsFromUsedInOperatingActivities", 15),
    }
    original = deepcopy(official)
    summary = ttm_summary(official)
    net = next(row for row in summary["rows"] if row["metric"] == "net_income")
    assert net["value"] == 40 and net["company_id"] == "corp"
    assert net["account_id"] == NS + "ProfitLoss"
    source = net["source_trace"]["inputs"][0]
    assert source["input_period_end"] == "2025-03-31"
    assert source["input_published_at"] == "2025-05-15"
    assert source["input_company_id"] == "corp"
    assert source["input_fact_ids"] == [NS + "ProfitLoss:0"]
    assert source["available_at"] is None
    assert source["quality_flags"] == ["publication_time_unverified"]
    result = build_financial_questions({"ttm": {"CFS": summary}})
    assert result["status"] == "ready"
    ratio = next(row for row in result["comparisons"] if row["metric"] == "cash_conversion")
    assert ratio["value"] == 150
    assert official == original


def test_undated_legacy_remains_reported_but_is_not_promoted_by_receipt_digits():
    rows = facts("IS:당기순이익", 20, False)
    for row in rows:
        row["filing_id"] = "20260315000001"
        row["published_at"] = "20260315000001"
    summary = ttm_summary({"period_facts": rows})
    assert summary["rows"][0]["value"] == 80
    assert all(
        "input_published_at" not in trace for trace in summary["rows"][0]["source_trace"]["inputs"]
    )
    result = build_financial_questions({"ttm": {"CFS": summary}})
    assert result["status"] == "not_available"


def test_wrong_company_period_evidence_is_not_ratio_input():
    summary = ttm_summary(
        {"xbrl_facts": facts() + facts(NS + "CashFlowsFromUsedInOperatingActivities")}
    )
    summary["rows"][0]["source_trace"]["inputs"][1]["input_company_id"] = "other"
    result = build_financial_questions({"ttm": {"CFS": summary}}, company_id="corp")
    assert all(row["value"] is None for row in result["comparisons"])


def test_dated_collector_retains_same_corporation_in_fact_store_and_snapshot(tmp_path):
    store = Store("sqlite://", tmp_path)
    payload = {
        "status": "000",
        "list": [
            {
                "stock_code": "005930",
                "account_nm": "매출액",
                "sj_div": "IS",
                "fs_div": "CFS",
                "thstrm_dt": "2025.01.01 ~ 2025.03.31",
                "thstrm_amount": "10",
                "rcept_no": "20260515000001",
                "currency": "KRW",
            }
        ],
    }
    content = json.dumps(payload).encode()

    async def corporations():
        return {"005930": "00126380"}

    async def get(*args, **kwargs):
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload, content=content)

    source = SimpleNamespace(
        dart="fixture_non_production",
        store=store,
        corporations=corporations,
        _dart_get=get,
        _age=lambda _: 0,
    )
    result = asyncio.run(DartPeriodCollector(source).collect(["005930"], today=date(2026, 9, 27)))
    assert result["facts"] == 1
    snapshot = store.get("official:005930")["period_facts"][0]
    normalized = FactStore(store).read("00126380")[0]
    assert snapshot["company_id"] == normalized["company_id"] == "00126380"
    assert snapshot["company_id"] != "005930"
    assert snapshot["source_trace"]["source_document_id"] == hashlib.sha256(content).hexdigest()
    assert snapshot["published_at"] is None and snapshot["publication_date"] is None
    assert payload["list"][0].get("company_id") is None
