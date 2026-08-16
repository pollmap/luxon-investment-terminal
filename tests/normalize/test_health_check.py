from decimal import Decimal

from packages.valuation.fiscal_fitness import build_fiscal_fitness_rows
from packages.valuation.health_check import build_health_check_score

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

FORECAST_EVIDENCE = {
    "source_trace": TRACE,
    "scorecard": {
        "status": "source_backed_scorecard",
        "summary": {
            "hit_rate_1y_pct": "80",
            "hit_rate_2y_pct": "60",
        },
    },
    "sentiment": {
        "net_revision_score_pct": "10",
        "quality_status": "source_backed",
    },
}


def test_health_check_scores_latest_fiscal_fitness_rows():
    fiscal_rows = build_fiscal_fitness_rows(
        "AAPL",
        [
            {
                "fiscal_year": 2023,
                "revenue": "100",
                "eps": "5",
                "fcf": "20",
                "gross_margin": "40",
                "operating_margin": "20",
                "net_margin": "12",
                "roe": "18",
                "roic": "13",
                "debt_to_equity": "0.5",
                "source_trace": TRACE,
            },
            {
                "fiscal_year": 2024,
                "revenue": "125",
                "eps": "6",
                "fcf": "25",
                "gross_margin": "45",
                "operating_margin": "22",
                "net_margin": "14",
                "roe": "20",
                "roic": "15",
                "debt_to_equity": "0.4",
                "source_trace": TRACE,
            },
        ],
        currency="USD",
        data_mode="source_backed",
    )

    score = build_health_check_score(
        "AAPL",
        fiscal_rows,
        currency="USD",
        data_mode="source_backed",
        forecast_evidence=FORECAST_EVIDENCE,
    )

    assert score["fiscal_year"] == 2024
    assert score["overall_score"] > Decimal("50")
    assert {axis["axis_key"] for axis in score["axes"]} == {
        "profitability",
        "cash_generation",
        "financial_strength",
        "growth",
        "predictability",
    }
    assert score["source_trace"]["source_type"] == "health_check_derived"
    assert score["source_trace"]["quality_status"] in {
        "source_backed_derived",
        "source_backed_partial",
    }


def test_health_check_keeps_predictability_transparent_when_missing():
    fiscal_rows = build_fiscal_fitness_rows(
        "AAPL",
        [{"fiscal_year": 2024, "roe": "20", "source_trace": TRACE}],
        currency="USD",
        data_mode="source_backed",
    )

    score = build_health_check_score(
        "AAPL",
        fiscal_rows,
        currency="USD",
        data_mode="source_backed",
        forecast_evidence=None,
    )

    predictability = next(axis for axis in score["axes"] if axis["axis_key"] == "predictability")
    assert predictability["score"] == Decimal("50.00")
    assert "predictability_requires_point_in_time_consensus_snapshots" in predictability["flags"]
    assert score["quality_status"] == "source_backed_partial"


def test_health_check_fixture_mode_is_explicit():
    fiscal_rows = build_fiscal_fitness_rows(
        "AAPL",
        [{"fiscal_year": 2024, "roe": "20", "source_trace": TRACE}],
        currency="USD",
        data_mode="fixture_non_production",
    )

    score = build_health_check_score(
        "AAPL",
        fiscal_rows,
        currency="USD",
        data_mode="fixture_non_production",
        forecast_evidence=FORECAST_EVIDENCE,
    )

    assert score["quality_status"] == "fixture_non_production_health_check"
    assert all(
        axis["quality_status"] == "fixture_non_production_health_check"
        for axis in score["axes"]
    )
