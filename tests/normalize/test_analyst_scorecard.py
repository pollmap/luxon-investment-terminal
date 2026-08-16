from packages.valuation.analyst_scorecard import build_analyst_scorecard

TRACE = {
    "source_document_id": "consensus-doc",
    "source_type": "user_consensus_csv",
    "filing_id": "consensus-import",
    "period": "FY2024",
    "available_at": "2024-11-01T12:00:00+00:00",
    "unit": "per_share",
    "currency": "USD",
    "method": "user_consensus_csv",
    "formula": "point-in-time consensus snapshot",
    "quality_status": "source_backed",
}


def test_analyst_scorecard_preserves_hit_rates_and_trace():
    scorecard = build_analyst_scorecard(
        "AAPL",
        {
            "scorecard": {
                "status": "source_backed_consensus_snapshots",
                "rows": [
                    {
                        "fiscal_year": 2024,
                        "actual_eps": "10.00",
                        "estimate_1y_prior": "10.50",
                        "estimate_2y_prior": "14.00",
                        "error_1y_pct": "5.00",
                        "error_2y_pct": "40.00",
                        "result_1y": "hit",
                        "result_2y": "miss",
                        "quality_status": "source_backed",
                        "source_trace": TRACE,
                    }
                ],
                "summary": {
                    "hit_rate_1y_pct": "100.00",
                    "hit_rate_2y_pct": "0.00",
                    "required_source": "point_in_time_consensus_snapshots",
                },
            },
            "source_trace": TRACE,
        },
        currency="USD",
        data_mode="source_backed",
    )

    assert scorecard["rows"][0]["source_trace"]["source_type"] == "analyst_scorecard_derived"
    assert scorecard["summary"]["hit_rate_1y_pct"] == "100.00"
    assert scorecard["summary"]["hit_rate_2y_pct"] == "0.00"
    assert scorecard["quality_status"] == "source_backed_derived"


def test_analyst_scorecard_pending_without_overlap():
    scorecard = build_analyst_scorecard(
        "AAPL",
        {"scorecard": {"status": "pending_actual_overlap", "rows": [], "summary": {}}},
        currency="USD",
        data_mode="source_backed",
    )

    assert scorecard["rows"] == []
    assert "pending_actual_overlap" in scorecard["flags"]
    assert scorecard["quality_status"] == "source_backed_partial"
