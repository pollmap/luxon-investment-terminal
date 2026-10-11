"""fixture_non_production: synthetic response dates and amounts, never production evidence."""

import json
from calendar import monthrange
from types import SimpleNamespace

import pytest

from services.market_dashboard.dart_periods import DartPeriodCollector
from services.market_dashboard.store import Store
from services.market_dashboard.ttm_backfill import backfill, checkpoint_for


@pytest.fixture
def source(tmp_path):
    store = Store(url="sqlite://", root=tmp_path)
    store.put("universe", {"rows": [{"code": "005930"}, {"code": "000660"}]})

    async def corporations():
        return {"005930": "00126380", "000660": "00164779"}

    return SimpleNamespace(
        store=store, dart="fixture-secret", corporations=corporations, _age=lambda _: 0
    )


@pytest.mark.asyncio
async def test_audit_does_not_request_source_and_annual_is_not_ttm(source):
    async def forbidden():
        raise AssertionError("audit must not request a provider")

    source.corporations = forbidden
    source.store.put(
        "official:005930",
        {
            "period_facts": [
                {
                    "period_start": "2025-01-01",
                    "period_end": "2025-12-31",
                    "period_verified": True,
                    "kind": "duration",
                    "account_id": "IS:매출액",
                    "basis": "CFS",
                    "unit": "원",
                    "currency": "KRW",
                    "value": 100,
                    "cumulative": True,
                    "source_trace": {"source": "fixture_non_production"},
                }
            ]
        },
    )
    report = await backfill(source, ["005930"])
    assert not report["complete"] and not report["account_ttm_ready_count"]
    assert report["after"]["005930"]["verified_duration_inputs"] == 1
    assert report["collection_attempts"] == []
    assert source.store.get(checkpoint_for(["005930"])) is None


@pytest.mark.asyncio
async def test_identity_failure_is_retryable_and_preserves_market_cursor(source):
    original = {"task": 4, "offset": 100, "status": "collecting"}
    source.store.put("dart-period-progress", original)
    calls = 0

    async def blocked():
        nonlocal calls
        calls += 1
        raise ConnectionError("fixture-secret must not appear in reports")

    source.corporations = blocked
    first = await backfill(source, ["005930"], collect=True, pause_seconds=0)
    assert first["collection_attempts"][0]["stage"] == "corporation_identity"
    assert first["collection_state"]["error"] == "ConnectionError"
    assert not first["complete"] and "fixture-secret" not in json.dumps(first)
    second = await backfill(source, ["005930"], collect=True, pause_seconds=0)
    assert second["collection_attempts"][0]["status"] == "retry_wait"
    assert calls == 1
    assert source.store.get("dart-period-progress") == original


@pytest.mark.asyncio
async def test_priority_checkpoint_is_stable_and_bounded(source):
    assert checkpoint_for(["005930", "000660", "005930"]) == checkpoint_for(["000660", "005930"])
    with pytest.raises(ValueError, match="Unknown"):
        await backfill(source, ["999999"], collect=True)
    with pytest.raises(ValueError, match="budget"):
        await backfill(source, ["005930"], max_batches=65)
    with pytest.raises(ValueError, match="checkpoint"):
        await DartPeriodCollector(source).collect(["005930"], checkpoint_key="universe")


@pytest.mark.asyncio
async def test_collected_periods_reach_monetary_ttm_without_claiming_eps(source):
    async def get(url, params):
        year = int(params["bsns_year"])
        month, total = {"11013": (3, 10), "11012": (6, 30), "11014": (9, 60), "11011": (12, 100)}[
            params["reprt_code"]
        ]
        row = {
            "stock_code": "005930",
            "account_nm": "매출액",
            "sj_div": "IS",
            "fs_div": "CFS",
            "currency": "KRW",
            "thstrm_dt": f"{year}.01.01 ~ {year}.{month:02}.{monthrange(year, month)[1]:02}",
            "thstrm_amount": str(total),
            "thstrm_add_amount": str(total),
            "rcept_no": f"{year + 1}0101000001",
        }
        payload = {"status": "000", "list": [row]}
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: payload,
            content=json.dumps(payload).encode(),
        )

    source._dart_get = get
    report = await backfill(source, ["005930"], collect=True, max_batches=64, pause_seconds=0)
    assert report["complete"] and report["account_ttm_ready_count"] == 1
    result = report["after"]["005930"]
    assert result["by_basis"]["CFS"]["rows"][0]["value"] == 100
    assert result["by_basis"]["OFS"]["accounts"] == 0
    assert result["per_share_ttm_status"] == "not_assessed"
    assert source.store.get("dart-period-progress") is None
