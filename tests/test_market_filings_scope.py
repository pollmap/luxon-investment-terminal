"""Synthetic DART fixtures test listing scope and last-good preservation."""
import asyncio
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from services.market_dashboard.authenticated_sources import AuthenticatedSources, DartDailyBudgetExceeded
from services.market_dashboard.store import Store


def filing(serial, code="005930", corp_cls="Y", date="20261008"):
    return {"rcept_no": f"{date}{serial:06d}", "rcept_dt": date,
            "stock_code": code, "corp_code": "00126380", "corp_cls": corp_cls,
            "corp_name": "검증용 회사", "report_nm": f"공시 {serial}"}


def feed(rows):
    return {"status": "000", "list": rows, "total_count": len(rows)}


def setup_source(tmp_path, responses):
    store = Store("sqlite://", tmp_path)
    store.put("universe", {"rows": [
        {"code": "005930", "name": "검증용 회사", "market": "KOSPI"},
        {"code": "091990", "name": "코스닥 회사", "market": "KOSDAQ"},
        {"code": "999999", "name": "코넥스 회사", "market": "KONEX"},
    ]})
    calls, archived = [], []

    class Client:
        async def get(self, url, params):
            calls.append(dict(params))
            status, body = responses[params["corp_cls"]]
            content = body if isinstance(body, bytes) else json.dumps(body).encode()
            return httpx.Response(status, content=content, request=httpx.Request("GET", url))

    save_raw = store.raw

    def record_raw(source, url, content):
        digest = save_raw(source, url, content)
        archived.append({"source": source, "url": url, "content": content, "digest": digest})
        return digest

    store.raw = record_raw
    source = AuthenticatedSources(SimpleNamespace(store=store, client=Client()))
    source.dart = "fixture_non_production"
    return source, store, calls, archived


def test_two_market_queries_merge_sort_deduplicate_then_limit(tmp_path):
    y_rows = [filing(n) for n in range(1, 101)]
    k_rows = [filing(n, "091990", "K") for n in range(101, 200)] + [filing(100)]
    source, store, calls, archived = setup_source(tmp_path, {
        "Y": (200, feed(y_rows)), "K": (200, feed(k_rows)),
    })
    result = asyncio.run(source.market_filings())
    assert [call["corp_cls"] for call in calls] == ["Y", "K"]
    assert all(call["page_count"] == "100" and call["last_reprt_at"] == "N" for call in calls)
    assert len(result["rows"]) == result["displayed_count"] == 100
    assert [row["receipt"] for row in result["rows"]] == [f"20261008{n:06d}" for n in range(199, 99, -1)]
    assert result["total"] == 200  # Provider total, distinct from displayed unique rows.
    assert len(archived) == 3  # Two response bodies and the merge manifest.
    assert all("fixture_non_production" not in raw["url"] for raw in archived)
    for row in result["rows"]:
        expected = archived[0 if row["code"] == "005930" else 1]
        assert row["source_trace"]["source_document_id"] == hashlib.sha256(expected["content"]).hexdigest()
    manifest = json.loads(archived[-1]["content"])
    assert [item["source_hash"] for item in manifest["sources"]] == [raw["digest"] for raw in archived[:2]]
    assert store.get("market-filings") == result


def test_eligible_issuers_are_filtered_before_top_100(tmp_path):
    source, _, _, _ = setup_source(tmp_path, {
        "Y": (200, feed([filing(1), filing(900, "111111"), filing(800, "999999", "N")])),
        "K": (200, feed([filing(2, "091990", "K")])),
    })
    result = asyncio.run(source.market_filings())
    assert [row["code"] for row in result["rows"]] == ["091990", "005930"]
    assert result["displayed_count"] == 2
    assert result["status"] == "ready"


@pytest.mark.parametrize("k_payload, expected_count", [(feed([filing(2, "091990", "K")]), 1), ({"status": "013"}, 0)])
def test_no_data_is_a_valid_market_result(tmp_path, k_payload, expected_count):
    source, _, calls, archived = setup_source(tmp_path, {
        "Y": (200, {"status": "013"}), "K": (200, k_payload),
    })
    result = asyncio.run(source.market_filings())
    assert len(calls) == 2 and len(archived) == 3
    assert len(result["rows"]) == expected_count
    assert result["status"] == ("ready" if expected_count else "no_data")


@pytest.mark.parametrize("status, body, error", [
    (200, {"status": "010"}, ValueError),
    (200, {"status": "020"}, DartDailyBudgetExceeded),
    (500, {"status": "900"}, httpx.HTTPStatusError),
    (200, b"not-json", ValueError),
    (200, {"status": "000", "list": None}, ValueError),
    (200, feed([{**filing(2), "rcept_no": "bad-receipt"}]), ValueError),
])
def test_failed_second_market_keeps_previous_complete_snapshot_and_raw(tmp_path, status, body, error):
    source, store, calls, archived = setup_source(tmp_path, {
        "Y": (200, feed([filing(1)])), "K": (status, body),
    })
    previous = {"status": "ready", "rows": [{"receipt": "previous-good"}], "received_at": "2026-10-07"}
    store.put("market-filings", previous)
    with pytest.raises(error):
        asyncio.run(source.market_filings())
    assert len(calls) == len(archived) == 2
    assert store.get("market-filings") == previous
    assert all("fixture_non_production" not in row["url"] for row in archived)
