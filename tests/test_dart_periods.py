"""fixture_non_production: dates and amounts below are synthetic accounting cases."""
import json
from datetime import date
from types import SimpleNamespace
import pytest
from services.market_dashboard.dart_periods import normalize_period_accounts, DartPeriodCollector
from services.market_dashboard.analysis import trailing_twelve_months
from services.market_dashboard.store import Store


def row(start, end, amount, **extra):
    return {"stock_code":"005930", "account_nm":"매출액", "sj_div":"IS", "fs_div":"CFS",
            "thstrm_dt":f"{start} ~ {end}", "thstrm_amount":str(amount), "rcept_no":"20260515000001", "currency":"KRW", **extra}


def test_non_december_year_and_annual_residual():
    rows=[row("2024.04.01","2024.06.30",10),row("2024.07.01","2024.09.30",20),
          row("2024.10.01","2024.12.31",30),row("2024.04.01","2025.03.31",100)]
    facts=normalize_period_accounts(rows,"https://example.test/fixture","fixture")
    result=trailing_twelve_months(facts)["rows"][0]
    assert result["value"]==100 and result["period_start"]=="2024-04-01"
    assert result["period_end"]=="2025-03-31"
    assert not trailing_twelve_months(facts[1:])["rows"]


def test_dates_units_balance_sheet_and_cumulative_amount():
    rows=[row("2025.01.01","2025.06.30",20,thstrm_add_amount="30")]
    facts=normalize_period_accounts(rows,"fixture","fixture")
    assert facts[0]["value"]==30 and facts[0]["cumulative"]
    for extra in [{"thstrm_dt":"2025.06.30 현재"},{"sj_div":"BS"},{"currency":""},
                  {"thstrm_dt":"2025.02.30 ~ 2025.06.30"},{"rcept_no":""}]:
        assert normalize_period_accounts([{**rows[0],**extra}],"fixture","fixture")==[]


@pytest.mark.asyncio
async def test_checkpoint_retry_and_last_good_preserved(tmp_path):
    store=Store(url="sqlite://",root=tmp_path)
    calls=[]
    async def corporations(): return {"005930":"00126380"}
    async def get(url,params):
        calls.append(params)
        payload={"status":"000","list":[row("2025.01.01","2025.03.31",10)]}
        return SimpleNamespace(raise_for_status=lambda:None,json=lambda:payload,content=json.dumps(payload).encode())
    source=SimpleNamespace(dart="fixture-secret",store=store,corporations=corporations,_dart_get=get,_age=lambda _:0)
    collector=DartPeriodCollector(source)
    first=await collector.collect(["005930"],today=date(2026,9,27))
    assert first["facts"]==1
    before=store.get("official:005930")["period_facts"]
    assert store.get("dart-period-progress")["task"]==1
    async def failed(*args,**kwargs): raise RuntimeError("must never persist fixture-secret")
    source._dart_get=failed
    assert (await collector.collect(["005930"],today=date(2026,9,27)))["status"]=="error"
    assert (await collector.collect(["005930"],today=date(2026,9,27)))["status"]=="retry_wait"
    assert store.get("official:005930")["period_facts"]==before
    assert "fixture-secret" not in json.dumps(store.get("dart-period-progress"))
