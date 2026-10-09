"""fixture_non_production: deterministic accounting and market boundary cases."""
import io
import json
import zipfile
from datetime import date, timedelta
import pytest
from services.market_dashboard.bars import aggregate_bars
from services.market_dashboard.valuation_series import valuation_series
from services.market_dashboard.financial_dates import publication_date
from services.market_dashboard.performance import performance
from services.market_dashboard.xbrl_facts import parse_instance, parse_xbrl_zip
from services.market_dashboard.store import Store
from services.market_dashboard.fact_store import FactStore
from services.market_dashboard.analysis import trailing_twelve_months


TRACE = {"source": "fixture_non_production", "filing_id": "20260301000001"}


def price(day, close=100):
    return {"date": day, "open": close-1, "high": close+2, "low": close-2, "close": close, "volume": 10, "confirmed": True}


def fact(**patch):
    return {"name": "EPS", "value": 10, "basis": "CFS", "frequency": "annual", "period_start": "2024-01-01",
        "period_end": "2024-12-31", "period_verified": True, "security_code": "005930", "currency": "KRW",
        "share_class_verified": True, "per_share_basis": "fixture-aligned", "published_at": "2025-03-14",
        "source_trace": TRACE, **patch}


def prices(rows):
    return {"rows": rows, "venue": "KRX", "per_share_basis": "fixture-aligned"}


def test_whole_week_before_clipping_and_warmup():
    rows = [price((date(2025,1,1)+timedelta(days=i)).isoformat(), 100+i) for i in range(60)]
    result = aggregate_bars(rows, interval="week", start="2025-01-16")
    first = result["rows"][0]
    assert first["period_start"] == "2025-01-13" and first["open"] == 111
    daily = aggregate_bars(rows, start="2025-02-01")
    assert daily["rows"][0]["ma20"] == 121.5
    assert len(daily["warmup"]) == 20


def test_missing_ohlc_does_not_manufacture_candle():
    result = aggregate_bars([price("2025-01-06"), {"date":"2025-01-07", "close":104}], interval="week")
    assert result["rows"][0]["close"] == 104
    assert result["rows"][0]["high"] is None and result["rows"][0]["volume"] is None
    assert result["availability"]["candles"] is False


def test_receipt_is_not_publication_and_date_only_waits_for_next_session():
    assert publication_date({"published_at":"20260515000001"}) == (None, False)
    values = prices([price(d) for d in ["2025-03-13", "2025-03-14", "2025-03-17", "2025-03-18"]])
    result = valuation_series([fact()], values, code="005930", interval="day")
    assert [p["reference_price"] for p in result["series"]] == [None,None,150,150]
    result = valuation_series([fact(published_at="2025-03-14T16:00:00+09:00")], values, code="005930", interval="day")
    assert result["series"][1]["reference_price"] is None


def test_corrections_are_not_known_early_and_losses_break_line():
    values = prices([price(d) for d in ["2025-03-14","2025-03-17","2025-03-18","2025-03-19"]])
    facts = [fact(),fact(value=-2,published_at="2025-03-18",filing_id="correction")]
    result = valuation_series(facts, values, code="005930", interval="day")
    assert [p["reference_price"] for p in result["series"]] == [None,150,150,None]
    assert result["series"][-1]["metric_value"] == -2


@pytest.mark.parametrize("change", [{"security_code":"005935"},{"per_share_basis":"different"},{"period_verified":False},{"published_at":None}])
def test_incompatible_facts_remain_in_table_without_comparison(change):
    result = valuation_series([fact(**change)], prices([price("2025-03-17")]), code="005930")
    assert result["status"] == "price_only" and not result["availability"]["comparison"]
    assert result["periods"][0]["value"] == 10


def test_quarter_is_not_used_as_annual_eps_and_partial_month_not_counted():
    values=prices([price(d) for d in ["2025-03-17","2025-03-31","2025-04-14"]])
    invalid=valuation_series([fact(period_start="2024-10-01")],values,code="005930")
    assert invalid["status"]=="price_only"
    result=valuation_series([fact()],values,code="005930")
    assert result["summary"]["monthly_observations"]==1


def test_binary_raw_and_versioned_facts(tmp_path):
    store = Store(url="sqlite://", root=tmp_path)
    raw = b"PK\x00\xff\xfe\x01"
    digest = store.raw("test", "https://example.test/report", raw)
    assert (tmp_path/"raw"/"test"/(digest+".bin")).read_bytes() == raw
    assert json.loads((tmp_path/"raw"/"test"/(digest+".json")).read_text(encoding="utf-8"))["payload"] is None
    db = FactStore(store)
    f = {**fact(),"account_id":"EPS","value":"10.123456789123456789"}
    assert db.append("corp", [f,f]) == 1
    assert db.append("corp", [{**f,"value":"11.1","filing_id":"correction"}]) == 1
    assert len(db.read("corp")) == 2
    assert db.read("corp")[0]["value"] == "10.123456789123456789"


def test_large_replayed_filing_is_atomic_and_corrections_remain_separate(tmp_path):
    db=FactStore(Store(url="sqlite://",root=tmp_path))
    rows=[{**fact(),"account_id":f"account-{i}","value":str(i)} for i in range(235)]
    assert db.append("corp",rows+rows)==235
    assert db.append("corp",rows)==0
    corrected={**rows[0],"filing_id":"correction","value":"999"}
    assert db.append("corp",[corrected])==1
    assert len(db.read("corp"))==236


def test_xbrl_preserves_dimensions_and_does_not_sum_per_share():
    xml = b'''<xbrl xmlns="http://www.xbrl.org/2003/instance" xmlns:i="http://ifrs.example/2025" xmlns:d="http://xbrl.org/2006/xbrldi" xmlns:iso="http://www.xbrl.org/2003/iso4217">
    <context id="a"><entity><identifier scheme="test">corp</identifier><segment><d:explicitMember dimension="i:SegmentsAxis">i:MobileMember</d:explicitMember></segment></entity><period><startDate>2025-01-01</startDate><endDate>2025-03-31</endDate></period></context>
    <unit id="won"><measure>iso:KRW</measure></unit><unit id="eps"><divide><unitNumerator><measure>iso:KRW</measure></unitNumerator><unitDenominator><measure>shares</measure></unitDenominator></divide></unit>
    <i:Revenue contextRef="a" unitRef="won" decimals="0">123456789123456789</i:Revenue><i:BasicEarningsLossPerShare contextRef="a" unitRef="eps">10.5</i:BasicEarningsLossPerShare></xbrl>'''
    facts = parse_instance(xml, filing_id="20250314000001", source_trace=TRACE, basis="CFS", publication="2025-03-14")
    assert facts[0]["period_start"] == "2025-01-01" and facts[0]["value"] == "123456789123456789"
    assert "MobileMember" in str(facts[0]["dimensions"])
    assert facts[1]["kind"] == "per_share"
    content = io.BytesIO()
    with zipfile.ZipFile(content,"w") as z:
        z.writestr("../../instance.xbrl",xml)
    assert len(parse_xbrl_zip(content.getvalue(), filing_id="20250314000001", source_trace=TRACE)) == 2
    with pytest.raises(ValueError):
        parse_instance(b'<!DOCTYPE x [<!ENTITY a "test">]><x/>', filing_id="x", source_trace=TRACE)


def test_ttm_never_joins_different_entities_or_segments():
    dates = [("2025-01-01","2025-03-31"),("2025-04-01","2025-06-30"),("2025-07-01","2025-09-30"),("2025-10-01","2025-12-31")]
    facts = [{"account_id":"cashflow","name":"영업현금흐름","company_id":"a" if i<2 else "b","value":10,"basis":"CFS","unit":"KRW","currency":"KRW","kind":"duration","period_verified":True,"period_start":s,"period_end":e,"source_trace":TRACE} for i,(s,e) in enumerate(dates)]
    assert not trailing_twelve_months(facts)["rows"]
    for i,f in enumerate(facts): f.update(company_id="a", dimensions={"segment":"mobile" if i<2 else "display"})
    assert not trailing_twelve_months(facts)["rows"]


def test_total_return_split_and_reinvestment_strictly_after_payment():
    snapshot = {"adjustment":"raw","venue":"KRX","rows":[price("2025-01-02",100),price("2025-01-03",50),price("2025-01-06",50),price("2025-01-07",50),price("2025-01-08",55)]}
    events = {"venue":"KRX","coverage":{"start":"2025-01-01","end":"2025-12-31","verified":True},"rows":[
        {"id":"s","security_code":"005930","verified":True,"source_trace":TRACE,"kind":"split","effective_date":"2025-01-03","ratio":2},
        {"id":"d","security_code":"005930","verified":True,"source_trace":TRACE,"kind":"dividend","ex_date":"2025-01-06","payment_date":"2025-01-07","payment_status":"confirmed","amount":5}]}
    result = performance(snapshot,events,code="005930")
    assert result["price_return"] == 10
    assert result["total_return"] == 20
    assert next(e for e in result["ledger"] if e["kind"]=="reinvest")["date"] == "2025-01-08"
    assert result["cagr"] is None
    assert performance({**snapshot,"adjustment":"total_return"},events,code="005930")["status"] == "raw_price_basis_required"
    events['rows'][1]['payment_status']='announced'
    assert performance(snapshot,events,code='005930')['status']=='payment_confirmation_required'


def test_new_endpoints_etag_and_no_fabricated_data(monkeypatch,tmp_path):
    from fastapi.testclient import TestClient
    from services.market_dashboard import app as module
    store=Store(url="sqlite://",root=tmp_path)
    store.put("universe",{"rows":[{"code":"005930","name":"fixture_non_production","market":"KOSPI"}]})
    store.put("prices:005930",prices([price("2025-03-17")]))
    store.put("detail:005930",{"financials":[fact()]})
    monkeypatch.setattr(module,"get_store",lambda:store)
    client=TestClient(module.app)
    path="/api/market/v1/securities/005930/analysis/valuation"
    first=client.get(path)
    assert first.status_code==200 and first.json()["availability"]["comparison"]
    assert client.get(path,headers={"If-None-Match":first.headers["etag"]}).status_code==304
    assert client.get(path+"?multiple=nan").status_code==422
    assert client.get("/api/market/v1/securities/005930/bars?interval=5min").json()["rows"]==[]
    assert client.get("/api/market/v1/securities/005930/events").json()["coverage"]["verified"] is False


def test_dividend_total_return_route_is_not_published_and_bars_prioritize_history(monkeypatch,tmp_path):
    from fastapi.testclient import TestClient
    from services.market_dashboard import app as module
    store=Store(url="sqlite://",root=tmp_path)
    store.put("universe",{"rows":[{"code":"005930"}]})
    monkeypatch.setattr(module,"get_store",lambda:store)
    monkeypatch.setattr(module,"history_via_openalgo",lambda *args:None)
    client=TestClient(module.app)
    base="/api/market/v1/securities/"
    # The removed total-return path must be absent even for a listed security.
    assert client.get(base+"005930/analysis/performance").status_code==404
    openapi_paths=client.get("/openapi.json").json()["paths"]
    assert not any(path.endswith("/analysis/performance") for path in openapi_paths)
    assert not store.list_prefix("trading-history-request:")
    path=base+"005930/bars?interval=day&adjustment=raw&venue=%ED%86%B5%ED%95%A9"
    assert client.get(path).status_code==200
    key="trading-history-request:005930:raw-history"
    assert store.get(key)["kind"]=="raw-history"
