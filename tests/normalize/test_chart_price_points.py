from packages.valuation.chart import render_valuation_svg
from services.api.chart_cache import _chart_visibility, valuation_chart_cache_key


SERIES = [
    {
        "fiscal_year": 2022,
        "metric": "6.00",
        "price": "130.00",
        "fair_value_price": "108.00",
        "normal_multiple": "20.0",
        "dividend": "0.90",
        "forecast_flag": False,
    },
    {
        "fiscal_year": 2023,
        "metric": "6.50",
        "price": "170.00",
        "fair_value_price": "117.00",
        "normal_multiple": "20.0",
        "dividend": "0.96",
        "forecast_flag": False,
    },
    {
        "fiscal_year": 2024,
        "metric": "7.00",
        "price": "190.00",
        "fair_value_price": "126.00",
        "normal_multiple": "20.0",
        "dividend": "1.00",
        "forecast_flag": False,
    },
]

SERIES_WITH_TRACE = [
    row
    | {
        "source_trace": {
            "source": "SEC_COMPANYFACTS",
            "source_type": "sec_companyfacts",
            "source_document_id": f"aapl-{row['fiscal_year']}-eps",
            "filing_id": f"0000320193-{row['fiscal_year']}",
            "period": f"FY{row['fiscal_year']}",
            "available_at": f"{row['fiscal_year'] + 1}-01-31T00:00:00+00:00",
            "unit": "per_share",
            "currency": "USD",
            "method": "S1_SEC_RECONCILIATION",
            "formula": "fixture-backed chart source trace test input",
            "quality_status": "passed",
        }
    }
    for row in SERIES
]


def test_chart_renderer_prefers_dated_price_points():
    svg = render_valuation_svg(
        SERIES,
        {
            "price": True,
            "price_points": [
                {"date": "2022-01-31", "fiscal_year": 2022, "close_price": "128.50"},
                {"date": "2022-02-28", "fiscal_year": 2022, "close_price": "140.00"},
                {"date": "2023-01-31", "fiscal_year": 2023, "close_price": "166.00"},
                {"date": "2026-01-31", "fiscal_year": 2026, "close_price": "999.00"},
            ],
        },
    )

    assert "Price (3 dated points)" in svg


def test_chart_renderer_embeds_source_trace_caption():
    svg = render_valuation_svg(
        SERIES_WITH_TRACE,
        {
            "metric": "adjusted_operating",
            "metric_label": "Adjusted Operating EPS",
            "data_mode": "source_backed",
            "data_backend": "postgres",
        },
    )

    assert "Source trace:" in svg
    assert "metric=Adjusted Operating EPS" in svg
    assert "methods=S1_SEC_RECONCILIATION" in svg
    assert "sources=SEC_COMPANYFACTS" in svg
    assert "docs=3" in svg
    assert "Quality: passed" in svg


def test_chart_cache_key_tracks_price_points():
    base_payload = {
        "data": SERIES,
        "meta": {
            "metric": "adjusted_operating",
            "line_visibility": {"price": True},
            "data_backend": "fixture",
            "price_points": [
                {"date": "2022-12-31", "fiscal_year": 2022, "close_price": "130.00"}
            ],
            "price_points_meta": {"frequency": "annual_fixture"},
        },
    }
    changed_payload = {
        **base_payload,
        "meta": {
            **base_payload["meta"],
            "price_points": [
                {"date": "2022-12-31", "fiscal_year": 2022, "close_price": "131.00"}
            ],
        },
    }

    assert (
        valuation_chart_cache_key("AAPL", base_payload, "svg")
        != valuation_chart_cache_key("AAPL", changed_payload, "svg")
    )
    visibility = _chart_visibility(base_payload)
    assert visibility["price_points"][0]["close_price"] == "130.00"
    assert visibility["price_points_meta"]["frequency"] == "annual_fixture"
