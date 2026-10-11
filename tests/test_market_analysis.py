from datetime import date
import pytest
from fastapi.testclient import TestClient
from services.market_dashboard.analysis import valuation_history, scenario, trailing_twelve_months
from services.market_dashboard.models import matches_security
from services.market_dashboard.app import app


def fact(start,end,value,**extra):
    return dict(account_id="Revenue",basis="CFS",unit="원",currency="KRW",kind="duration",period_verified=True,
                period_start=start,period_end=end,fiscal_start=start[:4]+"-01-01",value=value,source_trace={"id":end},**extra)


def test_initials_aliases_and_preferred_identity():
    row={"name":"삼성전자우","code":"005935","aliases":["Samsung Electronics Preferred"]}
    assert matches_security(row,"ㅅㅅㅈㅈㅇ")
    assert matches_security(row,"samsung")
    assert matches_security(row,"삼성 전자")
    assert not matches_security(row,"005930")


def test_scenario_is_explicit_and_validated():
    assert scenario(base=100,growth=10,multiple=15,years=2)["rows"][-1]["reference_price"] == 1815
    with pytest.raises(ValueError): scenario(base=0,growth=0,multiple=15,years=3)
    client=TestClient(app)
    assert client.post("/api/market/v1/analysis/scenario",json={"base":100,"growth":0,"multiple":15,"years":3}).status_code==200
    assert client.post("/api/market/v1/analysis/scenario",json={"base":100,"growth":-100,"multiple":15,"years":3}).status_code==422


def test_ttm_standalone_and_cumulative():
    rows=[fact("2025-01-01","2025-03-31",10,cumulative=True),fact("2025-01-01","2025-06-30",30,cumulative=True),fact("2025-01-01","2025-09-30",60,cumulative=True),fact("2025-01-01","2025-12-31",100,cumulative=True)]
    result=trailing_twelve_months(rows)
    assert result["rows"][0]["value"]==100
    assert result["rows"][0]["period_start"]=="2025-01-01"
    assert trailing_twelve_months(rows[:3])["rows"]==[]
    assert trailing_twelve_months([{**f,"period_verified":False} for f in rows])["rows"]==[]
    assert trailing_twelve_months([{**f,"kind":"instant"} for f in rows])["rows"]==[]
    assert trailing_twelve_months([{**f,"kind":"ratio"} for f in rows])["rows"]==[]


def test_ttm_does_not_bridge_gap_or_mix_basis():
    rows=[fact("2025-01-01","2025-03-31",10),fact("2025-04-01","2025-06-30",20),fact("2025-07-01","2025-09-30",30),fact("2025-10-01","2025-12-31",40)]
    assert trailing_twelve_months(rows)["rows"][0]["value"]==100
    assert trailing_twelve_months([*rows[:3],{**rows[3],"basis":"OFS"}])["rows"]==[]
    assert trailing_twelve_months([*rows[:3],{**rows[3],"period_start":"2025-10-02"}])["rows"]==[]
    corrected={**rows[0],"value":15,"published_at":"2026-03-01"}
    assert trailing_twelve_months([*rows,corrected])["rows"][0]["value"]==105


def test_valuation_does_not_reuse_ancient_price_or_negative_eps():
    rows=[dict(name="EPS",basis="CFS",frequency="annual",period="2025/12",value=-5,source_trace={"id":"f"}),dict(name="EPS",basis="CFS",frequency="annual",period="2024/12",value=10,source_trace={"id":"g"})]
    out=valuation_history(rows,[{"date":"2024-12-30","close":100}])
    assert out["rows"][1]["price"] is None
    assert out["rows"][1]["reference_price"] is None
    assert out["rows"][0]["reference_price"]==150
    assert out["mean_multiple"]==10
    assert out["point_in_time"] is False
    assert valuation_history([{**rows[0],"source_trace":None}],[])["rows"]==[]
