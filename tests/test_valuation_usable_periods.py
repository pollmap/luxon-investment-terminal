"""fixture_non_production: usable dates must agree with automatic frequency selection."""

from copy import deepcopy

import pytest

from services.market_dashboard.analysis_inputs import metric_availability
from services.market_dashboard.valuation_series import valuation_series


def facts():
    shared = {
        "metric": "EPS", "basis": "CFS", "currency": "KRW", "security_code": "000270",
        "share_class_verified": True, "period_verified": True, "per_share_basis": "reviewed",
        "source_trace": {"source": "fixture_non_production"},
    }
    return [
        {**shared, "frequency": "annual", "period_start": "2025-01-01",
         "period_end": "2025-12-31", "published_at": "2026-03-12", "value": 10},
        {**shared, "frequency": "ttm", "period_start": "2025-07-01",
         "period_end": "2026-06-30", "published_at": "2026-09-16", "value": 12},
    ]


def prices(*days, **patch):
    return {
        "venue": "통합", "per_share_basis": "reviewed",
        "per_share_basis_start": "2025-01-01", "per_share_basis_end": "2026-06-30",
        "rows": [{"date": day, "close": 100} for day in
                 (days or ("2026-03-13", "2026-06-30", "2026-09-17", "2026-10-08"))],
        **patch,
    }


def chart(source=None, price=None, **options):
    return valuation_series(
        source if source is not None else facts(), price if price is not None else prices(),
        code="000270", venue="통합", interval="day", **options,
    )


def metric(source=None, price=None, metric_code="EPS"):
    return next(m for m in metric_availability(
        source if source is not None else facts(), price if price is not None else prices(),
        "000270",
    )["metrics"] if m["code"] == metric_code)


@pytest.mark.parametrize("metric_code", ["EPS", "OCF", "EBITDA"])
def test_unusable_ttm_falls_back_to_verified_annual_without_mutating_evidence(metric_code):
    source, price = facts(), prices()
    for row in source:
        row["metric"] = metric_code
    before = deepcopy((source, price))
    result = chart(source, price, metric=metric_code, multiple=10)
    assert result["frequency"] == "annual"
    assert result["availability"]["comparison"] is True
    assert [r["metric_value"] for r in result["series"]] == [10, 10, None, None]
    assert (source, price) == before
    menu = metric(source, price, metric_code)
    assert menu["comparison_frequencies"] == ["annual"]
    assert menu["comparison_end"] == "2025-12-31"


def test_explicit_ttm_keeps_requested_frequency_and_blocked_reasons():
    result = chart(frequency="ttm")
    assert result["frequency"] == "ttm"
    assert result["availability"] == {
        "prices": True, "comparison": False, "reasons": ["no_available_fact"],
        "forecast": False, "dividends": False,
    }
    assert result["periods"][0]["value"] == 12


@pytest.mark.parametrize("value", [12, 0, -12])
def test_usable_ttm_stays_preferred_including_zero_and_losses(value):
    source = facts()
    source[-1]["value"] = value
    result = chart(source, prices(per_share_basis_end="2026-10-08"))
    assert result["frequency"] == "ttm"
    assert result["series"][-1]["metric_value"] == value
    assert result["series"][-1]["comparison_eligible"] is (value > 0)
    if value <= 0:
        assert result["series"][-1]["reason"] == "non_positive_metric"


def test_latest_mode_does_not_apply_point_in_time_publication_gate():
    result = chart(mode="latest")
    assert result["frequency"] == "ttm"
    assert result["series"][1]["metric_value"] == 12
    assert result["series"][2]["metric_value"] is None  # Unit interval still applies.


@pytest.mark.parametrize("publication,close,expected", [
    ("2026-06-30", "2026-06-30T15:30:00+09:00", "annual"),
    ("2026-06-30T15:00:00+09:00", "2026-06-30T15:30:00+09:00", "ttm"),
    ("2026-06-30T15:30:00+09:00", "2026-06-30T15:30:00+09:00", "annual"),
    ("2026-06-30T15:00:00+09:00", None, "annual"),
])
def test_automatic_choice_reuses_date_only_and_actual_session_close_contract(
        publication, close, expected):
    source = facts()
    source[-1]["published_at"] = publication
    sessions = {"2026-06-30": close} if close else {}
    result = chart(source, session_closes=sessions)
    assert result["frequency"] == expected


def test_warmup_outside_selected_window_cannot_unlock_a_ttm():
    source = facts()
    source[-1]["published_at"] = "2026-06-29"
    result = chart(source, start="2026-09-01")
    assert not result["availability"]["comparison"]
    assert all(r["metric_value"] is None for r in result["series"])


@pytest.mark.parametrize("patch", [
    {"security_code": "000275"}, {"share_class_verified": False},
    {"per_share_basis": "another-split"}, {"currency": "USD"},
    {"period_verified": False},
])
def test_fallback_never_loosens_company_class_currency_or_split_gates(patch):
    source = [{**row, **patch} for row in facts()]
    result = chart(source)
    assert not result["availability"]["comparison"]
    assert metric(source)["comparison_frequencies"] == []


@pytest.mark.parametrize("price", [prices(rows=[]), prices(rows=[], row_count=0)])
def test_no_prices_preserves_missing_prices_without_manufacturing_comparison(price):
    result = chart(price=price)
    assert result["status"] == "not_collected"
    menu = metric(price=price)
    assert menu["status"] == "prices_not_collected"
    assert menu["available"] is True and not menu["comparison_available"]


def test_metadata_only_excludes_impossible_ttm_but_does_not_claim_actual_date_verification():
    menu = metric(price=prices(rows=[], row_count=100,
                              first_time="2025-01-02", last_time="2026-10-08"))
    assert menu["available"] is True and menu["comparison_available"] is True
    assert menu["comparison_frequencies"] == ["annual"]
    assert menu["comparison_dates_verified"] is False
    assert menu["status"] == "dates_unverified"


def test_metadata_without_bounds_remains_accessible_but_unverified():
    menu = metric(price={"venue": "통합", "per_share_basis": "reviewed", "row_count": 100})
    assert menu["available"] is True and menu["comparison_available"] is True
    assert menu["comparison_frequencies"] == ["annual", "ttm"]
    assert menu["comparison_dates_verified"] is False
    assert menu["status"] == "dates_unverified"


def test_actual_price_dates_must_exist_inside_the_reviewed_interval():
    price = prices("2024-12-31", "2026-09-17")
    assert not chart(price=price)["availability"]["comparison"]
    menu = metric(price=price)
    assert menu["comparison_frequencies"] == []
    assert menu["status"] == "comparison_dates_unavailable"


def test_real_correction_to_negative_ttm_is_not_replaced_by_positive_annual():
    source = facts()
    source[-1]["published_at"] = "2026-06-29"
    source.append({**source[-1], "value": -5, "published_at": "2026-06-30",
                   "filing_id": "correction"})
    result = chart(source, prices("2026-06-30", "2026-07-01",
                                 per_share_basis_end="2026-07-01"))
    assert result["frequency"] == "ttm"
    assert [r["metric_value"] for r in result["series"]] == [12, -5]


def test_valid_two_point_ttm_keeps_auto_and_explicit_results_identical():
    price = prices(per_share_basis_end="2026-10-08")
    auto, explicit = chart(price=price), chart(price=price, frequency="ttm")
    assert auto["series"] == explicit["series"]
    assert auto["frequency"] == "ttm"
    assert sum(r["comparison_eligible"] for r in auto["series"]) == 2


@pytest.mark.parametrize("published,possible", [
    ("2026-06-30", False),
    ("2026-06-30T15:00:00+09:00", True),
    ("2026-07-01T00:00:00+09:00", False),
])
def test_metadata_only_rejects_proven_impossibility_not_unknown_session_hours(
        published, possible):
    ttm = {**facts()[-1], "published_at": published}
    menu = metric([ttm], prices(rows=[], row_count=100))
    assert menu["comparison_available"] is possible
    assert menu["comparison_dates_verified"] is False
    assert menu["available"] is True
    assert menu["status"] == ("dates_unverified" if possible else "comparison_dates_unavailable")


def test_missing_or_expired_dates_do_not_certify_a_metric_from_row_count():
    menu = metric(price=prices(rows=None, row_count=100,
                              first_time="2030-01-01", last_time="2030-12-31",
                              per_share_basis_end="2030-12-31"))
    assert not menu["comparison_available"]
    assert menu["status"] == "comparison_dates_unavailable"
    assert menu["available"] is True
