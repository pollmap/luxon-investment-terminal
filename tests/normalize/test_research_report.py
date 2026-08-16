from decimal import Decimal

from packages.valuation.research_report import build_research_report

TRACE = {
    "source_document_id": "doc",
    "source_type": "fixture",
    "filing_id": "filing",
    "period": "FY2024",
    "available_at": "2024-11-01T12:00:00+00:00",
    "unit": "reported",
    "currency": "USD",
    "method": "fixture_non_production",
    "formula": "fixture test trace",
    "quality_status": "passed",
}


def test_research_report_builds_source_traced_sections():
    report = build_research_report(
        "AAPL",
        snapshot={"source_trace": TRACE},
        valuation_rows=[
            {
                "fiscal_year": 2024,
                "price": "120",
                "fair_value_price": "100",
                "normal_multiple": "20",
                "fair_multiple": "15",
                "forecast_flag": False,
                "source_trace": TRACE,
            },
            {
                "fiscal_year": 2029,
                "price": "180",
                "metric": "9",
                "total_return_cagr_pct": "8.5",
                "forecast_flag": True,
                "source_trace": TRACE,
            },
        ],
        financial_rows=[{"fiscal_year": 2024, "source_trace": TRACE}],
        fiscal_fitness_rows=[
            {
                "fiscal_year": 2024,
                "metric_key": "roe_pct",
                "value": "20",
                "unit": "percent",
                "source_trace": TRACE,
                "flags": [],
            }
        ],
        health_check={
            "fiscal_year": 2024,
            "overall_score": "76.00",
            "rating": "healthy",
            "quality_status": "source_backed_derived",
            "flags": [],
            "source_trace": TRACE,
        },
        forecast_evidence={"source_trace": TRACE, "sentiment": {"label": "positive"}},
        use_of_cash_rows=[
            {
                "fiscal_year": 2024,
                "fcf_margin_pct": "25",
                "dividend_payout_pct": "40",
                "debt_to_equity": "0.5",
                "flags": [],
                "source_trace": TRACE,
            }
        ],
        currency="USD",
        data_mode="source_backed",
    )

    assert report["quality_status"] == "source_backed_derived"
    assert {section["section_key"] for section in report["sections"]} == {
        "valuation",
        "quality",
        "forecast",
        "capital_allocation",
        "data_quality",
    }
    valuation_gap = next(
        fact
        for fact in report["audit_facts"]
        if fact["fact_name"] == "research_report.valuation_gap_pct"
    )
    assert valuation_gap["value"] == Decimal("20.00")
    assert report["source_trace"]["source_type"] == "research_report_derived"


def test_research_report_fixture_mode_is_explicit():
    report = build_research_report(
        "AAPL",
        snapshot={"source_trace": TRACE},
        valuation_rows=[],
        financial_rows=[],
        fiscal_fitness_rows=[],
        health_check=None,
        forecast_evidence=None,
        use_of_cash_rows=None,
        currency="USD",
        data_mode="fixture_non_production",
    )

    assert report["quality_status"] == "fixture_non_production_research_report"
    assert "missing_valuation_map" in report["flags"]
    assert "missing_health_check" in report["flags"]


def test_research_report_flags_missing_traces():
    report = build_research_report(
        "AAPL",
        snapshot={},
        valuation_rows=[
            {
                "fiscal_year": 2024,
                "price": "120",
                "fair_value_price": "100",
                "forecast_flag": False,
                "source_trace": {},
            }
        ],
        financial_rows=[],
        fiscal_fitness_rows=[],
        health_check=None,
        forecast_evidence=None,
        use_of_cash_rows=None,
        currency="USD",
        data_mode="source_backed",
    )

    assert report["quality_status"] == "source_backed_warning"
    assert "missing_source_trace" in report["flags"]
    assert all(fact["quality_status"] for fact in report["audit_facts"])
