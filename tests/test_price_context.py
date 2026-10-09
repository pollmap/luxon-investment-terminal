"""fixture_non_production: domestic sessions, price units and incomplete history."""

import pytest
from fastapi.testclient import TestClient

from services.market_dashboard.price_context import price_context
from services.market_dashboard.response_cache import RevisionResponseCache
from services.market_dashboard.store import Store


def snapshot(*rows, **extra):
    return {
        "rows": [{"date": d, "close": p} for d, p in rows],
        "venue": "KRX",
        "adjustment": "raw",
        "source_trace": {"source": "fixture_non_production"},
        **extra,
    }


def calculate(data, **kwargs):
    return price_context(data, code="005930", **kwargs)


def test_drawdown_uses_prior_peak_and_preserves_actual_sessions():
    data = snapshot(
        ("2025-01-31", 100), ("2025-02-03", 120), ("2025-02-04", 90), ("2025-02-05", 108)
    )
    result = calculate(data)
    assert [p["drawdown_pct"] for p in result["series"]] == [0, 0, -25, -10]
    assert result["summary"]["return_pct"] == 8
    assert result["summary"]["peak_date"] == "2025-02-03"
    assert len(result["series"]) == 4  # No invented Saturday/Sunday observations.
    assert result["months"][-1]["return_pct"] == 8
    assert result["months"][-1]["as_of_month"] is True
    assert result["availability"]["calendar_verified"] is False


def test_month_ends_use_friday_not_calendar_last_day_and_missing_month_is_null():
    result = calculate(snapshot(("2025-04-30", 100), ("2025-05-30", 110), ("2025-07-31", 121)))
    assert [(r["month"], r["return_pct"]) for r in result["months"]] == [
        ("2025-04", None),
        ("2025-05", 10),
        ("2025-06", None),
        ("2025-07", None),
    ]
    assert result["months"][1]["end"] == "2025-05-30"
    assert result["quality"]["long_gaps"]


def test_selected_period_resets_peak_but_month_uses_previous_month_baseline():
    result = calculate(
        snapshot(("2024-12-30", 1000), ("2025-01-31", 100), ("2025-02-03", 80), ("2025-02-28", 90)),
        start="2025-02-01",
    )
    assert result["series"][0]["drawdown_pct"] == 0
    assert result["summary"]["return_pct"] == 12.5
    assert result["months"][0]["return_pct"] == -10
    assert result["months"][0]["start"] == "2025-01-31"


def test_future_and_unconfirmed_values_never_influence_context():
    data = snapshot(("2025-02-03", 100), ("2025-02-04", 120), ("2025-02-05", 1000))
    data["rows"][1]["confirmed"] = False
    result = calculate(data, end="2025-02-04")
    assert result["summary"]["end"] == "2025-02-03"
    assert result["summary"]["observations"] == 1
    assert result["summary"]["return_pct"] == 0


@pytest.mark.parametrize("close", [0, -10, None, float("nan"), float("inf"), True, "bad"])
def test_bad_prices_are_not_zero_returns(close):
    result = calculate(snapshot(("2025-02-03", close)))
    assert result["series"] == [] and result["status"] == "not_collected"


def test_conflicting_duplicate_or_market_basis_is_rejected():
    data = snapshot(("2025-02-03", 100), ("2025-02-03", 110))
    assert calculate(data)["status"] == "incompatible_data"
    data["rows"][1] = {"date": "2025-02-04", "close": 110, "venue": "NXT"}
    assert calculate(data)["series"] == []
    assert calculate(snapshot(("2025-02-03", 100), adjustment="total_return"))["series"] == []


def test_leap_year_selection_does_not_crash_and_results_are_deterministic():
    data = snapshot(("2023-02-28", 100), ("2024-02-29", 110))
    assert calculate(data) == calculate(data)
    assert calculate(data)["summary"]["observations"] == 2
    with pytest.raises(ValueError):
        calculate(data, years=2)
    with pytest.raises(ValueError):
        calculate(data, start="2025-02-30")
    with pytest.raises(ValueError):
        calculate(data, start="2025-01-01", end="2024-01-01")


def test_endpoint_is_revision_cached_and_unknown_stock_is_not_an_empty_success(
    tmp_path, monkeypatch
):
    from services.market_dashboard import app as module

    store = Store(url="sqlite://", root=tmp_path)
    store.put("universe", {"rows": [{"code": "005930"}]})
    store.put("prices:005930", snapshot(("2025-02-03", 100), ("2025-02-04", 110)))
    monkeypatch.setattr(module, "get_store", lambda: store)
    monkeypatch.setattr(module, "_detail_responses", RevisionResponseCache())
    client = TestClient(module.app)
    url = "/api/market/v1/securities/005930/analysis/price-context"
    first = client.get(url)
    assert first.json()["summary"]["return_pct"] == 10
    assert client.get(url, headers={"If-None-Match": first.headers["etag"]}).status_code == 304
    store.put("prices:005930", snapshot(("2025-02-03", 100), ("2025-02-04", 80)))
    assert client.get(url).json()["summary"]["return_pct"] == -20
    assert client.get(url + "?years=2").status_code == 422
    assert client.get(url.replace("005930", "000000")).status_code == 404
